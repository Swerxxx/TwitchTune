"""Additional lyrics sources; no shared cookies between providers."""
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import math
from html.parser import HTMLParser
from urllib.parse import urlparse,urlencode,quote
from difflib import SequenceMatcher
import requests
from lyrics import LyricsError,plain_text,normalized,song_query


class GeniusHTML(HTMLParser):
    VOID={'br','img','input','hr','meta','link','source','wbr','area','base','embed','param','track','col'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.parts=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        active=self.stack[-1][1] if self.stack else False
        skip=self.stack[-1][2] if self.stack else False
        active=active or attrs.get('data-lyrics-container')=='true' or 'lyrics' in attrs.get('class','').split()
        skip=skip or attrs.get('data-exclude-from-selection')=='true' or tag in ('script','style','button')
        if active and not skip and tag in ('br','p','div'):
            self.parts.append('\n')
        if tag not in self.VOID:self.stack.append((tag,active,skip))
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in self.VOID:self.handle_endtag(tag)
    def handle_endtag(self,tag):
        for index in range(len(self.stack)-1,-1,-1):
            if self.stack[index][0]==tag:
                _,active,skip=self.stack[index]
                if active and not skip and tag in ('div','p'):self.parts.append('\n')
                del self.stack[index:];break
    def handle_data(self,text):
        if self.stack and self.stack[-1][1] and not self.stack[-1][2]:self.parts.append(text)
    def text(self):
        text=plain_text({'plainLyrics':''.join(self.parts)})
        text=re.sub(r'\n{3,}','\n\n',text)
        return re.sub(r'(?:\n|^)\d*\s*Embed\s*$','',text).strip()[:60_000]


def genius_url(url):
    if not isinstance(url,str):return ''
    parsed=urlparse(url)
    return url if parsed.scheme=='https' and parsed.hostname in ('genius.com','www.genius.com') and not parsed.username and not parsed.password and parsed.path.endswith('-lyrics') else ''


class GeniusProvider:
    def __init__(self):self.blocked_until=0
    def fetch(self,url,params=None,cancelled=lambda:False):
        if time.monotonic()<self.blocked_until:
            raise LyricsError('Genius временно недоступен.')
        deadline=time.monotonic()+15
        try:
            with requests.get(url,params=params,stream=True,timeout=(3,7),headers={'User-Agent':'TwitchTune/0.9 lyrics reader','Accept':'application/json,text/html'}) as response:
                if response.status_code in (429,503):
                    try:delay=float(response.headers.get('Retry-After','30'))
                    except (TypeError,ValueError):
                        try:
                            deadline_date=parsedate_to_datetime(response.headers.get('Retry-After',''))
                            if deadline_date.tzinfo is None:deadline_date=deadline_date.replace(tzinfo=timezone.utc)
                            delay=(deadline_date-datetime.now(timezone.utc)).total_seconds()
                        except (TypeError,ValueError,OverflowError):delay=30
                    if not math.isfinite(delay):delay=30
                    delay=max(1,delay)
                    self.blocked_until=time.monotonic()+delay
                    raise LyricsError('Genius временно занят.')
                if response.status_code==403:
                    self.blocked_until=time.monotonic()+60
                    raise LyricsError('Genius ограничил доступ к странице.')
                response.raise_for_status()
                data=bytearray()
                for chunk in response.iter_content(65536):
                    if cancelled():return ''
                    if time.monotonic()>deadline:raise LyricsError('Genius не ответил вовремя.')
                    data.extend(chunk)
                    if len(data)>3_000_000:raise LyricsError('Слишком большой ответ Genius.')
                return data.decode('utf-8',errors='replace')
        except requests.RequestException:
            self.blocked_until=max(self.blocked_until,time.monotonic()+15)
            raise LyricsError('Не удалось связаться с Genius.') from None
    def search(self,artist,title,duration=0,*,cancelled=lambda:False):
        if cancelled():return []
        try:
            data=json.loads(self.fetch('https://genius.com/api/search/song',{'q':(artist+' '+title).strip(),'per_page':5},cancelled))
        except ValueError:
            raise LyricsError('Genius вернул неверный ответ.') from None
        response=data.get('response',{}) if isinstance(data,dict) else {}
        if not isinstance(response,dict):raise LyricsError('Genius вернул неверный ответ.')
        hits=response.get('hits',[])
        if not hits:
            sections=response.get('sections',[])
            hits=[hit for section in (sections if isinstance(sections,list) else []) if isinstance(section,dict) and section.get('type')=='song' for hit in (section.get('hits') or [])]
        if not isinstance(hits,list):raise LyricsError('Genius вернул неверный ответ.')
        candidates=[]
        for hit in hits[:10]:
            record=hit.get('result',{}) if isinstance(hit,dict) else {}
            if not isinstance(record,dict):continue
            primary=record.get('primary_artist') or {}
            name,performer=record.get('title',''),primary.get('name','') if isinstance(primary,dict) else ''
            url=genius_url(record.get('url'))
            if not url or not isinstance(name,str) or not isinstance(performer,str):continue
            similarity=SequenceMatcher(None,normalized(title),normalized(name)).ratio()
            artist_match=SequenceMatcher(None,normalized(artist),normalized(performer)).ratio() if artist else 1
            if similarity<.72 or artist_match<.65:continue
            # Alternate live/remix/translation versions are not a match for the original.
            versions={'live','remix','cover','slowed','sped','translation','перевод','ремикс'}
            if (set(normalized(name).split()) & versions)!=(set(normalized(title).split()) & versions):continue
            candidates.append((similarity+artist_match,record,name,performer,url))
        results=[]
        for _,record,name,performer,url in sorted(candidates,key=lambda x:x[0],reverse=True)[:2]:
            if cancelled():return []
            parser=GeniusHTML()
            try:parser.feed(self.fetch(url,cancelled=cancelled))
            except LyricsError:
                if time.monotonic()<self.blocked_until:raise
                continue
            text=parser.text()
            if text:
                results.append(dict(id='genius:'+str(record.get('id',url)),trackName=name,artistName=performer,
                                    duration=0,plainLyrics=text,source='Genius',sourceUrl=url))
        return results


class LyricsOvhProvider:
    def __init__(self):self.blocked_until=0
    def search(self,artist,title,duration=0,*,cancelled=lambda:False):
        if not artist.strip() or not title.strip() or cancelled():return []
        versions={'live','remix','cover','slowed','sped','translation','перевод','ремикс'}
        if set(normalized(title).split())&versions:return []
        if time.monotonic()<self.blocked_until:raise LyricsError('Lyrics.ovh временно недоступен.')
        url='https://api.lyrics.ovh/v1/'+quote(artist.strip(),safe='')+'/'+quote(title.strip(),safe='')
        try:
            with requests.get(url,stream=True,timeout=(3,6),headers={'User-Agent':'TwitchTune lyrics reader','Accept':'application/json'},allow_redirects=False) as response:
                if response.status_code==404:return []
                if response.status_code in (403,429,503):
                    try:delay=float(response.headers.get('Retry-After','30'))
                    except (ValueError,TypeError):delay=30
                    if not math.isfinite(delay):delay=30
                    self.blocked_until=time.monotonic()+max(1,delay)
                    raise LyricsError('Lyrics.ovh временно недоступен.')
                response.raise_for_status()
                body=bytearray();deadline=time.monotonic()+10
                for chunk in response.iter_content(65536):
                    if cancelled():return []
                    body.extend(chunk)
                    if len(body)>1_000_000 or time.monotonic()>deadline:raise LyricsError('Не удалось прочитать Lyrics.ovh.')
                data=json.loads(body.decode('utf-8'))
                text=plain_text({'plainLyrics':data.get('lyrics') if isinstance(data,dict) else ''})[:60_000]
                if not text or cancelled():return []
                return [dict(id='ovh:'+artist+':'+title,artistName=artist,trackName=title,duration=0,
                             plainLyrics=text,source='Lyrics.ovh',sourceUrl=url)]
        except (requests.RequestException,ValueError,UnicodeError):
            self.blocked_until=max(self.blocked_until,time.monotonic()+15)
            raise LyricsError('Не удалось связаться с Lyrics.ovh.') from None


class YandexLyricsProvider:
    def search(self,row,settings,*,cancelled=lambda:False):
        if row.get('source')!='Яндекс Музыка' or not settings.get('yandex_auth') or cancelled():return []
        from source_metadata import yandex_reference
        from yandex_direct import attach_session
        from yt_dlp import YoutubeDL
        from yt_dlp.networking import Request
        from yt_dlp_plugins.extractor.yandex_music_v2 import _api_headers
        class Silent:
            def debug(self,*_):pass
            def warning(self,*_):pass
            def error(self,*_):pass
        try:
            ident,canonical=yandex_reference(row['url'])
            with YoutubeDL({'quiet':True,'no_warnings':True,'socket_timeout':7,'logger':Silent()},auto_init=False) as ydl:
                attach_session(ydl,settings)
                def read(url):
                    if cancelled():return ''
                    with ydl.urlopen(Request(url,headers=_api_headers())) as response:
                        data=response.read(1_000_001)
                    if len(data)>1_000_000:raise LyricsError('Слишком большой ответ Яндекса.')
                    return data.decode('utf-8-sig',errors='replace')
                text=''
                try:
                    supplement=json.loads(read('https://api.music.yandex.ru/tracks/'+ident+'/supplement'))
                    supplement=supplement.get('result',supplement)
                    lyrics=supplement.get('lyrics') or {}
                    if lyrics.get('hasRights',True):text=lyrics.get('fullLyrics') or lyrics.get('lyrics') or ''
                except Exception:pass
                if not text and not cancelled():
                    # Lyrics signing protocol from the public yandex-music-api SDK.
                    # This is an application protocol key, never a user's credential.
                    stamp=int(time.time())
                    signature=base64.b64encode(hmac.new(b'p93jhgh689SBReK6ghtw62',f'{ident}{stamp}'.encode(),hashlib.sha256).digest()).decode()
                    params=urlencode({'format':'TEXT','timeStamp':stamp,'sign':signature})
                    data=json.loads(read(f'https://api.music.yandex.ru/tracks/{ident}/lyrics?{params}'))
                    data=data.get('result',data)
                    download=data.get('downloadUrl','')
                    parsed=urlparse(download)
                    host=parsed.hostname or ''
                    if parsed.scheme=='https' and not parsed.username and (host.endswith('.yandex.net') or host.endswith('.yandex.ru')):
                        text=read(download)
                text=plain_text({'plainLyrics':text})
                if not text or cancelled():return []
                artist,title=song_query(row)
                return [dict(id='yandex:'+ident,trackName=title or row['title'],artistName=artist,duration=row.get('duration') or 0,
                             plainLyrics=text[:60_000],source='Яндекс Музыка',sourceUrl=canonical)]
        except Exception:
            # No cookie details, signed URLs or raw account errors leave this source.
            raise LyricsError('Текст Яндекса недоступен для выбранного входа.') from None
