"""Offline source fallback tests. All lyrics below are synthetic test strings."""
import sys
import json
import time
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lyrics import LyricsError,LyricsClient
from lyrics_resolver import LyricsResolver
from lyrics_sources import GeniusHTML,GeniusProvider,YandexLyricsProvider,LyricsOvhProvider,genius_url
from core import QueueStore


def record(**kwargs):
    return dict(dict(trackName='Test Song',artistName='Test Artist',duration=120,
                     plainLyrics='Собственная тестовая строка',source='Genius'),**kwargs)


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.base=Mock();self.base.search.return_value=[]
        self.resolver=LyricsResolver(self.base,lambda:{'yandex_auth':'firefox'})
        self.resolver.genius=Mock();self.resolver.genius.search.return_value=[]
        self.resolver.yandex=Mock();self.resolver.yandex.search.return_value=[]
        self.resolver.ovh=Mock();self.resolver.ovh.search.return_value=[]
    def test_lrclib_busy_empty_network_and_invalid_response_use_genius(self):
        for failure in (LyricsError('busy'),RuntimeError('bad response'),None):
            with self.subTest(failure=failure):
                self.resolver.cache.clear()
                self.base.search.side_effect=failure
                self.resolver.genius.search.return_value=[record()]
                self.assertEqual(self.resolver.search('Test Artist','Test Song')[0]['source'],'Genius')
    def test_exact_lrclib_text_skips_genius(self):
        self.base.search.return_value=[record(source='LRCLIB')]
        self.assertEqual(self.resolver.search('Test Artist','Test Song')[0]['source'],'LRCLIB')
        self.resolver.genius.search.assert_not_called()
    def test_instrumental_without_text_still_tries_genius(self):
        self.base.search.return_value=[record(plainLyrics='',instrumental=True)]
        self.resolver.genius.search.return_value=[record()]
        self.assertEqual(self.resolver.search('Test Artist','Test Song')[0]['source'],'Genius')
    def test_session_cache_and_explicit_refresh(self):
        self.resolver.genius.search.return_value=[record()]
        self.resolver.search('Test Artist','Test Song')
        self.resolver.search('Test Artist','Test Song')
        self.resolver.genius.search.assert_called_once()
        self.resolver.search('Test Artist','Test Song',force=True)
        self.assertEqual(self.resolver.genius.search.call_count,2)
    def test_yandex_id_first_and_falls_back_after_denied_session(self):
        row=dict(source='Яндекс Музыка',url='https://music.yandex.ru/track/123',title='Test Artist — Test Song')
        self.resolver.yandex.search.return_value=[record(source='Яндекс Музыка')]
        self.assertEqual(self.resolver.search('Test Artist','Test Song',row=row)[0]['source'],'Яндекс Музыка')
        self.base.search.assert_not_called()
        self.resolver.yandex.search.side_effect=LyricsError('denied')
        self.resolver.genius.search.return_value=[record()]
        self.assertEqual(self.resolver.search('Test Artist','Test Song',row=row,force=True)[0]['source'],'Genius')
    def test_edited_query_does_not_return_original_yandex_track(self):
        row=dict(source='Яндекс Музыка',url='https://music.yandex.ru/track/123',title='Different Song')
        self.resolver.search('Test Artist','Test Song',row=row)
        self.resolver.yandex.search.assert_not_called()
    def test_cancellation_after_source_does_not_cache_or_start_next(self):
        stopped=[False]
        def lookup(*args,**kwargs):stopped[0]=True;return [record()]
        self.base.search.side_effect=lookup
        self.assertEqual(self.resolver.search('Test Artist','Test Song',cancelled=lambda:stopped[0]),[])
        self.assertFalse(self.resolver.cache)
        self.resolver.genius.search.assert_not_called()
    def test_safe_aggregate_error_and_short_negative_cache(self):
        self.base.search.side_effect=LyricsError('private cookie value')
        self.resolver.genius.search.side_effect=LyricsError('signed URL')
        for _ in range(2):
            with self.assertRaises(LyricsError) as error:self.resolver.search('Test Artist','Test Song')
            self.assertNotIn('cookie',str(error.exception));self.assertNotIn('signed',str(error.exception))
        self.base.search.assert_called_once()
    def test_lrclib_backoff_is_respected_while_genius_remains_available(self):
        self.resolver.lrclib=LyricsClient();self.resolver.lrclib.next_request=time.monotonic()+60
        self.resolver.genius.search.return_value=[record()]
        with patch('lyrics.requests.get') as network:
            self.assertEqual(self.resolver.search('Test Artist','Test Song',force=True)[0]['source'],'Genius')
            network.assert_not_called()

    def test_independent_ovh_source_can_supply_text_without_accessing_genius(self):
        self.base.search.side_effect=LyricsError('LRCLIB busy')
        self.resolver.ovh.search.return_value=[record(source='Lyrics.ovh')]
        self.assertEqual(self.resolver.search('Test Artist','Test Song')[0]['source'],'Lyrics.ovh')
        self.resolver.genius.search.assert_not_called()

    def test_positive_text_survives_restart_and_failed_refresh_without_overwriting_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'q.db';store=QueueStore(path)
            self.resolver.store=store;self.resolver.genius.search.return_value=[record()]
            self.resolver.search('Test Artist','Test Song',120,row={'url':'https://youtu.be/one'})
            store.db.close();store=QueueStore(path)
            try:
                resolver=LyricsResolver(self.base,store=store)
                resolver.genius=Mock();resolver.ovh=Mock()
                for provider in (resolver.lrclib,resolver.genius,resolver.ovh):provider.search.side_effect=LyricsError('offline')
                cached=resolver.search('Test Artist','Test Song',120,row={'url':'https://soundcloud.com/a/b'})
                self.assertTrue(cached[0]['cachedCopy'])
                resolver.genius.search.assert_not_called();resolver.ovh.search.assert_not_called()
                refreshed=resolver.search('Test Artist','Test Song',120,force=True)
                self.assertEqual(refreshed[0]['plainLyrics'],cached[0]['plainLyrics'])
                self.assertTrue(refreshed[0]['cachedCopy'])
                resolver.lrclib.search.side_effect=None
                resolver.lrclib.search.return_value=[record(plainLyrics='',instrumental=True)]
                retained=resolver.search('Test Artist','Test Song',120,force=True)
                self.assertEqual(retained[0]['plainLyrics'],cached[0]['plainLyrics'])
                resolver.lrclib.search.side_effect=LyricsError('offline')
                with self.assertRaises(LyricsError):resolver.search('Other Artist','Other Song',120)
            finally:store.db.close()

    def test_cache_write_failure_does_not_discard_found_text(self):
        store=Mock();store.lyrics_cache_read.return_value=[];store.lyrics_cache_write.side_effect=OSError('disk full')
        self.resolver.store=store;self.resolver.genius.search.return_value=[record()]
        self.assertEqual(self.resolver.search('Test Artist','Test Song')[0]['plainLyrics'],record()['plainLyrics'])


