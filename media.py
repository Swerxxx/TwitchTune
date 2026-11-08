import queue
import re
import threading
from pathlib import Path
from urllib.parse import quote_plus
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError
from yandex_direct import extract_direct, validate_full_track, YandexAccessError
from track_files import remove_track_files, downloaded_audio_path, track_folder
from spotify_support import spotify_metadata, choose_spotify_version, SpotifyError
from covers import download_cover, image_url
from source_metadata import yandex_metadata
from soundcloud_text import description_lyrics, description_text, lyrics_record
from soundcloud_download import extract_soundcloud
from vk_audio import extract_vk_audio, vk_audio_id

from limits import MAX_TRACK_SECONDS,MAX_TRACK_BYTES

LIMIT_BYTES = MAX_TRACK_BYTES
LIMIT_SECONDS = MAX_TRACK_SECONDS

def youtube_connection_error(error):
    text=str(error).casefold()
    return any(marker in text for marker in ('unexpected_eof_while_reading','eof occurred in violation',
                                              'connection reset','connection aborted','timed out','timeout'))

class QuietLogger:
    def debug(self, _): pass
    def warning(self, _): pass
    def error(self, _): pass

def options(soundcloud=False):
    return dict(quiet=True, no_warnings=True, logger=QuietLogger(), noplaylist=True,
                socket_timeout=45 if soundcloud else 15, retries=2, fragment_retries=2, extractor_retries=2,
                compat_opts={'no-certifi'},
                js_runtimes={'deno': {}, 'node': {}},
                format='bestaudio/best', max_filesize=LIMIT_BYTES,
                concurrent_fragment_downloads=1)

def metadata_filter(info, *, incomplete=False, max_seconds=LIMIT_SECONDS):
    if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
        return 'Прямые эфиры не принимаются.'
    if (info.get('duration') or 0) > max_seconds:
        return f'Трек длиннее лимита заявки: {max_seconds//60}:{max_seconds%60:02}.'
    if max_seconds < LIMIT_SECONDS and not incomplete and not info.get('duration'):
        return 'Не удалось проверить длительность для лимита награды. Выбери другую запись.'
    if (info.get('filesize') or info.get('filesize_approx') or 0) > LIMIT_BYTES:
        return 'Файл больше 512 МБ.'
    return None

def search(query):
    if not query.strip():
        raise ValueError('Введи исполнителя и название песни.')
    if 'http://' in query.lower() or 'https://' in query.lower():
        raise ValueError('Для поиска нужны исполнитель и название, а не ссылка на сервис.')
    query = query.strip()
    opts = options()
    opts.update(extract_flat='in_playlist', skip_download=True, playlistend=10, extractor_retries=1)
    searches = ('ytsearch10:' + query,
                'https://music.youtube.com/search?q=' + quote_plus(query) + '#videos')
    completed = False
    last_error = None
    for target in searches:
        try:
            with YoutubeDL(opts) as ydl:
                data = ydl.extract_info(target, download=False)
                if not isinstance(data, dict) or data.get('entries') is None:
                    continue
                results = [
                    dict(title=entry.get('title') or 'Без названия',
                         url='https://www.youtube.com/watch?v=' + entry['id'],
                         duration=entry.get('duration'),
                         channel=entry.get('channel') or entry.get('uploader') or '')
                    for entry in data['entries']
                    if isinstance(entry, dict)
                    and re.fullmatch(r'[A-Za-z0-9_-]{11}', str(entry.get('id') or ''))
                    and not metadata_filter(entry)
                ]
        except (DownloadError, ExtractorError, OSError) as error:
            last_error = error
            continue
        completed = True
        if results:
            return results
    if completed:
        return []
    if last_error and youtube_connection_error(last_error):
        raise ValueError('YouTube оборвал соединение при поиске. Проверь доступ к YouTube на этом ПК и попробуй ещё раз.')
    if last_error:
        raise ValueError(str(last_error)) from None
    raise ValueError('YouTube и YouTube Music не вернули результаты. Попробуй позже или вставь прямую ссылку.')


def load_saved_cover(store, folder, row, cancelled=lambda: False):
    """Backfill old cached tracks without downloading their audio again."""
    folder = track_folder(folder, row)
    ident = row['id']
    def stopped():
        current = store.get(ident)
        return cancelled() or not current or current['status'] == 'Пропущено'
    if stopped():
        return ''
    try:
        info = row
        if not image_url(info):
            if row['source'] == 'Spotify':
                info = spotify_metadata(row['url'])
            elif row['source'] == 'Яндекс Музыка':
                info = yandex_metadata(row['url'])
            elif row['source'] in ('Telegram', 'MP3'):
                return ''
            else:
                opts = options(soundcloud=row['source']=='SoundCloud' and not row['replacement'])
                opts.update(skip_download=True)
                with YoutubeDL(opts) as ydl:
                    info = (extract_soundcloud(ydl,row['url'],download=False,cancelled=stopped) if row['source']=='SoundCloud' and not row['replacement'] else ydl.extract_info(row['replacement'] or row['url'], download=False)) or {}
        path = download_cover(info, folder, ident, cancelled=stopped)
        if path and not store.update_download(ident, cover_path=path, cover_url=image_url(info)):
            Path(path).unlink(missing_ok=True)
            return ''
        return path
    except Exception:
        return ''


