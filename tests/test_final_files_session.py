import sys, sqlite3, tempfile, unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock,patch
from http.cookiejar import CookieJar
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from track_files import downloaded_audio_path
from core import QueueStore
from media import Downloader
from browser_session import firefox_yandex_cookies,session_read_error,firefox_profile
from yandex_direct import attach_session

class FileAndSessionTests(unittest.TestCase):
    def test_firefox_default_profile_comes_from_profiles_ini(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve()/'Mozilla'/'Firefox'
            profile=root/'Profiles'/'test.default-release'
            profile.mkdir(parents=True)
            (profile/'cookies.sqlite').write_bytes(b'profile discovery fixture')
            (root/'profiles.ini').write_text('[Profile0]\nName=default-release\nIsRelative=1\nPath=Profiles/test.default-release\nDefault=1\n',encoding='utf-8')
            with patch.dict('os.environ',{'APPDATA':folder,'LOCALAPPDATA':''}), patch('browser_session.Path.home',return_value=Path(folder).resolve()/'home'):
                self.assertEqual(firefox_profile(),profile.resolve())

    def test_actual_postprocessed_file_wins_over_predicted_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            ident='a'*32
            actual=Path(folder).resolve()/(ident+'.m4a');actual.write_bytes(b'audio')
            predicted=Path(folder).resolve()/(ident+'.mp4')
            self.assertEqual(downloaded_audio_path(folder,ident,{'requested_downloads':[{'filepath':str(actual)}]},str(predicted)),actual)
            self.assertEqual(downloaded_audio_path(folder,ident,{},str(predicted),[str(actual)]),actual)
            self.assertEqual(downloaded_audio_path(folder,ident,{},str(predicted)),actual)

    def test_partial_files_artwork_foreign_paths_and_ambiguity_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder,tempfile.TemporaryDirectory() as other:
            ident='a'*32
            for name in (ident+'.mp3.part',ident+'.cover.png',ident+'.info.json','b'*32+'.mp3'):
                (Path(folder).resolve()/name).write_bytes(b'not complete audio')
            self.assertIsNone(downloaded_audio_path(folder,ident,{},str(Path(folder).resolve()/(ident+'.mp3'))))
            outside=Path(other)/(ident+'.mp3');outside.write_bytes(b'audio')
            self.assertIsNone(downloaded_audio_path(folder,ident,{'filepath':str(outside)}))
            for ext in ('mp3','m4a'):(Path(folder).resolve()/(ident+'.'+ext)).write_bytes(b'audio')
            self.assertIsNone(downloaded_audio_path(folder,ident,{}))

    def pipeline(self,folder,info,limit=900):
        store=QueueStore(Path(folder).resolve()/'q.db')
        ident=store.add('https://soundcloud.com/artist/track',max_duration=limit)
        ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
        ydl.prepare_filename.return_value=str(Path(folder).resolve()/(ident+'.mp4'))
        down=Downloader.__new__(Downloader)
        down.store,down.folder,down.stop,down.emit=store,Path(folder).resolve(),Event(),Mock()
        return store,ident,ydl,down

    def test_postprocessor_path_reaches_ready_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            store,ident,ydl,down=self.pipeline(folder,{})
            try:
                actual=Path(folder).resolve()/(ident+'.opus');actual.write_bytes(b'processed audio')
                def make(opts):
                    def extract(*args,**kwargs):
                        opts['postprocessor_hooks'][0]({'status':'finished','info_dict':{'filepath':str(actual)}})
                        return {'title':'Track','duration':120}
                    ydl.extract_info.side_effect=extract;return ydl
                with patch('media.YoutubeDL',side_effect=make):down.download(store.get(ident))
                self.assertEqual(store.get(ident)['status'],'Готово')
                self.assertEqual(store.get(ident)['path'],str(actual))
            finally:store.db.close()

    def test_match_filter_keeps_limit_reason_when_extractor_returns_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            store,ident,ydl,down=self.pipeline(folder,{},limit=120)
            try:
                def make(opts):
                    def extract(*args,**kwargs):
                        info={'title':'Track','duration':180}
                        opts['match_filter'](info,incomplete=False)
                        return info
                    ydl.extract_info.side_effect=extract;return ydl
                with patch('media.YoutubeDL',side_effect=make),self.assertRaisesRegex(ValueError,'лимита заявки: 2:00'):
                    down.download(store.get(ident))
                self.assertNotEqual(store.get(ident)['status'],'Готово')
            finally:store.db.close()

    def test_firefox_fallback_only_reads_yandex_and_never_rewrites_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder).resolve()/'cookies.sqlite'
            db=sqlite3.connect(path)
            db.execute('CREATE TABLE moz_cookies(host TEXT,name TEXT,value TEXT,path TEXT,expiry INTEGER,isSecure INTEGER,originAttributes TEXT)')
            db.executemany('INSERT INTO moz_cookies VALUES(?,?,?,?,?,?,?)',[
                ('.yandex.ru','Session_id','FAKE_TEST_SESSION','/',2147483647,1,''),
                ('.example.org','other','UNRELATED','/',2147483647,1,''),
                ('.yandex.ru','Session_id','FAKE_CONTAINER_SESSION','/',2147483647,1,'^userContextId=1')])
            db.commit();db.close();before=path.read_bytes()
            cookies=list(firefox_yandex_cookies(folder))
            self.assertEqual([c.domain for c in cookies],['.yandex.ru'])
            self.assertEqual(cookies[0].value,'FAKE_TEST_SESSION')
            self.assertEqual(path.read_bytes(),before)
            ydl=Mock(cookiejar=CookieJar())
            with patch('yandex_direct.extract_cookies_from_browser',side_effect=PermissionError('blocked')) as standard:
                attach_session(ydl,{'yandex_auth':'firefox','yandex_profiles':{'firefox':folder}})
            self.assertEqual(standard.call_args.kwargs['profile'],folder)
            self.assertEqual([c.name for c in ydl.cookiejar],['Session_id'])

    def test_session_errors_are_specific_without_private_details(self):
        for error,expected in [(FileNotFoundError('missing PRIVATE_PATH'),'не найден'),(PermissionError('permission denied PRIVATE_PATH'),'занят'),(RuntimeError('DPAPI decrypt PRIVATE_TOKEN'),'расшифровать')]:
            message=session_read_error(error)
            self.assertIn(expected,message)
            self.assertNotIn('PRIVATE_',message)
