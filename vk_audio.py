"""VK track pages and authenticated playback with exact-ID validation."""
import math
import re
import threading
import time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from vk_records import AudioPage, structured_records
from vk_web_api import web_audio_record, VKWebError
from vk_stream import verified_audio_formats, VKStreamError
from urllib.parse import urlparse, urlencode
from vk_session import attach_vk_session, vk_host, session_user_id, VKSessionError
from yt_dlp.extractor.vk import VKWallPostIE
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import determine_ext, clean_html


class VKAudioError(ValueError):
    pass


def vk_audio_id(url):
    parsed=urlparse(url)
    if parsed.hostname not in ('vk.ru','vk.com','www.vk.ru','www.vk.com','m.vk.ru','m.vk.com'):
        return ''
    match=re.fullmatch(r'/audio(-?\d+_\d+)(?:_[A-Za-z0-9]{1,256})?/?',parsed.path)
    return match[1] if match else ''


def audio_record(html,ident):
    parser=AudioPage();parser.feed(html)
    return record_by_id(parser.records,ident)


def record_by_id(records,ident):
    candidates=[]
    for record in records:
        if f"{record.get('owner_id')}_{record.get('id')}"!=ident:continue
        artist=record.get('artist') or record.get('performer')
        track=record.get('title')
        if not isinstance(artist,str) or not isinstance(track,str) or not artist.strip() or not track.strip():continue
        artist,track=(clean_html(artist) or '').strip()[:200],(clean_html(track) or '').strip()[:300]
        if not artist or not track:continue
        try:duration=float(record.get('duration') or 0)
        except (TypeError,ValueError):duration=0
        if not math.isfinite(duration) or duration<0:duration=0
        candidates.append(dict(title=artist+' — '+track,artist=artist,track=track,duration=duration,
                    url=record.get('url') if isinstance(record.get('url'),str) else '',
                    actionHash=record.get('actionHash',''),urlHash=record.get('urlHash',''),coverUrl=record.get('coverUrl','')))
    return max(candidates,key=lambda x:(playable_url(x['url']),bool(x['actionHash'] and x['urlHash']),bool(x['url'])),default=None)


def playable_url(url):
    try:
        parsed=urlparse(url)
        host=parsed.hostname or ''
        roots=('vkuseraudio.net','vkuseraudio.ru','useraudio.net','userapi.com','vk.com','vk.ru')
        return (parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443)
                and any(host==root or host.endswith('.'+root) for root in roots)
                and 'audio_api_unavailable' not in url and determine_ext(url) in ('mp3','m4a','aac','ogg','opus','m3u8'))
    except ValueError:return False


_reload_lock=threading.Lock()
_reload_after=0




