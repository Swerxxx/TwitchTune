"""Remove only cache files owned by this application/request."""
import re
from pathlib import Path
import shutil

AUDIO_EXTENSIONS = {'.mp3','.m4a','.mp4','.aac','.opus','.ogg','.oga','.webm','.weba','.flac','.wav','.mka','.ts'}


def track_folder(default, row):
    if not row.get('album_folder'):
        return Path(default)
    root = (Path(default).parent / 'Альбомы').resolve()
    folder = Path(row['album_folder']).resolve()
    if folder == root or not folder.is_relative_to(root):
        raise ValueError('Недопустимая папка альбома.')
    return folder


def downloaded_audio_path(folder, ident, info, predicted='', completed=()):
    """Use the actual postprocessed path; prepare_filename may describe an old extension."""
    if not re.fullmatch(r'[a-f0-9]{32}', ident):
        return None
    root = Path(folder).resolve()
    candidates = [info.get('filepath'), *reversed(completed)]
    candidates += [x.get('filepath') for x in (info.get('requested_downloads') or []) if isinstance(x, dict)]
    candidates += [info.get('_filename'), predicted]
    def valid(value):
        if not isinstance(value, (str, Path)) or not value:
            return None
        try:
            path = Path(value)
            if not path.is_absolute():
                # yt-dlp may return either a path relative to cwd or just a basename.
                path = path if path.is_file() else root/path
            path = path.resolve()
            if path.parent != root or not path.name.startswith(ident+'.') or path.suffix.lower() not in AUDIO_EXTENSIONS:
                return None
            return path if path.is_file() and path.stat().st_size > 0 else None
        except OSError:
            return None
    for candidate in candidates:
        path = valid(candidate)
        if path:
            return path
    remaining = [path for candidate in root.glob(ident+'.*') if (path := valid(candidate))]
    return remaining[0] if len(remaining) == 1 else None


def remove_track_files(folder, ident, path=''):
    root = Path(folder).resolve()
    if not re.fullmatch(r'[a-f0-9]{32}', ident):
        return []
    candidates = list(root.glob(ident + '.*'))
    if path:
        candidates.append(Path(path))
    failed = []
    for candidate in set(candidates):
        try:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root) or resolved == root:
                continue
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink(missing_ok=True)
        except OSError:
            failed.append(str(candidate))
    return failed
