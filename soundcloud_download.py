"""Resolve SoundCloud share links and retry transient network failures."""
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import requests
from yt_dlp.extractor.soundcloud import SoundcloudIE


class SoundCloudError(RuntimeError):
    pass


def failure_kind(error):
    seen, messages, pending = set(), [], [error]
    while pending and len(seen) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        messages.append(str(current).lower())
        for name in ('cause', '__cause__', '__context__'):
            child = getattr(current, name, None)
            if isinstance(child, BaseException):
                pending.append(child)
        info = getattr(current, 'exc_info', None)
        if info and len(info) > 1 and isinstance(info[1], BaseException):
            pending.append(info[1])
    detail = ' '.join(messages)
    if re.search(r'\b(?:401|403|404|429|451)\b', detail):
        return 'access'
    if any(x in detail for x in ('timed out','timeout','connection reset','connection aborted','connection refused','resolve','network is unreachable','temporarily unavailable')):
        return 'network'
    return 'other'


def resolve_soundcloud(url):
    parsed = urlparse(url)
    if parsed.hostname == 'on.soundcloud.com':
        # A share code is not a /artist/track path. Resolve its redirect first.
        with requests.get(url, stream=True, allow_redirects=True, timeout=(10,45)) as response:
            response.raise_for_status()
            parsed = urlparse(response.url)
    if parsed.scheme not in ('https','http') or parsed.hostname not in ('soundcloud.com','www.soundcloud.com','m.soundcloud.com') or parsed.username or parsed.password:
        raise SoundCloudError('Короткая ссылка SoundCloud не раскрылась в ссылку на трек. Открой её в браузере и скопируй полный адрес soundcloud.com/исполнитель/трек.')
    query = [(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if not k.startswith('utm_') and k != 'si']
    canonical = urlunparse(('https','soundcloud.com',parsed.path,'',urlencode(query),''))
    if not SoundcloudIE.suitable(canonical):
        raise SoundCloudError('Нужна ссылка на отдельный трек SoundCloud. Открой песню в браузере и скопируй полный адрес soundcloud.com/исполнитель/трек.')
    return canonical


def extract_soundcloud(ydl, url, *, download, cancelled=lambda:False, retry_notice=lambda:None):
    for attempt in range(2):
        if cancelled():
            raise SoundCloudError('Загрузка SoundCloud отменена.')
        try:
            canonical = resolve_soundcloud(url)
            if cancelled():
                raise SoundCloudError('Загрузка SoundCloud отменена.')
            return ydl.extract_info(canonical,download=download,ie_key=SoundcloudIE.ie_key())
        except SoundCloudError:
            raise
        except Exception as error:
            if failure_kind(error) == 'network':
                if attempt == 0 and not cancelled():
                    retry_notice()
                    continue
                raise SoundCloudError('SoundCloud не ответил после повторной попытки (ожидание до 45 секунд на запрос). Проверь, открывается ли этот трек в браузере на этом компьютере. Затем нажми «Повторить»; для короткой ссылки попробуй полный адрес трека.') from None
            if failure_kind(error) == 'access':
                raise SoundCloudError('SoundCloud ограничил доступ к треку или числу запросов. Проверь доступность песни в браузере и попробуй позже. Можно выбрать другую запись через «Найти версию».') from None
            raise SoundCloudError('Не удалось загрузить трек SoundCloud. Проверь полную ссылку на песню и обнови приложение через build_exe.bat. Если ошибка повторится, пришли исходную ссылку на трек.') from None
