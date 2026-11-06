"""Album catalog import; individual audio still uses the existing downloader."""
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests
from yt_dlp import YoutubeDL
from core import parse_link
from source_metadata import yandex_record
from spotify_support import EmbedData


def album_reference(text):
    links = re.findall(r'https?://[^\s<>"\x00-\x1f]+', text)
    if len(links) != 1:
        return None
    url = links[0].rstrip('.,!;)')
    p = urlparse(url)
    if p.username or p.password or p.port not in (None, 80, 443):
        return None
    host = (p.hostname or '').lower().removeprefix('www.')
    if re.fullmatch(r'music\.yandex\.(?:ru|com|kz|by|uz)', host) and re.fullmatch(r'/album/\d+/?', p.path):
        return 'Яндекс Музыка', 'https://music.yandex.ru' + p.path.rstrip('/')
    match = re.fullmatch(r'/(?:intl-[a-z]+/)?album/([A-Za-z0-9]+)/?', p.path)
    if host == 'open.spotify.com' and match:
        return 'Spotify', 'https://open.spotify.com/album/' + match[1]
    if host in ('soundcloud.com', 'm.soundcloud.com') and re.fullmatch(r'/[^/]+/sets/[^/]+/?', p.path):
        return 'SoundCloud', 'https://soundcloud.com' + p.path.rstrip('/')
    if host in ('youtube.com', 'music.youtube.com', 'm.youtube.com') and p.path == '/playlist':
        ident = parse_qs(p.query).get('list', [''])[0]
        if re.fullmatch(r'[A-Za-z0-9_-]{5,150}', ident):
            return 'YouTube', 'https://www.youtube.com/playlist?list=' + ident
    return None


