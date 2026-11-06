"""Background cover loading, including albums saved before cover support."""
from pathlib import Path
from albums import catalog
from covers import download_cover, image_url
from track_files import track_folder


def load_album_cover(store, default_folder, row, settings=None, cancelled=lambda: False):
    ident = row['id']
    folder = track_folder(default_folder, row)
    def stopped():
        return cancelled() or not store.get(ident)
    if stopped():
        return ''
    info = dict(row)
    if not image_url(info):
        # Prefer the album's own artwork; track artwork is a fallback for old
        # albums whose catalog/image is no longer available.
        existing = next((r['cover_path'] for r in store.album_tracks(row['album_id'])
                         if r['cover_path'] and Path(r['cover_path']).is_file()), '')
        try:
            data = catalog(row['url'], cancelled=stopped, settings=settings)
            info = {'cover_url': data.get('cover_url') or next((t.get('cover_url') for t in data['tracks'] if t.get('cover_url')), '')}
        except Exception:
            info = {}
        if not image_url(info) and existing and not stopped():
            with store.lock:
                if not stopped():store.update(ident, cover_path=existing)
            return existing
    if stopped() or not image_url(info):
        return ''
    folder.mkdir(parents=True, exist_ok=True)
    path = download_cover(info, folder, ident, cancelled=stopped)
    if not path:
        return ''
    with store.lock:
        if stopped():
            Path(path).unlink(missing_ok=True)
            return ''
        store.update(ident, cover_path=path, cover_url=image_url(info))
    return path
