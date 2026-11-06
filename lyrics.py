"""LRCLIB lookup: metadata cleanup, bounded session cache, and polite requests."""
import re
import math
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from difflib import SequenceMatcher
import requests


class LyricsError(RuntimeError):
    pass


def song_query(row):
    title = str(row.get('track') or row.get('title') or '').strip()
    artist = str(row.get('artist') or '').strip()
    if title.startswith(('https://', 'http://')):
        return artist, ''
    # Remove upload labels, but preserve versions such as live/remix/slowed.
    title = re.sub(r'[\[(](?:official\s+(?:music\s+)?(?:video|audio)|official|lyrics?|lyric video|audio|visuali[sz]er|4k|hd|hq)[\])]', '', title, flags=re.I).strip()
    title = re.sub(r'[\[(](?:официальный\s+(?:клип|трек)|премьера(?:\s+клипа)?(?:\s+\d{4})?|prod\.?\s+[^\])]+)[\])]', '', title, flags=re.I).strip()
    title = re.sub(r'\s*[|/]\s*(?:official(?:\s+music)?\s+(?:video|audio)|lyrics?|visuali[sz]er)\s*$', '', title, flags=re.I).strip()
    if not row.get('track'):
        parts = re.split(r'\s+[—–-]\s+', title, maxsplit=1)
        if len(parts) == 2:
            artist = artist or parts[0].strip()
            title = parts[1].strip()
    return artist[:200], title[:300]


def plain_text(record):
    text = record.get('plainLyrics')
    if not isinstance(text, str) or not text.strip():
        text = record.get('syncedLyrics')
    if not isinstance(text, str):
        return ''
    # The popup is a reading window, so LRC timing/metadata tags are hidden.
    lines = []
    for line in text.splitlines():
        line = re.sub(r'\[\d{1,3}:\d{2}(?:[.:]\d+)?\]|<\d{1,3}:\d{2}(?:[.:]\d+)?>', '', line).strip()
        if re.match(r'^\[(?:ar|ti|al|by|offset|length|re|ve):', line, re.I):
            continue
        lines.append(line)
    return '\n'.join(lines).strip()


def normalized(value):
    return re.sub(r'[^\w]+', ' ', str(value).casefold()).strip()


def exact_match(record, artist, title, duration=0):
    if not artist or normalized(record.get('artistName')) != normalized(artist):
        return False
    if normalized(record.get('trackName')) != normalized(title):
        return False
    return not duration or not record.get('duration') or abs(record['duration']-duration) <= 5


class LyricsClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.next_request = 0

    def search(self, artist, title, duration=0, *, force=False, cancelled=lambda: False):
        artist, title = artist.strip(), title.strip()
        if not title:
            raise LyricsError('Укажи название песни; исполнителя можно добавить для точности.')
        key = (artist.casefold(), title.casefold(), round(duration or 0))
        while not self.lock.acquire(timeout=.1):
            if cancelled():
                return []
        try:
            if cancelled():
                return []
            cached = self.cache.get(key)
            if not force and cached and time.monotonic() < cached[0]:
                self.cache.move_to_end(key)
                return cached[1]
            params = {'track_name': title, 'artist_name': artist} if artist else {'q': title}
            data=self._fetch(params,cancelled)
            if not data and artist and not cancelled():
                data=self._fetch({'q':artist+' '+title},cancelled)
            results, seen = [], set()
            for record in data[:20]:
                if not isinstance(record, dict) or not isinstance(record.get('trackName'), str):
                    continue
                if not (plain_text(record) or record.get('instrumental')):
                    continue
                ident = record.get('id')
                if ident is not None and ident in seen:
                    continue
                if ident is not None:
                    seen.add(ident)
                try:
                    record['duration'] = max(0, float(record.get('duration') or 0))
                except (ValueError, TypeError):
                    record['duration'] = 0
                results.append(record)
            def rank(record):
                similarity = SequenceMatcher(None, normalized(title), normalized(record['trackName'])).ratio()
                return (exact_match(record, artist, title, duration), similarity,
                        -abs(record['duration']-duration) if duration else 0)
            results.sort(key=rank, reverse=True)
            self.cache[key] = (time.monotonic()+(300 if results else 60), results)
            self.cache.move_to_end(key)
            while len(self.cache) > 100:
                self.cache.popitem(last=False)
            return results
        finally:
            self.lock.release()

    def _fetch(self,params,cancelled):
        wait = self.next_request-time.monotonic()
        if wait > .6:
            raise LyricsError(f'Сервис просит подождать. Повтори поиск через {max(1, int(wait)+1)} с.')
        if wait > 0:
            time.sleep(wait)
        if cancelled():
            return []
        try:
            response = requests.get('https://lrclib.net/api/search', params=params,
                headers={'User-Agent': 'TwitchTune/0.6 (personal desktop lyrics client)', 'Accept': 'application/json'},
                timeout=(5, 12))
        except requests.RequestException:
            raise LyricsError('Не удалось связаться с базой текстов. Проверь интернет и повтори поиск.') from None
        finally:
            self.next_request = max(self.next_request, time.monotonic()+.3)
        if response.status_code in (429, 503):
            retry = response.headers.get('Retry-After', '30')
            try:
                delay = float(retry)
            except (ValueError, TypeError):
                try:
                    deadline = parsedate_to_datetime(retry)
                    if deadline.tzinfo is None:
                        deadline = deadline.replace(tzinfo=timezone.utc)
                    delay = (deadline-datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = 30
            if not math.isfinite(delay):
                delay = 30
            self.next_request = time.monotonic()+max(1, delay)+.3
            raise LyricsError('База текстов временно занята. Попробуй немного позже.')
        if response.status_code != 200:
            raise LyricsError(f'База текстов недоступна (HTTP {response.status_code}). Попробуй позже.')
        try:
            data = response.json()
        except ValueError:
            raise LyricsError('База текстов вернула неверный ответ. Попробуй позже.') from None
        if not isinstance(data, list):
            raise LyricsError('Не удалось прочитать результаты поиска.')
        return data