class TwitchTuneVKAudioIE(InfoExtractor):
    IE_NAME='twitchtune:vk-audio'
    _VALID_URL=r'https?://(?:(?:www|m)\.)?vk\.(?:com|ru)/audio(?P<id>-?\d+_\d+)(?:_[A-Za-z0-9]{1,256})?/?(?:[?#]|$)'
    def __init__(self,downloader,cancelled=lambda:False,metadata_notice=lambda _:None):
        super().__init__(downloader)
        self.cancelled,self.metadata_notice=cancelled,metadata_notice
        self.authenticated=False
    def reload_playback(self,url,ident,data,page_url):
        global _reload_after
        while not _reload_lock.acquire(timeout=.1):
            if self.cancelled():raise VKAudioError('Загрузка отменена.')
        try:
            wait=_reload_after-time.monotonic()
            if wait>2:raise VKAudioError('VK просит подождать. Повтори загрузку немного позже.')
            while time.monotonic()<_reload_after:
                if self.cancelled():raise VKAudioError('Загрузка отменена.')
                time.sleep(.1)
            if self.cancelled():raise VKAudioError('Загрузка отменена.')
            try:
                return self._download_json(url,ident,data=data,
                    headers={'X-Requested-With':'XMLHttpRequest','Content-Type':'application/x-www-form-urlencoded','Referer':page_url,'Origin':'https://'+(urlparse(page_url).hostname or 'vk.ru')},
                    transform_source=lambda text:text.removeprefix('<!--'))
            except Exception as exc:
                cause=getattr(exc,'cause',None) or exc
                if getattr(cause,'status',None) in (429,503):
                    retry=getattr(cause,'headers',{}).get('Retry-After','60')
                    try:delay=float(retry)
                    except (TypeError,ValueError):
                        try:
                            date=parsedate_to_datetime(retry)
                            if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
                            delay=(date-datetime.now(timezone.utc)).total_seconds()
                        except (TypeError,ValueError,OverflowError):delay=60
                    if not math.isfinite(delay):delay=60
                    _reload_after=time.monotonic()+max(1,delay)
                    raise VKAudioError('VK временно ограничил запросы. Повтори загрузку немного позже.') from None
                raise
            finally:_reload_after=max(_reload_after,time.monotonic()+.75)
        finally:_reload_lock.release()

    def _real_extract(self,url):
        ident=self._match_id(url)
        if self.cancelled():raise VKAudioError('Загрузка отменена.')
        audio_path=urlparse(url).path.rstrip('/')
        page_url=('https://m.'+vk_host(self._downloader)+audio_path) if self.authenticated else url
        webpage=self._download_webpage(page_url,ident)
        if self.cancelled():raise VKAudioError('Загрузка отменена.')
        record=audio_record(webpage,ident)
        if not record and self.authenticated and not self.cancelled():
            webpage=self._download_webpage('https://'+vk_host(self._downloader)+audio_path,ident)
            record=audio_record(webpage,ident)
        from_web_api=False
        if not record and self.authenticated and not self.cancelled():
            from_web_api=True
            record=record_by_id([web_audio_record(self,url,ident)],ident)
        if not record:raise VKAudioError('VK-A01: страница получена, но данные именно этой аудиозаписи не найдены. Нужна проверка формата страницы VK; повторный вход сам по себе эту ошибку не объясняет.')
        metadata={key:record[key] for key in ('title','artist','track','duration')}
        self.metadata_notice(metadata)
        if self.cancelled():raise VKAudioError('Загрузка отменена.')
        stream=record['url']
        if self.authenticated and not from_web_api and not playable_url(stream):
            action,url_hash=record['actionHash'],record['urlHash']
            if not all(isinstance(x,str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,512}',x) for x in (action,url_hash)):
                raise VKAudioError('VK-A02: запись найдена, но VK не передал параметры запроса воспроизведения.')
            data=urlencode({'act':'reload_audio','ids':ident+'_'+action+'_'+url_hash}).encode()
            if self.cancelled():raise VKAudioError('Загрузка отменена.')
            response=self.reload_playback('https://m.'+vk_host(self._downloader)+'/audio',ident,data,page_url)
            # Only take the exact requested recording from the playback response.
            refreshed=record_by_id(structured_records(response),ident)
            if not refreshed:raise VKAudioError('VK-A03: ответ воспроизведения не содержит запрошенный трек. Другие записи не подставлены.')
            record=refreshed
            metadata={key:record[key] for key in ('title','artist','track','duration')}
            self.metadata_notice(metadata)
            stream=record['url']
        if 'audio_api_unavailable' in stream:
            user_id=session_user_id(self._downloader,webpage)
            if not user_id:raise VKAudioError('VK не отдал ID текущего аккаунта. Обнови вход на vk.ru и повтори проверку в настройках.')
            try:stream=VKWallPostIE()._unmask_url(stream,user_id)
            except Exception:raise VKAudioError('Не удалось прочитать адрес воспроизведения VK. Повтори вход или выбери другую версию.') from None
        if self.cancelled():raise VKAudioError('Загрузка отменена.')
        formats=None
        referer=('https://'+vk_host(self._downloader)+'/') if from_web_api else page_url
        if from_web_api and stream and not playable_url(stream):
            formats=verified_audio_formats(self,stream,ident,referer)
        if formats is None and not playable_url(stream):raise VKAudioError('VK-A04: запись найдена, но адрес аудиопотока отсутствует или его формат не поддержан.')
        if self.authenticated and not metadata['duration']:raise VKAudioError('VK не отдал длительность записи. Полный трек проверить не удалось.')
        if formats is not None:
            pass
        elif determine_ext(stream)=='m3u8':
            formats=self._extract_m3u8_formats(stream,ident,'m4a',m3u8_id='hls',fatal=False)
            formats=[x for x in formats if not x.get('has_drm')]
            if not formats:raise VKAudioError('VK-A05: HLS-поток не содержит доступных форматов без DRM.')
        else:
            formats=[dict(url=stream,ext=determine_ext(stream),vcodec='none')]
        covers=record.get('coverUrl') or ''
        thumbnails=[{'url':x} for x in covers.split(',') if x.startswith('https://')] if isinstance(covers,str) else []
        return dict(id=ident,**metadata,formats=formats,thumbnails=thumbnails,http_headers={'Referer':referer})


def extract_vk_audio(ydl,url,*,download=True,cancelled=lambda:False,metadata_notice=lambda _:None,settings=None):
    if not vk_audio_id(url):raise VKAudioError('Нужна ссылка VK на одну аудиозапись.')
    extractor=TwitchTuneVKAudioIE(ydl,cancelled,metadata_notice)
    ydl.add_info_extractor(extractor)
    try:
        extractor.authenticated=attach_vk_session(ydl,settings or {})
        return ydl.extract_info(url,download=download,ie_key=extractor.ie_key())
    except Exception as exc:
        # Keep signed CDN URLs and internal downloader traces out of the UI.
        current=exc;seen=set()
        while isinstance(current,BaseException) and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current,(VKAudioError,VKSessionError,VKWebError,VKStreamError)):raise VKAudioError(str(current)) from None
            current=getattr(current,'__cause__',None) or getattr(current,'__context__',None)
        raise VKAudioError('Не удалось загрузить аудиозапись VK. Проверь доступность ссылки. Если VK требует входа или не отдаёт файл, нажми «Найти версию».') from None
