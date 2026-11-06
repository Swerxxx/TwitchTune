"""VK authenticated playback protocol tests using dummy local sessions only."""
import sys,tempfile,sqlite3,json,unittest,time
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock,patch
from http.cookiejar import CookieJar,Cookie
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yt_dlp import YoutubeDL
from vk_audio import extract_vk_audio,TwitchTuneVKAudioIE,VKAudioError
from vk_session import attach_vk_session,firefox_vk_cookies,check_vk_session,VKSessionError,vk_host,page_user_id
from test_vk_audio import URL,STREAM,page


def cookie(domain,name,value):
    return Cookie(0,name,value,None,False,domain,True,domain.startswith('.'),'/',True,True,None,False,None,None,{},False)


class VKSessionTests(unittest.TestCase):
    def setUp(self):
        import vk_audio
        vk_audio._reload_after=0

    def test_server_backoff_stops_followup_requests(self):
        import vk_audio
        cause=RuntimeError('private response')
        cause.status=429;cause.headers={'Retry-After':'120'}
        wrapper=RuntimeError('private URL');wrapper.cause=cause
        extractor=TwitchTuneVKAudioIE(Mock())
        with patch.object(extractor,'_download_json',side_effect=wrapper) as request:
            for _ in range(2):
                with self.assertRaises(VKAudioError):extractor.reload_playback('https://m.vk.ru/audio','1_2',b'','https://m.vk.ru/audio1_2')
        request.assert_called_once()
        self.assertGreater(vk_audio._reload_after,time.monotonic()+115)
        vk_audio._reload_after=0

    def test_authenticated_cookie_domain_wins_over_language_cookie(self):
        jar=CookieJar();jar.set_cookie(cookie('.vk.ru','remixlang','0'));jar.set_cookie(cookie('.vk.com','remixsid','DUMMY'))
        self.assertEqual(vk_host(Mock(cookiejar=jar)),'vk.com')
    def test_account_id_after_nested_config_is_recognized(self):
        for text in ('window.vk = {api: {id:999}, language: "{test}", id:77};', 'var vk = {"config":{"nested":[1,2]}, "id": 77};', 'vk.id = 77;'):
            self.assertEqual(page_user_id(text),77)
        self.assertEqual(page_user_id('vk = {api:{id:999}, id:0};'),0)
        self.assertEqual(page_user_id('var post = {id:77};'),0)
    def test_unknown_shell_remains_unconfirmed_and_offers_track_check(self):
        ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False);ydl.cookiejar=CookieJar()
        stream=Mock();stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock(return_value=False)
        stream.read.return_value=b'<div id="app">New VK shell</div>';stream.url='https://vk.ru/feed';ydl.urlopen.return_value=stream
        with patch('yt_dlp.YoutubeDL',return_value=ydl),patch('vk_session.attach_vk_session',return_value=True):
            result=check_vk_session({'vk_auth':'firefox'})
        self.assertIn('Сессия VK прочитана',result)
        self.assertIn('подтвердить вход',result)
        self.assertNotIn('Вход VK подтверждён',result)
        self.assertEqual(ydl.urlopen.call_count,2)
    def test_security_redirect_is_not_reported_as_confirmed(self):
        ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False);ydl.cookiejar=CookieJar()
        stream=Mock();stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock(return_value=False)
        stream.read.return_value=b'<script>vk = {id:77};</script>';stream.url='https://id.vk.com/auth';ydl.urlopen.return_value=stream
        with patch('yt_dlp.YoutubeDL',return_value=ydl),patch('vk_session.attach_vk_session',return_value=True),self.assertRaises(VKSessionError):check_vk_session({'vk_auth':'firefox'})

    def test_browser_selection_is_explicit_and_cookies_are_domain_scoped(self):
        ydl=Mock(cookiejar=CookieJar())
        with patch('vk_session.extract_cookies_from_browser') as read:
            self.assertFalse(attach_vk_session(ydl,{}));read.assert_not_called()
        jar=CookieJar()
        for domain,name,value in [('.vk.ru','remixsid','DUMMY'),('.yandex.ru','Session_id','UNRELATED'),('.evilvk.ru','evil','OTHER'),('.vk.com','remixuid','77')]:jar.set_cookie(cookie(domain,name,value))
        with patch('vk_session.extract_cookies_from_browser',return_value=jar) as read:
            self.assertTrue(attach_vk_session(ydl,{'vk_auth':'chrome','vk_profiles':{'chrome':'C:/dummy/Default'}}))
        self.assertEqual(read.call_args.kwargs['profile'],'C:/dummy/Default')
        self.assertEqual(sorted(c.name for c in ydl.cookiejar),['remixsid','remixuid'])
    def test_firefox_fallback_reads_only_vk_and_never_modifies_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cookies.sqlite'
            with sqlite3.connect(path) as connection:
                connection.execute('CREATE TABLE moz_cookies(host,name,value,path,expiry,isSecure,originAttributes)')
                connection.executemany('INSERT INTO moz_cookies VALUES(?,?,?,?,?,?,?)',[
                    ('.vk.ru','remixsid','DUMMY','/',9999999999,1,''),('.yandex.ru','Session_id','OTHER','/',9999999999,1,''),('.vk.ru','container','NO','/',9999999999,1,'userContextId=1')])
            before=path.read_bytes()
            self.assertEqual([x.name for x in firefox_vk_cookies(folder)],['remixsid'])
            self.assertEqual(path.read_bytes(),before)
            with patch('vk_session.extract_cookies_from_browser',side_effect=PermissionError('locked')):
                self.assertTrue(attach_vk_session(Mock(cookiejar=CookieJar()),{'vk_auth':'firefox','vk_profiles':{'firefox':folder}}))
    def test_missing_expired_or_unreadable_session_is_actionable_and_private(self):
        for failure in (RuntimeError('DPAPI PRIVATE'),PermissionError('denied PRIVATE')):
            with patch('vk_session.extract_cookies_from_browser',side_effect=failure),self.assertRaises(VKSessionError) as error:
                attach_vk_session(Mock(cookiejar=CookieJar()),{'vk_auth':'chrome'})
            self.assertNotIn('PRIVATE',str(error.exception))
        with patch('vk_session.extract_cookies_from_browser',return_value=CookieJar()),self.assertRaises(VKSessionError):
            attach_vk_session(Mock(cookiejar=CookieJar()),{'vk_auth':'chrome'})
    def test_playback_reload_uses_exact_id_session_and_covers(self):
        notice=Mock()
        initial=page(url='',actionHash='action',urlHash='hash')+'<script>vk = {id:77};</script>'
        updated=[148062996,-2001062996,STREAM,'Test Song','Test Artist',120,None,None,None,None,None,None,None,'','https://example.com/cover.png']
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value=initial) as get,patch.object(TwitchTuneVKAudioIE,'_download_json',return_value={'data':[[updated]]}) as post:
            result=extract_vk_audio(ydl,URL,download=False,settings={'vk_auth':'firefox'},metadata_notice=notice)
        self.assertIn('m.vk.',get.call_args.args[0])
        self.assertIn(b'act=reload_audio',post.call_args.kwargs['data'])
        self.assertIn(b'-2001062996_148062996_action_hash',post.call_args.kwargs['data'])
        self.assertEqual(result['url'],STREAM);self.assertEqual(result['thumbnails'][0]['url'],'https://example.com/cover.png')
    def test_reload_never_selects_a_different_song(self):
        initial=page(url='',actionHash='action',urlHash='hash')
        other=[999,-2001062996,STREAM,'Other','Artist',100]
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value=initial),patch.object(TwitchTuneVKAudioIE,'_download_json',return_value={'data':[[other]]}):
            with self.assertRaises(VKAudioError):extract_vk_audio(ydl,URL,download=False,settings={'vk_auth':'firefox'})
    def test_player_url_decoding_uses_current_account(self):
        masked='https://m.vk.ru/mp3/audio_api_unavailable.mp3?extra=DUMMY'
        updated=[148062996,-2001062996,masked,'Test Song','Test Artist',120]
        initial=page(url='',actionHash='action',urlHash='hash')+'<script>vk = {id:77};</script>'
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value=initial),patch.object(TwitchTuneVKAudioIE,'_download_json',return_value={'data':[[updated]]}),patch('vk_audio.VKWallPostIE._unmask_url',return_value=STREAM) as decode:
            self.assertEqual(extract_vk_audio(ydl,URL,download=False)['url'],STREAM)
        decode.assert_called_once_with(masked,77)
    def test_cancel_after_metadata_does_not_request_playback(self):
        stopped=[False]
        def notice(_):stopped[0]=True
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value=page(url='',actionHash='action',urlHash='hash')),patch.object(TwitchTuneVKAudioIE,'_download_json') as post:
            with self.assertRaises(VKAudioError):extract_vk_audio(ydl,URL,cancelled=lambda:stopped[0],metadata_notice=notice)
        post.assert_not_called()
    def test_session_check_requires_server_acceptance_not_just_cookie_presence(self):
        ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False);ydl.cookiejar=CookieJar()
        stream=Mock();stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock(return_value=False);ydl.urlopen.return_value=stream
        with patch('yt_dlp.YoutubeDL',return_value=ydl),patch('vk_session.attach_vk_session',return_value=True):
            stream.read.return_value=b'<script>vk = {id:77};</script>'
            self.assertIn('подтверждён',check_vk_session({'vk_auth':'firefox'}))
            stream.read.return_value=b'<form id="quick_login_form"></form>'
            with self.assertRaises(VKSessionError):check_vk_session({'vk_auth':'firefox'})
