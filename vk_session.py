"""Explicit, domain-scoped VK browser sessions. No password collection or exports."""
import re
import sqlite3
from copy import copy
from pathlib import Path
from http.cookiejar import Cookie
from yt_dlp.cookies import extract_cookies_from_browser,YoutubeDLCookieJar,YDLLogger
from browser_session import firefox_profile,session_read_error


class VKSessionError(ValueError):
    pass


def vk_domain(domain):
    domain=domain.lstrip('.').lower()
    return any(domain==root or domain.endswith('.'+root) for root in ('vk.com','vk.ru'))


def firefox_vk_cookies(profile):
    path=firefox_profile(profile)/'cookies.sqlite'
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    connection.row_factory=sqlite3.Row
    jar=YoutubeDLCookieJar()
    try:
        columns={r[1] for r in connection.execute('PRAGMA table_info(moz_cookies)')}
        query="SELECT host,name,value,path,expiry,isSecure FROM moz_cookies WHERE (host='vk.ru' OR host LIKE '%.vk.ru' OR host='vk.com' OR host LIKE '%.vk.com')"
        if 'originAttributes' in columns:query+=" AND originAttributes=''"
        for row in connection.execute(query):
            if not vk_domain(row['host']):continue
            jar.set_cookie(Cookie(0,row['name'],row['value'],None,False,row['host'],True,row['host'].startswith('.'),row['path'] or '/',True,bool(row['isSecure']),row['expiry'] or None,False,None,None,{},False))
    finally:connection.close()
    return jar


def attach_vk_session(ydl,settings):
    mode=settings.get('vk_auth','')
    if not mode:return False
    if mode not in ('firefox','chrome','edge','cookies'):raise VKSessionError('Выбери источник входа в настройках VK Музыки.')
    try:
        if mode=='cookies':
            path=Path(settings.get('vk_cookie_file',''))
            if not path.is_file():raise VKSessionError('Выбери файл cookies.txt в настройках VK Музыки.')
            jar=YoutubeDLCookieJar(str(path));jar.load(ignore_discard=True,ignore_expires=False)
        else:
            profiles=settings.get('vk_profiles') or {}
            profile=profiles.get(mode,'') if isinstance(profiles,dict) else ''
            kwargs={'logger':YDLLogger(ydl)}
            if profile:kwargs['profile']=profile
            try:jar=extract_cookies_from_browser(mode,**kwargs)
            except Exception:
                if mode!='firefox':raise
                jar=firefox_vk_cookies(profile)
        allowed=[copy(cookie) for cookie in jar if vk_domain(cookie.domain) and not cookie.is_expired()]
        if not any(c.name in ('remixsid','remixsid6','remixsid5') and c.value for c in allowed):
            raise VKSessionError('Сессия VK не найдена или истекла. Войди на vk.ru в выбранном профиле браузера и повтори проверку.')
        for cookie in allowed:ydl.cookiejar.set_cookie(cookie)
        return True
    except VKSessionError:raise
    except Exception as exc:
        message=session_read_error(exc).replace('настройках Яндекса','настройках VK Музыки').replace('выбранный профиль браузера','выбранный профиль браузера VK')
        raise VKSessionError(message) from None


def vk_hosts(ydl):
    # Language/analytics cookies do not identify the authenticated domain.
    roots=[]
    for root in ('vk.ru','vk.com'):
        if any(c.name in ('remixsid','remixsid6','remixsid5') and c.value and not c.is_expired()
               and (c.domain.lstrip('.')==root or c.domain.lstrip('.').endswith('.'+root)) for c in ydl.cookiejar):
            roots.append(root)
    return roots or ['vk.ru']


def vk_host(ydl):
    return vk_hosts(ydl)[0]


def page_user_id(webpage):
    # The ID must belong to VK's own global object, not an arbitrary post/user.
    direct=re.search(r'\bvk\.id\s*=\s*([1-9]\d*)',webpage)
    if direct:return int(direct[1])
    for start in re.finditer(r'\bvk\s*=\s*\{',webpage):
        text=webpage[start.end():start.end()+64000]
        depth=1;quote='';escaped=False;property_start=True
        i=0
        while i<len(text):
            char=text[i]
            if not quote and depth==1 and property_start:
                match=re.match(r"\s*['\"]?id['\"]?\s*:\s*([1-9]\d*)\s*[,}]",text[i:])
                if match:return int(match[1])
                if not char.isspace():property_start=False
            if quote:
                if escaped:escaped=False
                elif char=='\\':escaped=True
                elif char==quote:quote=''
            elif char in ('\"',"'",'`'):quote=char
            elif char in '{[(':depth+=1
            elif char in '}])':
                depth-=1
                if depth==0:break
            elif char==',' and depth==1:property_start=True
            i+=1
    return 0


def session_user_id(ydl,webpage=''):
    ident=page_user_id(webpage)
    if ident:return ident
    for cookie in ydl.cookiejar:
        if vk_domain(cookie.domain) and not cookie.is_expired() and cookie.name=='remixuid' and cookie.value.isdigit():return int(cookie.value)
    return 0


def check_vk_session(settings):
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request
    class Silent:
        def debug(self,*_):pass
        def warning(self,*_):pass
        def error(self,*_):pass
    try:
        with YoutubeDL({'quiet':True,'no_warnings':True,'socket_timeout':12,'logger':Silent()},auto_init=False) as ydl:
            if not attach_vk_session(ydl,settings):raise VKSessionError('Выбери источник входа VK Музыки.')
            from urllib.parse import urlparse
            login_wall=False
            for host in vk_hosts(ydl):
                for surface in (host,'m.'+host):
                    with ydl.urlopen(Request('https://'+surface+'/feed')) as response:
                        raw=response.read(2_000_001)
                        final_url=getattr(response,'url','')
                    if len(raw)>2_000_000:raise VKSessionError('VK вернул слишком большой ответ. Повтори проверку позже.')
                    page=raw.decode('utf-8',errors='replace')
                    parsed=urlparse(final_url) if isinstance(final_url,str) else None
                    wall=bool(parsed and (parsed.hostname in ('login.vk.com','id.vk.com','id.vk.ru') or parsed.path in ('/login','/login.php','/captcha','/security_check')))
                    wall=wall or bool(re.search(r'<form\b[^>]*\bid=[\"\'](?:quick_login_form|login_form)[\"\']',page,re.I))
                    if not wall and page_user_id(page):
                        return 'Вход VK подтверждён. Теперь выдели заявку и нажми «Повторить».'
                    login_wall=login_wall or wall
            if login_wall:
                raise VKSessionError('VK показал страницу входа или проверки безопасности. Открой VK в этом профиле Firefox/Chrome и заверши вход. Если профилей несколько, укажи нужную папку вручную.')
            # An unrecognized app shell is inconclusive, not proof of logout.
            return 'Сессия VK прочитана, но подтвердить вход по странице не удалось. Выдели заявку и нажми «Повторить»: доступ проверится при запросе трека. Если не получится, укажи папку профиля с входом VK через «Папка профиля».'
    except VKSessionError:raise
    except Exception:raise VKSessionError('Не удалось проверить вход VK. Проверь интернет и открой vk.ru в выбранном браузере.') from None