class OvhTests(unittest.TestCase):
    def response(self,status=200,body=None):
        response=Mock(status_code=status,headers={'Retry-After':'120'})
        response.iter_content.return_value=[json.dumps(body or {'lyrics':'[00:01]Собственная тестовая строка'}).encode()]
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        return context
    def test_plain_text_and_safe_url_encoding(self):
        with patch('lyrics_sources.requests.get',return_value=self.response()) as request:
            result=LyricsOvhProvider().search('Test / Artist','Test Song')
        self.assertEqual(result[0]['plainLyrics'],'Собственная тестовая строка')
        self.assertEqual(result[0]['source'],'Lyrics.ovh')
        self.assertIn('Test%20%2F%20Artist/Test%20Song',request.call_args.args[0])
        self.assertFalse(request.call_args.kwargs['allow_redirects'])
    def test_not_found_cooldown_and_versions_are_distinct(self):
        with patch('lyrics_sources.requests.get',return_value=self.response(404)):
            self.assertEqual(LyricsOvhProvider().search('Artist','Song'),[])
        provider=LyricsOvhProvider()
        with patch('lyrics_sources.requests.get',return_value=self.response(429)) as request:
            for _ in range(2):
                with self.assertRaises(LyricsError):provider.search('Artist','Song')
            request.assert_called_once()
        with patch('lyrics_sources.requests.get') as request:
            self.assertEqual(LyricsOvhProvider().search('Artist','Song (Remix)'),[])
            self.assertEqual(LyricsOvhProvider().search('','Song'),[])
            request.assert_not_called()


