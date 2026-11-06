"""VK web-session audio lookup when the HTML contains only an app shell.

Uses the selected account's normal web session. Tokens stay in memory and are
sent in POST bodies only. No passwords, third-party token services or DRM bypass.
"""
import hashlib
import threading
import time
from collections import OrderedDict
from urllib.parse import urlencode,urlparse
from vk_session import vk_host
from vk_records import structured_records

_cache=OrderedDict()
_blocked=OrderedDict()
_lock=threading.Lock()
WEB_CLIENT_ID='6287487'
API_VERSION='5.131'


class VKWebError(ValueError):
    pass


def token_value(data):
    if not isinstance(data,dict):return ''
    for value in (data,data.get('data'),data.get('response')):
        if isinstance(value,dict):
            token=value.get('access_token')
            if isinstance(token,str) and 10<=len(token)<=4096 and not any(c.isspace() for c in token):return token
    return ''


def web_audio_record(extractor,url,ident):
    """Return raw exact-ID record; propagate only fixed, non-sensitive messages."""
    host=vk_host(extractor._downloader)
    digest=hashlib.sha256()
    for cookie in sorted(extractor._downloader.cookiejar,key=lambda c:(c.domain,c.name,c.path)):
        if cookie.name in ('remixsid','remixsid5','remixsid6','p'):
            digest.update((cookie.domain+'\0'+cookie.name+'\0'+cookie.value+'\0').encode())
    key=(host,digest.digest())
    while not _lock.acquire(timeout=.1):
        if extractor.cancelled():raise VKWebError('Загрузка отменена.')
    try:
        if extractor.cancelled():raise VKWebError('Загрузка отменена.')
        if _blocked.get(key,0)>time.monotonic():raise VKWebError('VK-W04: веб-API просит подождать. Повтори загрузку через минуту.')
        cached=_cache.get(key)
        if cached and cached[0]>time.monotonic():
            token=cached[1];_cache.move_to_end(key)
        else:
            try:
                data=extractor.reload_playback('https://login.'+host+'/?act=web_token',ident,
                    urlencode({'version':'1','app_id':WEB_CLIENT_ID}).encode(),'https://'+host+'/')
            except Exception:
                raise VKWebError('VK-W01: не удалось получить веб-доступ через выбранную сессию VK. HTTP-вход может быть действующим, но доступ к веб-API не подтверждён.') from None
            if extractor.cancelled():raise VKWebError('Загрузка отменена.')
            token=token_value(data)
            if not token:raise VKWebError('VK-W01: VK не выдал веб-токен выбранной сессии. Нужна проверка ответа веб-авторизации; повторный выбор того же профиля не гарантирует исправление.')
            _cache[key]=(time.monotonic()+480,token)
            while len(_cache)>4:_cache.popitem(last=False)
    finally:_lock.release()
    if extractor.cancelled():raise VKWebError('Загрузка отменена.')
    shared_id=urlparse(url).path.rstrip('/').removeprefix('/audio')
    # The caller already validated this exact single-track URL, including its key.
    params={'audios':shared_id,'access_token':token,'v':API_VERSION,'client_id':WEB_CLIENT_ID}
    try:
        response=extractor.reload_playback('https://web.api.'+host+'/method/audio.getById',ident,
                                         urlencode(params).encode(),'https://'+host+'/')
    except Exception:
        raise VKWebError('VK-W02: запрос аудиозаписи к веб-API не выполнен. Проверь сеть или повтори позже.') from None
    if extractor.cancelled():raise VKWebError('Загрузка отменена.')
    error=response.get('error') if isinstance(response,dict) else None
    if isinstance(error,dict):
        code=error.get('error_code')
        if code==5:
            with _lock:_cache.pop(key,None)
            raise VKWebError('VK-W03: веб-API отклонил токен сессии. Повтори вход на vk.ru и проверку в приложении.')
        if code in (6,9):
            with _lock:
                _blocked[key]=time.monotonic()+60
                while len(_blocked)>4:_blocked.popitem(last=False)
            raise VKWebError('VK-W04: веб-API ограничил частоту запросов. Подожди минуту и повтори.')
        if code==14:raise VKWebError('VK-W05: веб-API требует проверку безопасности. Заверши её в браузере.')
        if code in (15,20,27):raise VKWebError('VK-W06: веб-API не разрешил доступ к этой аудиозаписи. Это отдельное ограничение API, даже если песня играет в браузере.')
        label=str(code) if type(code) is int and 0<=code<=9999 else 'неизвестный'
        raise VKWebError('VK-W07: веб-API вернул код '+label+'. Полный ответ не выводится, поскольку может содержать данные сессии.')
    for record in structured_records(response):
        if str(record.get('owner_id'))+'_'+str(record.get('id'))==ident:return record
    raise VKWebError('VK-W08: веб-API не вернул запись с запрошенным ID. Соседние треки не подставлены.')
