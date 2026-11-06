"""Public track metadata, independent of audio download availability."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlparse
import requests
from core import parse_link


class MetadataError(ValueError):
    pass


def yandex_reference(url):
    url, source = parse_link(url)
    if source != 'Яндекс Музыка':
        raise MetadataError('Нужна ссылка на трек Яндекс Музыки.')
    match = re.fullmatch(r'/(?:album/(\d+)/)?track/(\d+)/?', urlparse(url).path)
    if not match:
        raise MetadataError('Нужна ссылка на отдельный трек, не альбом или плейлист.')
    album, track = match.groups()
    path = f'/album/{album}/track/{track}' if album else f'/track/{track}'
    return track, 'https://music.yandex.ru' + path


def yandex_record(record, expected_id):
    if not isinstance(record, dict) or str(record.get('id', '')).split(':')[0] != str(expected_id):
        return None
    track = record.get('title')
    if not isinstance(track, str) or not track.strip():
        return None
    version = record.get('version')
    if isinstance(version, str) and version.strip():
        track += ' (' + version.strip() + ')'
    artists = record.get('artists') or []
    artist = ', '.join(a['name'] for a in artists if isinstance(a, dict) and isinstance(a.get('name'), str))
    try:
        duration = max(0, float(record.get('durationMs') or 0)/1000)
    except (ValueError, TypeError):
        duration = 0
    result = dict(title=(artist+' — ' if artist else '')+track.strip(), artist=artist, track=track.strip(), duration=duration)
    cover = record.get('coverUri') or next((a.get('coverUri') for a in record.get('albums', []) if isinstance(a, dict) and a.get('coverUri')), '')
    if isinstance(cover, str) and cover:
        result['cover_url'] = ('https://' + cover.removeprefix('//') if not cover.startswith('http') else cover).replace('%%','640x640')
    return result


class PageData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.blocks, self.current = {}, [], None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            self.meta[attrs.get('property') or attrs.get('name')] = attrs.get('content', '')
        elif tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.current = []
    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)
    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.blocks.append(''.join(self.current))
            self.current = None


def page_metadata(html):
    parser = PageData()
    parser.feed(html)
    def recordings(value):
        if isinstance(value, list):
            for item in value:
                yield from recordings(item)
        elif isinstance(value, dict):
            kind = value.get('@type', [])
            if kind == 'MusicRecording' or isinstance(kind, list) and 'MusicRecording' in kind:
                yield value
            if '@graph' in value:
                yield from recordings(value['@graph'])
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in recordings(data):
            track = item.get('name')
            if not isinstance(track, str) or not track.strip():
                continue
            artists = item.get('byArtist', [])
            if isinstance(artists, dict): artists = [artists]
            if not isinstance(artists, list): artists = []
            artist = ', '.join(a['name'] for a in artists if isinstance(a, dict) and isinstance(a.get('name'), str))
            return dict(title=(artist+' — ' if artist else '')+track, artist=artist, track=track, duration=0)
    title = parser.meta.get('og:title', '').strip()
    # A generic app shell, captcha, or login screen is not song metadata.
    generic = ('яндекс музыка', 'яндекс.музыка', 'yandex music', 'яндекс музыке')
    if title and parser.meta.get('og:type') in ('music.song', 'music.recording') and not title.lower().startswith(generic):
        return dict(title=title, artist='', track=title, duration=0)
    return None


def yandex_metadata(url):
    ident, canonical = yandex_reference(url)
    headers = {'User-Agent': 'TwitchTune/0.7', 'Accept': 'application/json'}
    failure = 'Яндекс не отдал название трека.'
    try:
        response = requests.get('https://api.music.yandex.net/tracks/' + ident, headers=headers, timeout=(5, 8))
        if response.status_code == 200:
            try:
                data = response.json()
                records = data.get('result', []) if isinstance(data, dict) else []
                if isinstance(records, list):
                    for record in records:
                        metadata = yandex_record(record, ident)
                        if metadata:
                            return metadata
            except ValueError:
                pass
        elif response.status_code in (401, 403, 451):
            failure = f'Яндекс ограничил доступ к данным (HTTP {response.status_code}).'
        else:
            failure = f'Яндекс не отдал данные трека (HTTP {response.status_code}).'
    except requests.RequestException:
        failure = 'Не удалось связаться с Яндекс Музыкой.'
    try:
        response = requests.get(canonical, headers={'User-Agent': headers['User-Agent']}, timeout=(5, 8))
        if response.status_code == 200:
            metadata = page_metadata(response.text[:2_000_000])
            if metadata:
                return metadata
    except requests.RequestException:
        pass
    raise MetadataError(failure + ' Впиши исполнителя и название в окне «Найти версию».')


def replacement_query(row):
    artist = str(row.get('artist') or '').strip()
    track = str(row.get('track') or '').strip()
    title = str(row.get('title') or '').strip()
    query = ' '.join(part for part in (artist, track) if part) if track else title
    return '' if re.search(r'https?://', query, re.I) else query
