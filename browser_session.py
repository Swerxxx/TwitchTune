"""Read-only Firefox fallback for the explicitly selected Yandex session."""
import configparser
import os
import sqlite3
from http.cookiejar import Cookie
from pathlib import Path
from yt_dlp.cookies import YoutubeDLCookieJar


def firefox_profile(explicit=''):
    if explicit:
        path = Path(explicit).expanduser()
        if not (path/'cookies.sqlite').is_file():
            raise FileNotFoundError('Firefox profile cookie database missing')
        return path.resolve()
    roots = []
    if os.environ.get('APPDATA'):
        roots.append(Path(os.environ['APPDATA'])/'Mozilla'/'Firefox')
    if os.environ.get('LOCALAPPDATA'):
        packages = Path(os.environ['LOCALAPPDATA'])/'Packages'
        roots.extend(p/'LocalCache'/'Roaming'/'Mozilla'/'Firefox' for p in packages.glob('Mozilla.Firefox_*'))
    roots.append(Path.home()/'.mozilla'/'firefox')
    candidates = []
    for root in roots:
        config = configparser.ConfigParser()
        config.read(root/'profiles.ini',encoding='utf-8')
        preferred, others = [], []
        for section in config.sections():
            if section.startswith('Install') and config[section].get('Default'):
                preferred.append(root/config[section]['Default'])
            elif section.startswith('Profile') and config[section].get('Path'):
                path = Path(config[section]['Path'])
                if config[section].get('IsRelative','1') == '1':
                    path = root/path
                (preferred if config[section].get('Default') == '1' else others).append(path)
        found = [p for p in preferred if (p/'cookies.sqlite').is_file()]
        candidates.extend(found or [p for p in others if (p/'cookies.sqlite').is_file()])
    candidates = list(dict.fromkeys(p.resolve() for p in candidates))
    if len(candidates) != 1:
        raise FileNotFoundError('Choose the Firefox profile folder explicitly')
    return candidates[0]


def firefox_yandex_cookies(profile=''):
    path = firefox_profile(profile)/'cookies.sqlite'
    # Open the live database read-only so committed WAL data remains visible.
    connection = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    jar = YoutubeDLCookieJar()
    try:
        columns = {r[1] for r in connection.execute('PRAGMA table_info(moz_cookies)')}
        query = "SELECT host,name,value,path,expiry,isSecure FROM moz_cookies WHERE (host='yandex.ru' OR host LIKE '%.yandex.ru')"
        if 'originAttributes' in columns:
            query += " AND originAttributes=''"
        for row in connection.execute(query):
            domain = row['host'].lstrip('.').lower()
            if domain != 'yandex.ru' and not domain.endswith('.yandex.ru'):
                continue
            jar.set_cookie(Cookie(0,row['name'],row['value'],None,False,row['host'],True,
                row['host'].startswith('.'),row['path'] or '/',True,bool(row['isSecure']),
                row['expiry'] or None,False,None,None,{},False))
    finally:
        connection.close()
    return jar


def session_read_error(error):
    # Classify without exposing raw paths, cookie values or encrypted contents.
    pieces, pending, seen = [], [error], set()
    while pending and len(seen)<12:
        current=pending.pop()
        if id(current) in seen:continue
        seen.add(id(current));pieces.append(str(current).lower())
        for key in ('__cause__','__context__','cause'):
            child=getattr(current,key,None)
            if isinstance(child,BaseException):pending.append(child)
    detail=' '.join(pieces)
    if any(x in detail for x in ('not found','missing','could not find','choose the firefox','no such file')):
        return 'Профиль браузера не найден. В настройках Яндекса укажи папку профиля, где выполнен вход. Для Firefox её можно найти на странице about:profiles → Корневой каталог.'
    if any(x in detail for x in ('permission','denied','locked','could not copy','busy')):
        return 'Профиль браузера занят или недоступен для чтения. Полностью закрой браузер, запусти TwitchTune под тем же пользователем Windows и повтори. Можно указать папку профиля вручную.'
    if any(x in detail for x in ('decrypt','dpapi','keyring','app-bound','encrypted')):
        return 'Не удалось расшифровать сессию этого браузера. Попробуй Firefox с входом в Яндекс либо локальный файл cookies.txt. Сессия другого пользователя Windows не подходит.'
    return 'Не удалось прочитать выбранный профиль браузера. Укажи папку профиля вручную и нажми «Проверить вход» либо выбери локальный cookies.txt.'
