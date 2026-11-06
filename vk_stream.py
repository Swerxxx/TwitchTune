"""Verify audio returned by VK's authenticated API, independent of CDN/path suffix."""
import ipaddress
import socket
from urllib.parse import urlparse
from yt_dlp.networking import Request


class VKStreamError(ValueError):
    pass


def public_https_target(url):
    try:
        if not isinstance(url,str) or any(ord(c)<32 or ord(c)==127 for c in url):return False
        parsed=urlparse(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):return False
        addresses=socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)
        ips=[ipaddress.ip_address(item[4][0].split('%')[0]) for item in addresses]
        return bool(ips) and all(ip.is_global and not ip.is_multicast for ip in ips)
    except (ValueError,OSError):return False


def audio_type(content_type,data):
    if not data:return ''
    mime=content_type.partition(';')[0].strip().lower()
    prefix=data.lstrip(b'\xef\xbb\xbf \r\n\t')
    if prefix.startswith(b'#EXTM3U'):return 'm3u8'
    if prefix.startswith((b'<',b'{',b'[')):return ''
    mimes={'audio/mpeg':'mp3','audio/mp3':'mp3','audio/mp4':'m4a','audio/x-m4a':'m4a','audio/m4a':'m4a',
           'audio/aac':'aac','audio/aacp':'aac','audio/ogg':'ogg','application/ogg':'ogg','audio/opus':'opus',
           'audio/flac':'flac','audio/x-flac':'flac','audio/wav':'wav','audio/x-wav':'wav','audio/wave':'wav'}
    if mime in mimes:return mimes[mime]
    if mime not in ('','application/octet-stream','binary/octet-stream'):return ''
    if data.startswith(b'ID3'):return 'mp3'
    if data.startswith(b'fLaC'):return 'flac'
    if data.startswith(b'OggS'):return 'opus' if b'OpusHead' in data[:128] else 'ogg'
    if data[:4]==b'RIFF' and data[8:12]==b'WAVE':return 'wav'
    if data[4:8]==b'ftyp' and data[8:12] in (b'M4A ',b'M4B ',b'isom',b'iso2',b'mp41',b'mp42',b'dash'):return 'm4a'
    if len(data)>=3 and data[0]==255:
        if data[1]&0xF6==0xF0:return 'aac'
        if data[1]&0xE0==0xE0 and data[1]&6 and data[1]&0x18!=8 and data[2]&0xF0!=0xF0 and data[2]&12!=12:return 'mp3'
    return ''


def verified_audio_formats(extractor,url,ident,referer):
    if extractor.cancelled():raise VKStreamError('Загрузка отменена.')
    if not public_https_target(url):raise VKStreamError('VK-A06: адрес из API не прошёл проверку публичного HTTPS-сервера. Проверь сеть или DNS.')
    if extractor.cancelled():raise VKStreamError('Загрузка отменена.')
    try:
        # Some CDNs ignore Range: read only the bounded prefix, then close.
        with extractor._downloader.urlopen(Request(url,headers={'Range':'bytes=0-4095','Referer':referer})) as response:
            final_url=getattr(response,'url',url)
            if not isinstance(final_url,str):final_url=url
            if final_url!=url and not public_https_target(final_url):raise VKStreamError('VK-A06: перенаправление аудиосервера не прошло проверку HTTPS.')
            status=getattr(response,'status',200)
            if status not in (200,206):raise VKStreamError('VK-A06: аудиосервер не вернул файл.')
            content_type=response.headers.get('Content-Type','')
            data=response.read(4096)
    except VKStreamError:raise
    except Exception:raise VKStreamError('VK-A06: не удалось проверить аудио по адресу из API. Ответ или подписанная ссылка могли истечь; нажми «Повторить».') from None
    if extractor.cancelled():raise VKStreamError('Загрузка отменена.')
    ext=audio_type(content_type,data)
    if not ext:raise VKStreamError('VK-A07: адрес из API доступен, но сервер не вернул распознаваемое аудио или HLS. Полный адрес и ответ не записываются.')
    if ext=='m3u8':
        formats=extractor._extract_m3u8_formats(final_url,ident,'m4a',m3u8_id='hls',fatal=False,headers={'Referer':referer})
        formats=[x for x in formats if not x.get('has_drm')]
        if not formats:raise VKStreamError('VK-A05: HLS-поток не содержит доступных форматов без DRM.')
        return formats
    return [dict(url=final_url,ext=ext,vcodec='none',http_headers={'Referer':referer})]
