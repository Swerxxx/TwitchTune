import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from threading import Event
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from limits import MAX_TRACK_SECONDS,MAX_TRACK_BYTES
from media import metadata_filter,options,Downloader
from local_audio import import_mp3
from spotify_support import parse_spotify_embed,SpotifyError


class LongTrackTests(unittest.TestCase):
    def test_ninety_minutes_is_inclusive_and_short_reward_limits_still_apply(self):
        self.assertEqual(MAX_TRACK_SECONDS,5400)
        for duration in (901,3600,5399,5400):self.assertIsNone(metadata_filter({'duration':duration}))
        self.assertIsNotNone(metadata_filter({'duration':5401}))
        self.assertIsNotNone(metadata_filter({'duration':121},max_seconds=120))
        self.assertIsNone(metadata_filter({'duration':120},max_seconds=120))
        self.assertEqual(options()['max_filesize'],MAX_TRACK_BYTES)
        self.assertIsNone(metadata_filter({'duration':5400,'filesize':MAX_TRACK_BYTES}))
        self.assertIsNotNone(metadata_filter({'duration':5400,'filesize':MAX_TRACK_BYTES+1}))

    def test_legacy_default_is_lifted_but_twitch_reward_and_its_album_children_stay_limited(self):
        from test_albums import album_metadata
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'q.db';store=QueueStore(path)
            manual=store.add('https://youtu.be/manual',max_duration=900)
            paid=store.add('https://youtu.be/paid',redemption='donation:donationalerts:account:old',max_duration=900)
            reward=store.add('https://youtu.be/reward',redemption='reward-id',max_duration=900,reward_title='Музыка до 15 мин')
            short=store.add('https://youtu.be/short',max_duration=120,reward_title='До 2 мин')
            album,ids=store.add_album(album_metadata(),Path(folder)/'album',redemption='reward-album',max_duration=900,reward_title='До 15 мин')
            store.db.close();store=QueueStore(path)
            try:
                self.assertEqual(store.get(manual)['max_duration'],5400)
                self.assertEqual(store.get(paid)['max_duration'],5400)
                self.assertEqual(store.get(reward)['max_duration'],900)
                self.assertEqual(store.get(short)['max_duration'],120)
                self.assertTrue(all(store.get(i)['max_duration']==900 for i in ids))
                self.assertEqual(store.get(store.add('https://youtu.be/new'))['max_duration'],5400)
                with self.assertRaises(ValueError):store.add('https://youtu.be/too-long',max_duration=5401)
            finally:store.db.close()

    def test_local_mp3_long_duration_is_supported_and_longer_file_keeps_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'long.mp3';source.write_bytes(b'offline fixture')
            store=QueueStore(root/'q.db')
            try:
                with patch('local_audio.MP3',return_value=SimpleNamespace(info=SimpleNamespace(length=5400),tags=None)):
                    ident=import_mp3(store,root/'tracks',source)
                self.assertEqual(store.get(ident)['duration'],5400)
                with patch('local_audio.MP3',return_value=SimpleNamespace(info=SimpleNamespace(length=5401),tags=None)):
                    with self.assertRaisesRegex(ValueError,'90 минут'):import_mp3(store,root/'tracks',source)
                self.assertEqual(source.read_bytes(),b'offline fixture')
            finally:store.db.close()

    def test_spotify_metadata_accepts_ninety_minute_recordings(self):
        def page(seconds):
            entity=dict(type='track',uri='spotify:track:Test123',title='Long recording',artists=[dict(name='Artist')],duration=seconds*1000)
            data=dict(props=dict(pageProps=dict(state=dict(data=dict(entity=entity)))))
            return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'
        self.assertEqual(parse_spotify_embed(page(5400),'Test123')['duration'],5400)
        with self.assertRaises(SpotifyError):parse_spotify_embed(page(5401),'Test123')

    def test_download_pipeline_accepts_ninety_minutes_and_rejects_one_second_over(self):
        for seconds in (5400,5401):
            with self.subTest(seconds=seconds),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);store=QueueStore(root/'q.db')
                ident=store.add('https://youtu.be/longrecording')
                audio=root/(ident+'.mp3');audio.write_bytes(b'offline placeholder')
                down=Downloader.__new__(Downloader)
                down.store,down.folder,down.stop,down.emit=store,root,Event(),Mock()
                ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
                ydl.prepare_filename.return_value=str(audio)
                def factory(opts):
                    def extract(*args,**kwargs):
                        info=dict(title='Long recording',duration=seconds)
                        reason=opts['match_filter'](info,incomplete=False)
                        if reason:raise ValueError(reason)
                        return info
                    ydl.extract_info.side_effect=extract;return ydl
                try:
                    with patch('media.YoutubeDL',side_effect=factory),patch('media.download_cover',return_value=''):
                        if seconds==5400:
                            down.download(store.get(ident))
                            self.assertEqual(store.get(ident)['status'],'Готово')
                            self.assertEqual(store.get(ident)['duration'],5400)
                        else:
                            with self.assertRaisesRegex(ValueError,'90:00'):down.download(store.get(ident))
                            self.assertNotEqual(store.get(ident)['status'],'Готово')
                finally:store.db.close()
