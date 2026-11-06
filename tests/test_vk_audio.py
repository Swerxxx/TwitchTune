"""Public VK audio routing; synthetic page fixtures, no account or network."""
import sys,json,tempfile,shutil,unittest
from pathlib import Path
from html import escape
from threading import Event
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yt_dlp import YoutubeDL
from core import QueueStore
from media import Downloader
from vk_audio import audio_record,vk_audio_id,playable_url,extract_vk_audio,TwitchTuneVKAudioIE,VKAudioError

URL='https://vk.ru/audio-2001062996_148062996'
STREAM='https://psv4.vkuseraudio.net/test.mp3'


def page(**changes):
    record=dict(id=148062996,owner_id=-2001062996,title='Test Song',artist='Test Artist',duration=120,url=STREAM)
    record.update(changes)
    return '<div data-audio="'+escape(json.dumps(record),quote=True)+'"></div>'


class VKAudioTests(unittest.TestCase):
    def test_exact_audio_id_and_domain(self):
        self.assertEqual(vk_audio_id(URL),'-2001062996_148062996')
        self.assertEqual(vk_audio_id(URL.replace('vk.ru','m.vk.com')+'?x=1'),'-2001062996_148062996')
        for url in ('https://example.com/audio1_2','https://vk.ru/video1_2','https://vk.ru/audios1','https://vk.ru/audio1_2extra'):
            self.assertEqual(vk_audio_id(url),'')
    def test_shared_audio_url_accepts_access_key_suffix(self):
        # Synthetic share key; never embed a user's actual access key in fixtures.
        signed='https://vk.ru/audio485456522_456239805_TestAccessKey123'
        self.assertEqual(vk_audio_id(signed),'485456522_456239805')
        self.assertTrue(TwitchTuneVKAudioIE.suitable(signed))
        for url in (signed+'/wrong',signed+'_extra',signed+'.bad'):
            self.assertFalse(vk_audio_id(url))
    def test_shared_key_survives_authenticated_mobile_and_desktop_requests(self):
        signed=URL+'_TestAccessKey123'
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',side_effect=['<html>New shell</html>',page()]) as fetch:
            result=extract_vk_audio(ydl,signed,download=False,settings={'vk_auth':'firefox'})
        self.assertEqual(result['id'],'-2001062996_148062996')
        self.assertEqual(fetch.call_count,2)
        self.assertTrue(all(call.args[0].endswith('_TestAccessKey123') for call in fetch.call_args_list))

    def test_page_selects_requested_id_not_related_song(self):
        data=audio_record(page(id=111)+page(),'-2001062996_148062996')
        self.assertEqual(data['track'],'Test Song');self.assertEqual(data['url'],STREAM)
        self.assertIsNone(audio_record(page(id=111),'-2001062996_148062996'))
    def test_login_screen_or_missing_metadata_is_not_a_song(self):
        for html in ('<h1>ВКонтакте: вход</h1>',page(artist=''),'<div data-audio="not json"></div>'):
            self.assertIsNone(audio_record(html,'-2001062996_148062996'))
    def test_legacy_records_and_html_entities(self):
        record=[148062996,-2001062996,STREAM,'Test &amp; Song','Test Artist',125]
        data=audio_record('<div data-audio="'+escape(json.dumps(record),quote=True)+'"></div>','-2001062996_148062996')
        self.assertEqual(data['track'],'Test & Song');self.assertEqual(data['duration'],125)
    def test_only_public_vk_audio_cdn_urls(self):
        self.assertTrue(playable_url(STREAM))
        for url in ('http://psv4.vkuseraudio.net/test.mp3','https://127.0.0.1/test.mp3','https://evil.com/a.mp3','https://vkuseraudio.net.evil.com/a.mp3','https://vk.com/audio_api_unavailable.mp3','https://u:p@vk.com/a.mp3','https://vk.com/login'):
            self.assertFalse(playable_url(url))
    def test_explicit_extractor_avoids_unsupported_url(self):
        notice=Mock()
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value=page()):
            result=extract_vk_audio(ydl,URL,download=False,metadata_notice=notice)
        self.assertEqual(result['id'],'-2001062996_148062996')
        self.assertEqual(result['url'],STREAM)
        self.assertEqual(result['artist'],'Test Artist');notice.assert_called_once()
    def test_unavailable_file_keeps_metadata_for_manual_version(self):
        notice=Mock()
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value=page(url='')):
            with self.assertRaises(VKAudioError) as error:extract_vk_audio(ydl,URL,download=False,metadata_notice=notice)
        self.assertIn('VK-A04',str(error.exception));notice.assert_called_once()
        self.assertEqual(notice.call_args.args[0]['track'],'Test Song')
    def test_failure_never_leaks_signed_stream_url(self):
        ydl=Mock();ydl.extract_info.side_effect=RuntimeError(STREAM+'?secret=private')
        with self.assertRaises(VKAudioError) as error:extract_vk_audio(ydl,URL)
        self.assertNotIn('secret',str(error.exception))
    def test_cancelled_request_does_not_read_page(self):
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch.object(TwitchTuneVKAudioIE,'_download_webpage') as fetch:
            with self.assertRaises(VKAudioError):extract_vk_audio(ydl,URL,cancelled=lambda:True)
        fetch.assert_not_called()
    def test_authenticated_download_rejects_preview_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store=QueueStore(Path(folder)/'queue.db')
            try:
                ident=store.add(URL,'Viewer')
                path=Path(folder)/(ident+'.mp3')
                shutil.copyfile(Path(__file__).parent/'fixtures'/'silence-6s.mp3',path)
                ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
                ydl.prepare_filename.return_value=str(path)
                down=Downloader.__new__(Downloader);down.store=store;down.folder=Path(folder);down.stop=Event();down.emit=Mock();down.settings=lambda:{'vk_auth':'firefox'}
                with patch('media.YoutubeDL',return_value=ydl),patch('media.extract_vk_audio',return_value={'title':'Test','duration':120}):
                    with self.assertRaises(VKAudioError) as error:down.download(store.get(ident))
                self.assertIn('фрагмент',str(error.exception))
                self.assertFalse(path.exists());self.assertNotEqual(store.get(ident)['status'],'Готово')
            finally:store.db.close()

    def test_downloader_routes_vk_audio_and_keeps_original_request(self):
        with tempfile.TemporaryDirectory() as folder:
            store=QueueStore(Path(folder)/'queue.db')
            try:
                shared_url=URL+'_TestAccessKey123'
                ident=store.add(shared_url,'Viewer',max_duration=150)
                path=Path(folder)/(ident+'.mp3')
                shutil.copyfile(Path(__file__).parent/'fixtures'/'silence-6s.mp3',path)
                ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
                ydl.prepare_filename.return_value=str(path)
                metadata=dict(title='Test Artist — Test Song',artist='Test Artist',track='Test Song',duration=120)
                def extract(*args,**kwargs):kwargs['metadata_notice'](metadata);return dict(metadata,id='test')
                down=Downloader.__new__(Downloader);down.store=store;down.folder=Path(folder);down.stop=Event();down.emit=Mock()
                with patch('media.YoutubeDL',return_value=ydl),patch('media.extract_vk_audio',side_effect=extract) as vk,patch('media.download_cover',return_value=''):
                    down.download(store.get(ident))
                vk.assert_called_once();ydl.extract_info.assert_not_called()
                row=store.get(ident)
                self.assertEqual(row['status'],'Готово');self.assertEqual(row['source'],'VK')
                self.assertEqual(row['url'],shared_url);self.assertEqual(row['viewer'],'Viewer');self.assertEqual(row['max_duration'],150)
                self.assertEqual(row['track'],'Test Song')
            finally:store.db.close()