class GeniusTests(unittest.TestCase):
    def test_only_lyrics_containers_inline_words_and_linebreaks(self):
        parser=GeniusHTML()
        parser.feed('<h1>Ignore title</h1><div data-lyrics-container="true">[Verse]<br>My <a>own</a> line<br><span data-exclude-from-selection="true">Ad</span>Next</div><div data-lyrics-container="true">Last<br>12Embed</div><footer>Ignore</footer>')
        self.assertEqual(parser.text(),'[Verse]\nMy own line\nNext\n\nLast')
    def test_no_body_wide_fallback_or_challenge_bypass(self):
        parser=GeniusHTML();parser.feed('<html><body>Please verify access</body></html>')
        self.assertEqual(parser.text(),'')
    def test_search_filters_wrong_artist_versions_and_external_links(self):
        provider=GeniusProvider()
        def hit(title='Test Song',artist='Test Artist',url='https://genius.com/test-song-lyrics'):
            return {'result':{'id':1,'title':title,'primary_artist':{'name':artist},'url':url}}
        payload=json.dumps({'response':{'sections':[{'type':'song','hits':[
            hit(title='Test Song (Live)'),hit(artist='Nobody'),hit(url='https://example.com/song-lyrics'),hit()]}]}})
        with patch.object(provider,'fetch',side_effect=[payload,'<div data-lyrics-container="true">Test line</div>']) as fetch:
            results=provider.search('Test Artist','Test Song')
        self.assertEqual(len(results),1);self.assertEqual(results[0]['source'],'Genius')
        self.assertEqual(fetch.call_count,2)
    def test_failed_first_page_tries_second(self):
        provider=GeniusProvider()
        hit={'result':{'title':'Test Song','primary_artist':{'name':'Test Artist'},'url':'https://genius.com/test-song-lyrics'}}
        payload=json.dumps({'response':{'hits':[hit,hit]}})
        with patch.object(provider,'fetch',side_effect=[payload,LyricsError('timeout'),'<div class="lyrics">Test line</div>']):
            self.assertEqual(len(provider.search('Test Artist','Test Song')),1)
    def test_url_validation(self):
        for url in ('http://genius.com/a-lyrics','https://evil.com/a-lyrics','https://genius.com.evil.com/a-lyrics','https://user@genius.com/a-lyrics'):
            self.assertEqual(genius_url(url),'')
    def test_cooldown_never_retries_on_403_429_or_503_even_with_force(self):
        for status in (403,429,503):
            with self.subTest(status=status):
                provider=GeniusProvider()
                response=Mock(status_code=status,headers={'Retry-After':'120'})
                context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
                with patch('lyrics_sources.requests.get',return_value=context) as fetch:
                    for _ in range(2):
                        with self.assertRaises(LyricsError):provider.search('Test Artist','Test Song')
                fetch.assert_called_once();self.assertGreater(provider.blocked_until,time.monotonic()+50)


class YandexTests(unittest.TestCase):
    row=dict(source='Яндекс Музыка',url='https://music.yandex.ru/track/123',title='Test Artist — Test Song')
    def test_no_auth_means_no_session_read(self):
        with patch('yandex_direct.attach_session') as attach:
            self.assertEqual(YandexLyricsProvider().search(self.row,{}),[])
            attach.assert_not_called()
    def run_source(self,responses):
        ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
        streams=[]
        for data in responses:
            stream=Mock();stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock(return_value=False)
            stream.read.return_value=data.encode();streams.append(stream)
        ydl.urlopen.side_effect=streams
        with patch('yt_dlp.YoutubeDL',return_value=ydl),patch('yandex_direct.attach_session') as attach:
            rows=YandexLyricsProvider().search(self.row,{'yandex_auth':'firefox'})
            attach.assert_called_once_with(ydl,{'yandex_auth':'firefox'})
        return rows,ydl
    def test_legacy_supplement_cleaned_and_attributed(self):
        rows,ydl=self.run_source([json.dumps({'result':{'lyrics':{'hasRights':True,'fullLyrics':'[00:01.20]Test line'}}})])
        self.assertEqual(rows[0]['plainLyrics'],'Test line');self.assertEqual(rows[0]['source'],'Яндекс Музыка')
        self.assertIn('/tracks/123/supplement',ydl.urlopen.call_args.args[0].url)
    def test_modern_download_url_and_session_fallback(self):
        rows,ydl=self.run_source(['{}',json.dumps({'result':{'downloadUrl':'https://lyrics.storage.yandex.net/test.txt'}}),'Test modern line'])
        self.assertEqual(rows[0]['plainLyrics'],'Test modern line')
        self.assertIn('/tracks/123/lyrics?',ydl.urlopen.call_args_list[1].args[0].url)
    def test_foreign_download_url_is_rejected(self):
        rows,ydl=self.run_source(['{}',json.dumps({'result':{'downloadUrl':'https://example.com/private'}})])
        self.assertEqual(rows,[]);self.assertEqual(ydl.urlopen.call_count,2)
    def test_private_session_error_is_hidden(self):
        with patch('yandex_direct.attach_session',side_effect=RuntimeError('secret cookie')):
            with self.assertRaises(LyricsError) as error:YandexLyricsProvider().search(self.row,{'yandex_auth':'firefox'})
        self.assertNotIn('secret',str(error.exception))
