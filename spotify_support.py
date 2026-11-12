"""Spotify track metadata and conservative matching to a public audio source."""
from limits import MAX_TRACK_SECONDS
import json
import math
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlparse
import requests
from core import parse_link


class SpotifyError(ValueError):
    pass


class EmbedData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks, self.reading = [], False

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == '__NEXT_DATA__':
            self.reading = True

    def handle_data(self, data):
        if self.reading:
            self.chunks.append(data)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.reading = False


def spotify_reference(url):
    url, source = parse_link(url)
    if source != 'Spotify':
        raise SpotifyError('Нужна ссылка на один трек Spotify.')
    ident = urlparse(url).path.rstrip('/').split('/')[-1]
    return ident, 'https://open.spotify.com/track/' + ident


def parse_spotify_embed(html, ident, require_duration=True):
    parser = EmbedData()
    parser.feed(html)
    try:
        entity = json.loads(''.join(parser.chunks))['props']['pageProps']['state']['data']['entity']
        if entity.get('type') != 'track' or entity.get('uri') != 'spotify:track:' + ident:
            raise ValueError('Wrong track')
        title = entity.get('name') or entity.get('title')
        artists = entity.get('artists') or []
        names = [x['name'].strip() for x in artists if isinstance(x, dict) and isinstance(x.get('name'), str) and x['name'].strip()]
        if not isinstance(title, str) or not title.strip() or not names:
            raise ValueError('Missing title/artist')
        duration = float(entity.get('duration') or 0) / 1000
        if not math.isfinite(duration) or duration < 0 or (require_duration and not 0 < duration <= MAX_TRACK_SECONDS):
            raise ValueError('Missing/unsupported duration')
        result = dict(title=', '.join(names)+' — '+title.strip(), artist=', '.join(names),
                      track=title.strip(), duration=duration)
        identity, cover_art = entity.get('visualIdentity'), entity.get('coverArt')
        artwork = (identity.get('image') if isinstance(identity, dict) else None) or (cover_art.get('sources') if isinstance(cover_art, dict) else None) or []
        if not isinstance(artwork, list):
            artwork = []
        artwork = [x for x in artwork if isinstance(x, dict) and isinstance(x.get('url'), str)]
        if artwork:
            result['cover_url'] = max(artwork, key=lambda x: x.get('width') if isinstance(x.get('width'), (int, float)) else 0)['url']
        return result
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SpotifyError('Spotify не отдал исполнителя, название и длительность трека. Попробуй позже или нажми «Найти версию».') from None


def spotify_metadata(url):
    ident, _ = spotify_reference(url)
    try:
        response = requests.get('https://open.spotify.com/embed/track/' + ident,
                                headers={'User-Agent': 'Mozilla/5.0 TwitchTune/0.8'}, timeout=(5, 12))
        response.raise_for_status()
    except requests.RequestException:
        raise SpotifyError('Не удалось получить данные Spotify. Проверь доступ к open.spotify.com и нажми «Повторить».') from None
    return parse_spotify_embed(response.text[:4_000_000], ident)


def spotify_search_metadata(url):
    """Resolve a search label; missing duration must not block manual selection.

    Kept separate from spotify_metadata: automatic downloads still require
    verified artist, title and full-track duration before choosing a result.
    """
    from source_metadata import page_metadata
    ident, canonical = spotify_reference(url)
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.8'}
    failure = 'Spotify не отдал название трека.'
    for endpoint, mode in (('https://open.spotify.com/embed/track/' + ident, 'embed'),
                           (canonical, 'page'),
                           ('https://open.spotify.com/oembed', 'oembed')):
        try:
            options = dict(headers=headers, timeout=(5, 8))
            if mode == 'oembed':
                options['params'] = {'url': canonical}
            response = requests.get(endpoint, **options)
            response.raise_for_status()
            if mode == 'embed':
                try:
                    return parse_spotify_embed(response.text[:4_000_000], ident, require_duration=False)
                except SpotifyError:
                    continue
            if mode == 'page':
                metadata = page_metadata(response.text[:2_000_000])
                if metadata and valid_search_title(metadata.get('track')):
                    return metadata
            else:
                data = response.json()
                title = data.get('title') if isinstance(data, dict) else None
                if valid_search_title(title):
                    # oEmbed may expose only the song title. These results are
                    # displayed for human selection, never matched automatically.
                    return dict(title=title.strip(), track=title.strip(), artist='', duration=0)
        except requests.RequestException as error:
            status = getattr(getattr(error, 'response', None), 'status_code', None)
            failure = f'Spotify вернул HTTP {status}.' if status else 'Не удалось связаться со Spotify.'
        except (ValueError, TypeError):
            continue
    raise SpotifyError(failure + ' Введи исполнителя и название во второе поле и нажми «Искать», либо вставь ссылку YouTube внизу.')


def valid_search_title(value):
    return (isinstance(value, str) and bool(value.strip()) and
            not re.search(r'https?://', value, re.I) and
            value.strip().casefold() not in {'spotify', 'spotify web player', 'spotify – web player',
                                            'spotify - web player', 'login', 'sign in'})


def words(value):
    return set(re.findall(r'\w+', unicodedata.normalize('NFKC', value).casefold()))


VERSIONS = {'live', 'remix', 'cover', 'karaoke', 'instrumental', 'acoustic',
            'slowed', 'sped', 'nightcore', 'ремикс', 'кавер', 'концерт', 'караоке'}
UPLOAD_LABELS = {'official', 'audio', 'video', 'music', 'lyrics', 'lyric', 'visualizer', 'hd', 'hq', '4k'}


def choose_spotify_version(metadata, candidates):
    """Require title, artist, version and duration; never just take result #1."""
    expected_title, expected_artist = words(metadata['track']), words(metadata['artist'])
    expected_versions = (expected_title | expected_artist) & VERSIONS
    duration = metadata['duration']
    matches = []
    for index, candidate in enumerate(candidates):
        title = words(candidate.get('title') or '')
        channel = words(candidate.get('channel') or '')
        seconds = candidate.get('duration') or 0
        url = candidate.get('url') or ''
        parsed = urlparse(url)
        if parsed.hostname not in ('www.youtube.com', 'youtube.com') or parsed.path != '/watch':
            continue
        if not expected_title.issubset(title) or not expected_artist.issubset(title | channel):
            continue
        if not title.issubset(expected_title | expected_artist | UPLOAD_LABELS):
            continue
        if title & VERSIONS != expected_versions:
            continue
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0 or abs(seconds-duration) > max(4, duration*.03):
            continue
        matches.append((abs(seconds-duration), index, candidate))
    return min(matches, key=lambda x: (x[0], x[1]))[2] if matches else None