def soundcloud_lyrics(store, row, cancelled=lambda: False,*,force=False,full=False):
    """Use saved description first; inspect old tracks without redownloading audio."""
    if row['source'] != 'SoundCloud' or cancelled():
        return None
    saved = lyrics_record(row,full=full)
    if not force and (saved or (row.get('description_checked') or 0)>=2):
        return saved
    try:
        opts = options(soundcloud=True)
        opts.update(skip_download=True)
        with YoutubeDL(opts) as ydl:
            info = extract_soundcloud(ydl,row['url'],download=False,cancelled=cancelled) or {}
        if cancelled():
            return None
        if cancelled():return None
        description=info.get('description')
        if not isinstance(description,str):return saved
        text = description_lyrics(description)
        raw=description_text(description)
        if not store.update_download(row['id'], description_lyrics=text, soundcloud_description=raw,description_checked=2):
            return None
        return lyrics_record(dict(row, description_lyrics=text,soundcloud_description=raw),full=full)
    except Exception:
        return saved if not cancelled() else None

class Downloader:
    def __init__(self, store, folder, emit, settings=None):
        self.store, self.folder, self.emit = store, Path(folder), emit
        self.folder.mkdir(parents=True, exist_ok=True)
        self.jobs = queue.PriorityQueue()
        self.job_sequence = 0
        self.pending = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.settings = settings or (lambda: {})
        for _ in range(2):
            threading.Thread(target=self.worker, daemon=True).start()

    def submit(self, ident):
        with self.lock:
            if ident not in self.pending:
                row=self.store.get(ident) or {}
                request=self.store.album_request(row['album_id']) if row.get('album_id') and row.get('album_position') else row
                paid=bool(request and request.get('donation_priority'))
                self.pending.add(ident)
                self.job_sequence+=1
                self.jobs.put((0 if paid else 1,request.get('donation_time',0) if paid else 0,self.job_sequence,ident))

    def worker(self):
        while not self.stop.is_set():
            try:
                _,_,_,ident = self.jobs.get(timeout=.5)
            except queue.Empty:
                continue
            request_row = None
            try:
                row = self.store.get(ident)
                request_row = row
                if row and row['status'] == 'Ожидает загрузки':
                    self.download(row)
            except Exception as exc:
                row = self.store.get(ident)
                if row and row['status'] != 'Пропущено':
                    status = 'Ошибка Яндекса' if row['source'] == 'Яндекс Музыка' and not row['replacement'] else ('Ошибка SoundCloud' if row['source']=='SoundCloud' and not row['replacement'] else 'Нужна замена')
                    self.store.update_download(ident, status=status, error=str(exc)[:1800])
            finally:
                if request_row and request_row.get('album_id'):
                    self.store.update_album_progress(request_row['album_id'])
                if not self.store.get(ident):
                    remove_track_files(track_folder(self.folder, request_row or {}), ident)
                with self.lock:
                    self.pending.discard(ident)
                self.jobs.task_done()
                self.emit('queue', None)

    def download(self, row):
        try:
            return self._download(row)
        except Exception as original:
            current=self.store.get(row['id'])
            from urllib.parse import urlparse
            host=(urlparse(row.get('replacement') or row['url']).hostname or '').lower().removeprefix('www.')
            if host in ('youtube.com','youtu.be','music.youtube.com','m.youtube.com') and youtube_connection_error(original):
                if not current or self.stop.is_set() or current['status']=='Пропущено':return
                self.emit('log','YouTube оборвал соединение — повторяем загрузку с новым подключением.')
                if self.stop.wait(1):return
                current=self.store.get(row['id'])
                if not current or current['status']=='Пропущено':return
                try:return self._download(current)
                except Exception as repeated:
                    if not youtube_connection_error(repeated):raise
                    raise RuntimeError('YouTube повторно оборвал защищённое соединение (SSL/сеть). Проверь доступ к YouTube на этом компьютере и нажми «Повторить». Заказ сохранён; отправлять донат заново не нужно.') from None
            if (not current or self.stop.is_set() or current['status']=='Пропущено' or
                row['source']!='Яндекс Музыка' or not row.get('album_id') or
                not row.get('album_position') or row.get('replacement')):
                raise
            # Limits and explicit cancellation must never trigger a workaround.
            if any(word in str(original).lower() for word in ('лимит','длиннее','100 мб','512 мб','отмен')):
                raise
            return self.replace_album_track(current, original)

    def replace_album_track(self, row, original):
        ident=row['id']
        def cancelled():
            current=self.store.get(ident)
            return self.stop.is_set() or not current or current['status']=='Пропущено'
        if cancelled():return
        metadata={key:row.get(key) for key in ('artist','track','duration','title','cover_url')}
        if not metadata['artist'] or not metadata['track'] or not metadata['duration']:
            try:
                metadata.update(yandex_metadata(row['url']))
            except Exception:
                raise YandexAccessError('Трек недоступен в Яндексе. Для точной автозамены не удалось получить исполнителя, название и длительность.') from None
        import math
        duration=metadata.get('duration')
        if not metadata.get('artist') or not metadata.get('track') or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration<=0:
            raise YandexAccessError('Трек недоступен в Яндексе. Недостаточно данных для точной автозамены.')
        if duration>(row.get('max_duration') or LIMIT_SECONDS):
            raise YandexAccessError('Трек длиннее лимита награды; автоматическая замена не выполняется.')
        if cancelled():return
        if not self.store.update_download(ident,status='Поиск замены',error='',**metadata):return
        self.emit('queue',None)
        candidates=search(metadata['artist']+' '+metadata['track'])
        if cancelled():return
        candidate=choose_spotify_version(metadata,candidates)
        if not candidate:
            raise YandexAccessError('Трек недоступен в Яндексе. Не найдена точная замена на YouTube по исполнителю, названию, версии и длительности.') from None
        folder=track_folder(self.folder,row)
        remove_track_files(folder,ident)
        if cancelled():return
        if not self.store.update_download(ident,replacement=candidate['url'],status='Ожидает загрузки',error='',path=''):return
        current=self.store.get(ident)
        if current and not cancelled():return self._download(current)

    def _download(self, row):
        ident = row['id']
        folder = track_folder(self.folder, row)
        folder.mkdir(parents=True, exist_ok=True)
        if not self.store.update_download(ident, status='Загрузка', error=''):
            return
        self.emit('queue', None)
        url = row['replacement'] or row['url']
        direct_yandex = row['source'] == 'Яндекс Музыка' and not row['replacement']
        automatic_spotify = row['source'] == 'Spotify' and not row['replacement']
        album_replacement = row['source']=='Яндекс Музыка' and bool(row.get('album_id')) and bool(row['replacement'])
        duration_limit = row.get('max_duration') or LIMIT_SECONDS
        if automatic_spotify:
            metadata = spotify_metadata(url)
            if metadata['duration'] > duration_limit:
                raise SpotifyError(f'Песня длиннее лимита награды: {duration_limit//60}:{duration_limit%60:02}.')
            if self.stop.is_set():
                return
            if not self.store.update_download(ident, status='Поиск записи', **metadata):
                return
            self.emit('queue', None)
            candidates = search(metadata['artist'] + ' ' + metadata['track'])
            current = self.store.get(ident)
            if self.stop.is_set() or not current or current['status'] == 'Пропущено':
                return
            candidate = choose_spotify_version(metadata, candidates)
            if not candidate:
                raise SpotifyError('Не найдена уверенная версия Spotify-трека на YouTube по исполнителю, названию и длительности. Нажми «Найти версию» для выбора вручную.')
            url = candidate['url']
            if not self.store.update_download(ident, replacement=url, status='Загрузка'):
                return
            row = self.store.get(ident)
            if not row:
                return
            self.emit('queue', None)

        def request_cancelled():
            current = self.store.get(ident)
            return self.stop.is_set() or not current or current['status'] == 'Пропущено'

        completed_files = []
        rejected = []
        def hook(data):
            if request_cancelled():
                raise ValueError('Загрузка отменена.')
            if data.get('downloaded_bytes', 0) > LIMIT_BYTES:
                raise ValueError('Загрузка остановлена: больше 512 МБ.')
            if data.get('status') == 'finished' and isinstance(data.get('filename'), str):
                completed_files.append(data['filename'])

        def processed(data):
            if data.get('status') == 'finished':
                info = data.get('info_dict') or {}
                if isinstance(info.get('filepath'), str):
                    completed_files.append(info['filepath'])

        direct_vk_audio = row['source']=='VK' and not row['replacement'] and bool(vk_audio_id(url))
        vk_settings = dict(getattr(self, 'settings', lambda: {})()) if direct_vk_audio else {}
        direct_soundcloud = row['source']=='SoundCloud' and not row['replacement']
        opts = options(soundcloud=direct_soundcloud)
        def checked_filter(info, *, incomplete=False):
            reason = metadata_filter(info, incomplete=incomplete, max_seconds=duration_limit)
            if reason:
                rejected.append(reason)
                return reason
            if (automatic_spotify or album_replacement) and not incomplete:
                seconds = info.get('duration') or 0
                if seconds <= 0 or abs(seconds-row['duration']) > max(4, row['duration']*.03):
                    reason = 'Длительность найденной записи не совпала с оригиналом. Выбери версию вручную.'
                    rejected.append(reason)
                    return reason
            return None
        opts.update(outtmpl=str(folder/(ident+'.%(ext)s')),
                    match_filter=checked_filter, progress_hooks=[hook], postprocessor_hooks=[processed],
                    windowsfilenames=True)
        with YoutubeDL(opts) as ydl:
            if direct_yandex:
                info = extract_direct(ydl, url, getattr(self, 'settings', lambda: {})())
            elif direct_vk_audio:
                def remember_vk(metadata):
                    if self.store.update_download(ident, **metadata):self.emit('queue', None)
                info = extract_vk_audio(ydl,url,download=True,cancelled=request_cancelled,metadata_notice=remember_vk,settings=vk_settings)
            elif direct_soundcloud:
                info = extract_soundcloud(ydl,url,download=True,
                    cancelled=request_cancelled,
                    retry_notice=lambda: self.emit('log','SoundCloud не ответил — повторная попытка загрузки.'))
            else:
                info = ydl.extract_info(url, download=True)
            if rejected:
                raise ValueError(rejected[-1])
            if not info and duration_limit < LIMIT_SECONDS:
                raise ValueError(f'Запись не прошла проверку: лимит награды {duration_limit//60}:{duration_limit%60:02}, длительность должна быть известна. Выбери подходящую запись.')
            if not info and automatic_spotify:
                raise SpotifyError('Найденная запись не прошла проверку длительности или доступности. Нажми «Найти версию» для выбора другой записи.')
            if not info or info.get('_type') in ('playlist', 'multi_video'):
                raise ValueError('Нужна одна песня, а не плейлист.')
            filename = ydl.prepare_filename(info)
        current = self.store.get(ident)
        if not current or current['status'] == 'Пропущено':
            remove_track_files(folder, ident, filename)
            return
        p = downloaded_audio_path(folder, ident, info, filename, completed_files)
        if p is None:
            raise ValueError('После загрузки не найден готовый аудиофайл. Нажми «Повторить». Если ошибка остаётся, пришли ссылку на трек и сообщения «Журнала событий» — потребуется проверить ответ источника и обработку файла.')
        if p.stat().st_size > LIMIT_BYTES:
            p.unlink()
            raise ValueError('Файл больше 512 МБ.')
        if direct_yandex or album_replacement or (direct_vk_audio and vk_settings.get('vk_auth')):
            try:
                validate_full_track(p, row['duration'] if album_replacement else info.get('duration') or 0)
            except YandexAccessError as exc:
                p.unlink(missing_ok=True)
                if direct_vk_audio:
                    from vk_audio import VKAudioError
                    raise VKAudioError(str(exc).replace('Яндекса','VK').replace('Яндекс','VK')) from None
                raise
        artists = info.get('artists') or []
        spotify = row['source'] == 'Spotify' or album_replacement
        artist = (row.get('artist') if spotify else '') or info.get('artist') or (', '.join(map(str, artists)) if isinstance(artists, (list, tuple)) else str(artists))
        track = (row.get('track') if spotify else '') or info.get('track') or (info.get('title') if direct_yandex else '') or ''
        title = (str(artist)+' — '+track) if (direct_yandex or spotify) and artist and track else info.get('title') or row['title']
        cover_info = {'cover_url': row.get('cover_url')} if spotify and row.get('cover_url') else info
        def cover_cancelled():
            current = self.store.get(ident)
            return self.stop.is_set() or not current or current['status'] == 'Пропущено'
        cover_path = download_cover(cover_info, folder, ident, cancelled=cover_cancelled)
        from_soundcloud = row['source'] == 'SoundCloud' and not row['replacement']
        if not self.store.update_download(ident, title=title, path=str(p), status='Готово', error='',
                                   artist=str(artist or ''), track=str(track), duration=info.get('duration') or 0,
                                   cover_url=image_url(cover_info), cover_path=cover_path,
                                   description_lyrics=description_lyrics(info.get('description')) if from_soundcloud else (row.get('description_lyrics','') if row['source']=='SoundCloud' else ''),
                                   soundcloud_description=description_text(info.get('description')) if from_soundcloud else (row.get('soundcloud_description','') if row['source']=='SoundCloud' else ''),
                                   description_checked=(2 if isinstance(info.get('description'),str) else 0) if from_soundcloud else (row.get('description_checked',0) if row['source']=='SoundCloud' else 0)):
            remove_track_files(folder, ident, str(p))