def catalog(text, cancelled=lambda: False, settings=None):
    reference = album_reference(text)
    if not reference:
        raise ValueError('Нужна ссылка на альбом Яндекса/Spotify, набор SoundCloud или плейлист YouTube.')
    source, url = reference
    if cancelled():
        raise ValueError('Добавление альбома отменено.')
    if source == 'Яндекс Музыка':
        ident = url.rsplit('/', 1)[-1]
        try:
            response = requests.get('https://api.music.yandex.net/albums/' + ident + '/with-tracks',
                                    headers={'User-Agent': 'TwitchTune/1.0'}, timeout=(5, 15))
            response.raise_for_status()
            album = response.json().get('result') or {}
            if str(album.get('id')) != ident:
                raise ValueError('Яндекс не вернул данные запрошенного альбома.')
        except (requests.RequestException, ValueError, TypeError, AttributeError):
            # Modern extractor reads the current album page and checks that its
            # preloaded list is complete; reuse the configured Yandex session.
            from media import options
            from yandex_direct import attach_session
            from yt_dlp_plugins.extractor.yandex_music_v2 import YandexMusicAlbumIE
            opts=options();opts.update(noplaylist=False,extract_flat=True,skip_download=True,playlistend=101)
            with YoutubeDL(opts) as ydl:
                if settings and settings.get('yandex_auth'):attach_session(ydl,settings)
                ydl.add_info_extractor(YandexMusicAlbumIE())
                info=ydl.extract_info(url,download=False,ie_key=YandexMusicAlbumIE.ie_key())
            if not info or not info.get('entries'):
                raise ValueError('Яндекс не вернул список альбома. Проверь вход в настройках Яндекс Музыки.')
            entries=list(info['entries'])
            if len(entries)>100 or (info.get('playlist_count') and len(entries)!=info['playlist_count']):
                raise ValueError('Яндекс не вернул полный список альбома (максимум 100 треков).')
            tracks=[]
            for entry in entries:
                link=entry.get('url') or entry.get('webpage_url') or ''
                parse_link(link)
                tracks.append(dict(url=link,title=entry.get('title') or link,artist=entry.get('artist') or '',track=entry.get('track') or '',duration=entry.get('duration') or 0))
            if cancelled():raise ValueError('Добавление альбома отменено.')
            return dict(url=url,source=source,title=info.get('title') or 'Альбом '+ident,
                        artist=info.get('artist') or '',year='',tracks=tracks,cover_url=info.get('thumbnail') or '')
        tracks = []
        for volume in album.get('volumes') or []:
            for row in volume:
                record = yandex_record(row, row.get('id'))
                if not record:
                    raise ValueError('Яндекс вернул неполный список треков альбома.')
                record['url'] = url + '/track/' + str(row['id']).split(':')[0]
                tracks.append(record)
        result = dict(title=album.get('title'), artist=', '.join(x['name'] for x in album.get('artists', []) if x.get('name')),
                      year=album.get('year') or '', tracks=tracks,
                      cover_url=('https://' + str(album.get('coverUri') or '').removeprefix('https://').removeprefix('//')).replace('%%','640x640') if album.get('coverUri') else '')
    elif source == 'Spotify':
        ident = url.rsplit('/', 1)[-1]
        response = requests.get('https://open.spotify.com/embed/album/' + ident,
                                headers={'User-Agent': 'Mozilla/5.0 TwitchTune/1.0'}, timeout=(5, 15))
        response.raise_for_status()
        parser = EmbedData(); parser.feed(response.text[:4_000_000])
        try:
            album = json.loads(''.join(parser.chunks))['props']['pageProps']['state']['data']['entity']
            if album.get('uri') != 'spotify:album:' + ident:
                raise ValueError()
            tracks = []
            for row in album.get('trackList') or []:
                match = re.fullmatch(r'spotify:track:([A-Za-z0-9]+)', row.get('uri', ''))
                if not match:
                    raise ValueError()
                tracks.append(dict(url='https://open.spotify.com/track/' + match[1], title=row.get('title') or '',
                                   artist=row.get('subtitle') or '', track=row.get('title') or '',
                                   duration=float(row.get('duration') or 0) / 1000))
            expected = album.get('totalTracks') or album.get('total_tracks') or len(tracks)
            if int(expected) != len(tracks):
                raise ValueError()
            art=album.get('coverArt') if isinstance(album.get('coverArt'),dict) else {}
            identity=album.get('visualIdentity') if isinstance(album.get('visualIdentity'),dict) else {}
            images=art.get('sources') or identity.get('image') or []
            images=images if isinstance(images,list) else []
            cover=next((x.get('url') for x in images if isinstance(x,dict) and x.get('url')), '')
            result = dict(cover_url=cover,title=album.get('name') or album.get('title'), artist=album.get('subtitle') or '', year='', tracks=tracks)
        except (ValueError, KeyError, TypeError):
            raise ValueError('Spotify не отдал список альбома целиком. Попробуй ссылку на этот альбом в другом сервисе.') from None
    else:
        from media import options
        opts = options(soundcloud=source == 'SoundCloud')
        opts.update(noplaylist=False, extract_flat='in_playlist', skip_download=True, playlistend=101)
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info or info.get('_type') not in ('playlist', 'multi_video'):
                raise ValueError('Сервис не вернул список треков альбома.')
            tracks = []
            for row in info.get('entries') or []:
                if cancelled():
                    raise ValueError('Добавление альбома отменено.')
                if not row:
                    raise ValueError('В списке есть недоступная запись. Не удалось прочитать альбом целиком.')
                link = row.get('webpage_url') or row.get('url') or ''
                if source == 'YouTube' and not link.startswith('http'):
                    link = 'https://www.youtube.com/watch?v=' + str(row.get('id') or link)
                parse_link(link)
                tracks.append(dict(url=link, title=row.get('title') or link, artist=row.get('artist') or row.get('uploader') or '',
                                   track=row.get('track') or '', duration=row.get('duration') or 0))
                if len(tracks)>100:
                    break
            result = dict(title=info.get('album') or info.get('title'), artist=info.get('album_artist') or info.get('uploader') or '',
                          year=str(info.get('release_year') or ''), tracks=tracks,cover_url=info.get('thumbnail') or '')
    if cancelled():
        raise ValueError('Добавление альбома отменено.')
    if not result.get('title') or not 1 <= len(result['tracks']) <= 100:
        raise ValueError('Не удалось прочитать альбом: нужно название и от 1 до 100 треков.')
    for track in result['tracks']:
        parse_link(track['url'])
    return dict(result, url=url, source=source)


def safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:80].rstrip(' .')
    return name or 'Без названия'


def create_album(store, root, metadata, viewer='Вручную', **request):
    existing = next((a for a in store.albums() if a['url'] == metadata['url']), None)
    if existing and not request.get('redemption'):
        return existing['id'], []
    label = safe_name(metadata.get('artist') or 'Неизвестный исполнитель') + ' — ' + safe_name(metadata['title'])
    if metadata.get('year'):
        label += ' (' + safe_name(metadata['year']) + ')'
    folder = Path(root) / (label[:120] + ' — ' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    try:
        (folder / 'Об альбоме.txt').write_text(f"{metadata.get('artist','')}\n{metadata['title']}\n{metadata.get('year','')}\n{metadata['url']}\n", encoding='utf-8')
        result = store.add_album(metadata, folder, viewer, **request)
        if not result[1]:
            (folder / 'Об альбоме.txt').unlink(); folder.rmdir()
        return result
    except Exception:
        (folder / 'Об альбоме.txt').unlink(missing_ok=True)
        folder.rmdir()
        raise
