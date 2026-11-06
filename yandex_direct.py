"""Direct Yandex downloads using the user's own authorized browser session."""
from copy import copy
from pathlib import Path
from mutagen import File as AudioFile
from yt_dlp.cookies import extract_cookies_from_browser, YoutubeDLCookieJar, YDLLogger
from yt_dlp.utils import ExtractorError
from yt_dlp_plugins.extractor.yandex_music_v2 import YandexMusicTrackIE
from source_metadata import yandex_reference
from browser_session import firefox_yandex_cookies, session_read_error


class YandexAccessError(RuntimeError):
    pass


class FullYandexTrackIE(YandexMusicTrackIE):
    IE_NAME = 'twitchtune:yandex'

    def report_warning(self, message, *args, **kwargs):
        if 'smart_preview' in str(message) or 'preview-only' in str(message):
            raise ExtractorError('Яндекс отдал только превью. Проверь вход и подписку в настройках Яндекс Музыки.', expected=True)
        return super().report_warning(message, *args, **kwargs)

    def _warn_if_preview_served(self, track_id, meta, download_info):
        if download_info.get('quality') in ('preview', 'smart_preview'):
            raise ExtractorError('Яндекс отдал только превью. Проверь вход и подписку в настройках Яндекс Музыки.', expected=True)
        super()._warn_if_preview_served(track_id, meta, download_info)


def attach_session(ydl, settings):
    mode = settings.get('yandex_auth', '')
    if mode not in ('firefox', 'chrome', 'edge', 'cookies'):
        raise YandexAccessError('Открой «Настройки → Яндекс Музыка» и выбери источник входа. Для полных записей нужен аккаунт с доступом к ним.')
    try:
        if mode == 'cookies':
            path = Path(settings.get('yandex_cookie_file', ''))
            if not path.is_file():
                raise YandexAccessError('Выбери файл cookies.txt в настройках Яндекс Музыки.')
            jar = YoutubeDLCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=False)
        else:
            profiles = settings.get('yandex_profiles') or {}
            profile = profiles.get(mode, '') if isinstance(profiles, dict) else ''
            try:
                kwargs = dict(logger=YDLLogger(ydl))
                if profile:
                    kwargs['profile'] = profile
                jar = extract_cookies_from_browser(mode, **kwargs)
            except Exception:
                if mode != 'firefox':
                    raise
                jar = firefox_yandex_cookies(profile)
        allowed = []
        for cookie in jar:
            domain = cookie.domain.lstrip('.').lower()
            if (domain == 'yandex.ru' or domain.endswith('.yandex.ru')) and not cookie.is_expired():
                allowed.append(copy(cookie))
        if not any(cookie.name == 'Session_id' for cookie in allowed):
            raise YandexAccessError('Вход Яндекса не найден или истёк. Открой music.yandex.ru в выбранном браузере, войди в аккаунт и повтори загрузку.')
        # Do not retain/export the browser's unrelated cookies or rewrite its file.
        for cookie in allowed:
            ydl.cookiejar.set_cookie(cookie)
    except YandexAccessError:
        raise
    except Exception as exc:
        if mode == 'cookies':
            raise YandexAccessError('Не удалось прочитать файл cookies.txt. Нужен доступный локальный файл формата Netscape с актуальной сессией Яндекса.') from None
        raise YandexAccessError(session_read_error(exc)) from None


def check_session(settings):
    from yt_dlp import YoutubeDL
    class Silent:
        def debug(self,*_): pass
        def warning(self,*_): pass
        def error(self,*_): pass
    with YoutubeDL({'quiet':True,'no_warnings':True,'logger':Silent()}, auto_init=False) as ydl:
        attach_session(ydl,settings)
    return 'Сессия Яндекса найдена. Доступ к песне и подписка проверятся при загрузке.'


def extract_direct(ydl, url, settings):
    _, canonical = yandex_reference(url)
    attach_session(ydl, settings)
    ydl.add_info_extractor(FullYandexTrackIE())
    try:
        return ydl.extract_info(canonical, download=True, ie_key=FullYandexTrackIE.ie_key())
    except Exception as exc:
        # Keep cookies, signed stream URLs and account details out of the GUI log.
        raise YandexAccessError(download_error_message(exc)) from None


def download_error_message(error):
    """Classify nested yt-dlp failures without returning private exception text."""
    messages, statuses, seen = [], set(), set()
    pending = [error]
    while pending and len(seen) < 16:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        messages.append(str(current).lower())
        statuses.add(str(getattr(current, 'status', '') or getattr(current, 'code', '')))
        for name in ('cause', '__cause__', '__context__'):
            nested = getattr(current, name, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
        info = getattr(current, 'exc_info', None)
        if info and len(info) > 1 and isinstance(info[1], BaseException):
            pending.append(info[1])
    detail = ' '.join(messages)
    if any(word in detail for word in ('превью', 'smart_preview', 'preview-only')):
        return 'Яндекс вернул только превью. Проверь вход в аккаунт с подпиской и доступ к этой песне. Затем нажми «Повторить».'
    if '451' in statuses or 'http error 451' in detail:
        return 'Яндекс ограничил доступ к запросу (HTTP 451). Проверь, открывается ли песня на music.yandex.ru из этой же сети и аккаунта.'
    if '401' in statuses or 'http error 401' in detail or 'unauthorized' in detail:
        return 'Яндекс не принял вход (HTTP 401). Войди заново на music.yandex.ru, выбери этот браузер в настройках и нажми «Повторить».'
    if '403' in statuses or 'http error 403' in detail:
        return 'Яндекс запретил загрузку (HTTP 403). Проверь доступ к песне и подписку в выбранном аккаунте; запрос также мог быть отклонён сервисом.'
    if '429' in statuses or 'http error 429' in detail:
        return 'Яндекс временно ограничил число запросов (HTTP 429). Подожди несколько минут и нажми «Повторить».'
    if any(word in detail for word in ('certificate', 'ssl:', 'sslerror')):
        return 'Не удалось проверить сертификат соединения с Яндексом. Проверь дату и время Windows и настройки HTTPS в антивирусе или прокси.'
    if any(word in detail for word in ('timed out', 'timeout', 'connection', 'resolve', 'network is unreachable')):
        return 'Не удалось соединиться с Яндексом. Проверь интернет и доступ к music.yandex.ru, затем нажми «Повторить».'
    if 'not found' in detail or '404' in statuses or 'http error 404' in detail:
        return 'Яндекс не вернул запрошенный трек (HTTP 404 / трек не найден). Открой исходную ссылку в браузере и проверь её.'
    return 'Яндекс не предоставил полный аудиофайл. Проверь выбранный источник входа, подписку и доступ к песне. Если песня играет в браузере, а ошибка остаётся, сообщи об этом — потребуется проверка загрузчика.'


def validate_full_track(path, expected_duration):
    if not expected_duration or expected_duration <= 0:
        raise YandexAccessError('Не удалось проверить полную длительность записи Яндекса. Трек не добавлен в очередь.')
    try:
        audio = AudioFile(str(path))
        # Mutagen FileType is a tag mapping: a valid tagless stream is falsey.
        # Audio validity depends on stream info, never on the presence of tags.
        duration = float(audio.info.length) if audio is not None and getattr(audio, 'info', None) is not None else 0
    except Exception:
        duration = 0
    if not duration:
        raise YandexAccessError('Яндекс не вернул читаемый аудиотрек.')
    if expected_duration and duration + max(5, expected_duration*.05) < expected_duration:
        raise YandexAccessError('Получен только фрагмент песни. Проверь вход и подписку; превью не добавлено в очередь.')
    return duration
