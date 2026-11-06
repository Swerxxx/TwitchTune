"""Source fallback and shared bounded cache for automatic and manual lyrics lookup."""
import threading
import time
import json
from collections import OrderedDict
from lyrics import LyricsError,exact_match,normalized,song_query,plain_text
from lyrics_sources import GeniusProvider,YandexLyricsProvider,LyricsOvhProvider


class LyricsResolver:
    def __init__(self,lrclib,settings=lambda:{},store=None):
        self.lrclib=lrclib;self.settings=settings
        self.genius=GeniusProvider();self.yandex=YandexLyricsProvider();self.ovh=LyricsOvhProvider();self.store=store
        self.cache=OrderedDict();self.lock=threading.Lock()
    def search(self,artist,title,duration=0,*,row=None,force=False,cancelled=lambda:False):
        if not title.strip():raise LyricsError('Укажи название песни.')
        row=row or {}
        artist,title=song_query({'artist':artist,'track':title})
        if not title:raise LyricsError('Укажи название песни.')
        key=(row.get('url','') if not artist else '',normalized(artist),normalized(title),round(duration or 0))
        if cancelled():return []
        try:saved=self.store.lyrics_cache_read(json.dumps(key,ensure_ascii=False)) if self.store else []
        except Exception:saved=[]
        if saved and not force:return [dict(record,cachedCopy=True) for record in saved]
        while not self.lock.acquire(timeout=.1):
            if cancelled():return []
        try:
            if cancelled():return []
            cached=self.cache.get(key)
            if not force and cached and cached[0]>time.monotonic():
                self.cache.move_to_end(key)
                if cached[2]:raise LyricsError(cached[2])
                return cached[1]
            errors=[];candidates=[]
            query_matches=tuple(map(normalized,(artist,title)))==tuple(map(normalized,song_query(row)))
            if row.get('source')=='Яндекс Музыка' and query_matches:
                try:
                    result=self.yandex.search(row,self.settings(),cancelled=cancelled)
                    if cancelled():return []
                    if result:return self.remember(key,result)
                except Exception:errors.append('Яндекс Музыка недоступен')
            if cancelled():return []
            try:
                candidates=self.lrclib.search(artist,title,duration,force=force,cancelled=cancelled)
                if cancelled():return []
                if any(plain_text(x) and (exact_match(x,artist,title,duration) or (not artist and normalized(x['trackName'])==normalized(title))) for x in candidates):
                    return self.remember(key,candidates)
            except Exception:errors.append('LRCLIB недоступен')
            if cancelled():return []
            try:
                result=self.ovh.search(artist,title,duration,cancelled=cancelled)
                if cancelled():return []
                if result:return self.remember(key,result+candidates)
            except Exception:errors.append('Lyrics.ovh недоступен')
            if cancelled():return []
            try:
                result=self.genius.search(artist,title,duration,cancelled=cancelled)
                if cancelled():return []
                if result:
                    return self.remember(key,result+candidates)
            except Exception:errors.append('Genius недоступен')
            if cancelled():return []
            if any(plain_text(record) for record in candidates):return self.remember(key,candidates)
            if saved:return [dict(record,cachedCopy=True) for record in saved]
            if candidates:return self.remember(key,candidates)
            message='Текст не найден в доступных источниках. Недоступны: '+', '.join(errors)+'. Попробуй позже или уточни название.' if errors else ''
            self.remember(key,[],message)
            if message:raise LyricsError(message)
            return []
        finally:self.lock.release()
    def remember(self,key,results,error=''):
        if self.store and any(plain_text(record) for record in results):
            try:self.store.lyrics_cache_write(json.dumps(key,ensure_ascii=False),results)
            except Exception:pass
        self.cache[key]=(time.monotonic()+(28800 if results else 30),results,error)
        self.cache.move_to_end(key)
        while len(self.cache)>100:self.cache.popitem(last=False)
        return results
