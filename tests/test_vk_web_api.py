"""Authenticated web API fallback on the HTML-shell failure seen in the report."""
import sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from http.cookiejar import CookieJar
from urllib.parse import parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yt_dlp import YoutubeDL
from vk_audio import extract_vk_audio,TwitchTuneVKAudioIE,VKAudioError
from vk_web_api import web_audio_record,token_value,VKWebError
from test_vk_audio import URL,STREAM
from test_vk_session import cookie

IDENT='-2001062996_148062996'
RAW=dict(id=148062996,owner_id=-2001062996,title='Test Song',artist='Test Artist',duration=120,url=STREAM)
TOKEN='DUMMY_TEST_TOKEN_NOT_REAL'


class VKWebAPITests(unittest.TestCase):
    def setUp(self):
        import vk_web_api,vk_audio
        vk_web_api._cache.clear();vk_web_api._blocked.clear();vk_audio._reload_after=0
    def extractor(self):
        jar=CookieJar();jar.set_cookie(cookie('.vk.ru','remixsid','DUMMY_SESSION'))
        extractor=Mock();extractor._downloader.cookiejar=jar;extractor.cancelled.return_value=False
        return extractor
    def test_shell_page_loads_exact_track_through_web_api(self):
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value='<html>VK app shell without data-audio</html>'),patch.object(TwitchTuneVKAudioIE,'reload_playback',side_effect=[{'data':{'access_token':TOKEN}},{'response':[RAW]}]) as request:
            result=extract_vk_audio(ydl,URL+'_TestAccessKey123',download=False,settings={'vk_auth':'firefox'})
        self.assertEqual(result['url'],STREAM)
        self.assertEqual(request.call_count,2)
        self.assertIn('/?act=web_token',request.call_args_list[0].args[0])
        self.assertIn('/method/audio.getById',request.call_args_list[1].args[0])
        params=parse_qs(request.call_args_list[1].args[2].decode())
        self.assertEqual(params['audios'],[IDENT+'_TestAccessKey123'])
        self.assertEqual(params['access_token'],[TOKEN])
        self.assertNotIn(TOKEN,request.call_args_list[1].args[0])
    def test_tokens_cached_per_session_only_and_kept_out_of_settings(self):
        extractor=self.extractor();extractor.reload_playback.side_effect=[{'data':{'access_token':TOKEN}},{'response':[RAW]},{'response':[RAW]}]
        first=web_audio_record(extractor,URL,IDENT);second=web_audio_record(extractor,URL,IDENT)
        self.assertEqual(first,second);self.assertEqual(extractor.reload_playback.call_count,3)
        extractor._downloader.cookiejar.set_cookie(cookie('.vk.ru','remixsid','OTHER_SESSION'))
        extractor.reload_playback.side_effect=[{'data':{'access_token':'SECOND_DUMMY_TOKEN'}},{'response':[RAW]}]
        web_audio_record(extractor,URL,IDENT)
        self.assertIn('web_token',extractor.reload_playback.call_args_list[-2].args[0])
    def test_wrong_id_is_not_substituted(self):
        extractor=self.extractor();extractor.reload_playback.side_effect=[{'data':{'access_token':TOKEN}},{'response':[dict(RAW,id=999)]}]
        with self.assertRaisesRegex(VKWebError,'VK-W08'):web_audio_record(extractor,URL,IDENT)
    def test_no_web_token_is_distinct_from_page_login(self):
        extractor=self.extractor();extractor.reload_playback.return_value={'type':'error','message':'SECRET_SESSION'}
        with self.assertRaisesRegex(VKWebError,'VK-W01') as error:web_audio_record(extractor,URL,IDENT)
        self.assertNotIn('SECRET_SESSION',str(error.exception))
        self.assertEqual(extractor.reload_playback.call_count,1)
    def test_api_errors_are_sanitized_and_have_actionable_codes(self):
        for code,expected in [(5,'VK-W03'),(6,'VK-W04'),(14,'VK-W05'),(15,'VK-W06'),(8,'VK-W07')]:
            with self.subTest(code=code):
                self.setUp();extractor=self.extractor()
                extractor.reload_playback.side_effect=[{'data':{'access_token':TOKEN}},{'error':{'error_code':code,'error_msg':'SECRET_TOKEN PRIVATE_URL'}}]
                with self.assertRaisesRegex(VKWebError,expected) as error:web_audio_record(extractor,URL,IDENT)
                self.assertNotIn('SECRET',str(error.exception));self.assertNotIn('PRIVATE',str(error.exception))
    def test_api_rate_limit_blocks_repeated_requests(self):
        extractor=self.extractor()
        extractor.reload_playback.side_effect=[{'data':{'access_token':TOKEN}},{'error':{'error_code':6}}]
        for _ in range(2):
            with self.assertRaisesRegex(VKWebError,'VK-W04'):web_audio_record(extractor,URL,IDENT)
        self.assertEqual(extractor.reload_playback.call_count,2)

    def test_cancelled_token_fetch_does_not_query_audio(self):
        extractor=self.extractor();stopped=[False];extractor.cancelled.side_effect=lambda:stopped[0]
        def request(*_):stopped[0]=True;return {'data':{'access_token':TOKEN}}
        extractor.reload_playback.side_effect=request
        with self.assertRaisesRegex(VKWebError,'отменена'):web_audio_record(extractor,URL,IDENT)
        self.assertEqual(extractor.reload_playback.call_count,1)
    def test_api_error_invalidates_cached_token(self):
        import vk_web_api
        extractor=self.extractor();extractor.reload_playback.side_effect=[{'data':{'access_token':TOKEN}},{'error':{'error_code':5}}]
        with self.assertRaises(VKWebError):web_audio_record(extractor,URL,IDENT)
        self.assertFalse(vk_web_api._cache)
    def test_masked_api_stream_is_decoded_without_legacy_reload_hashes(self):
        raw=dict(RAW,url='https://m.vk.ru/mp3/audio_api_unavailable.mp3?extra=DUMMY')
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value='<script>vk = {id:77};</script>'),patch.object(TwitchTuneVKAudioIE,'reload_playback',side_effect=[{'data':{'access_token':TOKEN}},{'response':[raw]}]) as request,patch('vk_audio.VKWallPostIE._unmask_url',return_value=STREAM):
            self.assertEqual(extract_vk_audio(ydl,URL,download=False)['url'],STREAM)
        self.assertEqual(request.call_count,2)
    def test_album_artwork_is_forwarded_to_download_pipeline(self):
        from vk_records import structured_records
        raw=dict(RAW,album={'thumb':{'photo_600':'https://example.com/test-cover.jpg'}})
        self.assertEqual(structured_records({'response':[raw]})[0]['coverUrl'],'https://example.com/test-cover.jpg')
