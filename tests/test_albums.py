import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from albums import album_reference,catalog,create_album,safe_name
from track_files import track_folder


def album_metadata():
    return dict(url='https://music.yandex.ru/album/123',title='Album',artist='Artist',year='2026',source='Яндекс Музыка',
                tracks=[dict(url=f'https://music.yandex.ru/album/123/track/{i}',title=f'Track {i}',duration=100+i) for i in (1,2)])

class AlbumStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.path=self.root/'queue.sqlite3';self.store=QueueStore(self.path)
    def tearDown(self):self.store.db.close();self.temp.cleanup()
    def ready(self, ids):
        for ident in ids:
            row=self.store.get(ident);path=Path(row['album_folder'])/(ident+'.mp3');path.write_bytes(b'fixture')
            self.store.update(ident,status='Готово',path=str(path))
        self.store.update_album_progress(self.store.get(ids[0])['album_id'])

    def test_album_is_one_request_vip_slot_and_one_rating_after_all_tracks(self):
        self.store.grant_vip('viewer')
        album,ids=create_album(self.store,self.root/'Альбомы',album_metadata(),'viewer')
        self.assertEqual(len(self.store.rows()),1)
        self.assertEqual(len(self.store.album_tracks(album)),2)
        self.assertEqual(self.store.profiles()[0]['total'],1)
        self.assertEqual(self.store.profiles()[0]['vip_used'],1)
        self.assertEqual(self.store.album_request(album)['status'],'Загрузка альбома')
        self.ready(ids)
        parent=self.store.album_request(album)
        self.assertEqual(parent['status'],'Готово')
        self.assertEqual(parent['duration'],203)
        self.store.finish(parent['id'])
        self.assertEqual(self.store.rows('history'),[])
        self.assertEqual(self.store.get(parent['id'])['status'],'Ждёт оценки')
        self.store.rate_criteria(parent['id'],dict(text=6,beat=6,flow=6,vibe=6,mix=6))
        self.assertEqual(len(self.store.rows('history')),1)
        self.assertEqual(self.store.profiles()[0]['rated'],1)
        self.assertTrue(all(r['rating'] is None for r in self.store.album_tracks(album)))
        with self.assertRaises(ValueError):
            self.store.rate_criteria(ids[0],dict(text=6,beat=6,flow=6,vibe=6,mix=6))
        self.store.db.close();self.store=QueueStore(self.path)
        self.assertEqual(self.store.rows('history')[0]['rating'],66)
        self.assertEqual(len(self.store.album_tracks(album)),2)

    def test_reward_limit_refresh_updates_old_album_children_and_survives_restart(self):
        from media import metadata_filter
        meta=album_metadata(); meta['tracks'][0]['duration']=3600
        album, ids=create_album(self.store,self.root/'Альбомы',meta,redemption='redeemed',
                               max_duration=900,reward_title='Альбом до 15 минут')
        self.store.update(ids[0],status='Ошибка Яндекса',error='Трек длиннее 15 минут')
        self.store.update_album_progress(album)
        reward=dict(id='album-reward',title='Альбом до 90 минут',limit=5400)
        self.assertEqual(self.store.sync_reward_limits([reward]),1)
        for row in [self.store.album_request(album)] + self.store.album_tracks(album):
            self.assertEqual(row['max_duration'],5400)
            self.assertEqual(row['reward_id'],'album-reward')
        self.assertIsNone(metadata_filter({'duration':3600},max_seconds=self.store.get(ids[0])['max_duration']))
        self.store.db.close(); self.store=QueueStore(self.path)
        self.assertEqual(self.store.get(ids[0])['max_duration'],5400)
        # Once ID is saved, renames no longer interfere with matching.
        self.store.sync_reward_limits([dict(reward,title='Новый альбом',limit=720)])
        self.assertEqual(self.store.get(ids[0])['max_duration'],720)
        self.assertIsNotNone(metadata_filter({'duration':3600},max_seconds=720))

    def test_reward_sync_does_not_guess_ambiguous_legacy_titles_or_change_manual(self):
        album, ids=create_album(self.store,self.root/'Альбомы',album_metadata(),redemption='r1',
                               max_duration=900,reward_title='Альбом до 15 минут')
        manual=self.store.add('https://youtu.be/manual',max_duration=123)
        other=self.store.add('https://youtu.be/other',redemption='r2',max_duration=300,
                             reward_title='Другой трек',reward_id='other')
        rewards=[dict(id='a',title='Альбом до 90 минут',limit=5400),dict(id='b',title='Альбом до 5 минут',limit=300)]
        self.assertEqual(self.store.sync_reward_limits(rewards),0)
        self.assertEqual(self.store.get(ids[0])['max_duration'],900)
        self.assertEqual(self.store.get(manual)['max_duration'],123)
        self.assertEqual(self.store.get(other)['max_duration'],300)

    def test_partial_download_error_never_marks_whole_album_ready(self):
        album,ids=create_album(self.store,self.root/'Альбомы',album_metadata())
        self.ready(ids[:1])
        self.assertEqual(self.store.album_request(album)['status'],'Загрузка альбома')
        self.store.update(ids[1],status='Ошибка Яндекса',error='Нет доступа')
        self.store.update_album_progress(album)
        self.assertEqual(self.store.album_request(album)['status'],'Ошибка альбома')
        self.assertIn('1/2',self.store.album_request(album)['error'])
        self.ready(ids)
        self.assertEqual(self.store.album_request(album)['status'],'Готово')

    def test_twitch_duplicate_cancel_and_same_album_from_different_viewers(self):
        meta=album_metadata()
        album,ids=create_album(self.store,self.root/'Альбомы',meta,'first',redemption='r1',viewer_id='1',max_duration=240)
        self.assertEqual(self.store.get(ids[0])['max_duration'],240)
        self.assertEqual(create_album(self.store,self.root/'Альбомы',meta,'first',redemption='r1'),(None,[]))
        second,_=create_album(self.store,self.root/'Альбомы',meta,'second',redemption='r2')
        self.assertNotEqual(second,album)
        self.assertEqual(len(self.store.rows()),2)
        parent=self.store.album_request(album)
        self.store.cancel_redemption('r1')
        self.assertIsNone(self.store.get(parent['id']))
        self.assertTrue(all(self.store.get(i) is None for i in ids))
        self.assertEqual(self.store.album_tracks(album),[])
        self.store.cancel_redemption('r3')
        self.assertEqual(create_album(self.store,self.root/'Альбомы',meta,'third',redemption='r3'),(None,[]))
        self.assertEqual(len(list((self.root/'Альбомы').iterdir())),2) # Existing albums only; cancelled pending catalog creates no folder.

    def test_track_finishes_only_after_rating_before_or_after_playback(self):
        first=self.store.add('https://youtu.be/a')
        self.store.finish(first,'Пропущено')
        self.assertEqual(self.store.get(first)['status'],'Ждёт оценки')
        self.store.rate_criteria(first,dict(beat=6,flow=6,vibe=6,mix=6),False)
        self.assertEqual(self.store.get(first)['status'],'Пропущено')
        second=self.store.add('https://youtu.be/b')
        self.store.rate(second,8)
        self.assertEqual(self.store.get(second)['status'],'Ожидает загрузки')
        self.store.finish(second)
        self.assertEqual(self.store.get(second)['status'],'Прослушано')

    def test_album_folder_is_bounded_and_cannot_escape_storage_root(self):
        meta=album_metadata();meta['title']='../../bad:album?*';meta['artist']='a/b'
        album,ids=create_album(self.store,self.root/'Альбомы',meta)
        folder=track_folder(self.root/'tracks',self.store.get(ids[0]))
        self.assertEqual(folder.parent,(self.root/'Альбомы').resolve())
        self.assertTrue((folder/'Об альбоме.txt').is_file())
        with self.assertRaises(ValueError):track_folder(self.root/'tracks',dict(album_folder=str(self.root)))
        self.assertNotIn('/',safe_name('../A/B'))

class AlbumCatalogTests(unittest.TestCase):
    def test_yandex_page_fallback_uses_existing_session(self):
        import requests
        with patch('albums.requests.get',side_effect=requests.ConnectionError()), \
                patch('albums.YoutubeDL') as ydl, patch('yandex_direct.attach_session') as attach:
            ydl.return_value.__enter__.return_value.extract_info.return_value=dict(title='Album',playlist_count=1,
                entries=[dict(url='https://music.yandex.ru/album/123/track/1')])
            result=catalog('https://music.yandex.ru/album/123',settings=dict(yandex_auth='firefox'))
            self.assertEqual(len(result['tracks']),1)
            attach.assert_called_once()

    def test_recognizes_only_album_paths_and_separates_track_links(self):
        for url in ('https://music.yandex.ru/album/123?utm_source=x','https://open.spotify.com/intl-ru/album/abc',
                    'https://soundcloud.com/artist/sets/album','https://www.youtube.com/playlist?list=OLAK5uy_test'):
            self.assertIsNotNone(album_reference(url),url)
        for url in ('https://music.yandex.ru/album/123/track/456','https://open.spotify.com/track/abc',
                    'https://example.com/album/123','https://music.yandex.ru.evil/album/123'):
            self.assertIsNone(album_reference(url),url)

    def test_yandex_multidisc_catalog_keeps_order(self):
        response=MagicMock();response.json.return_value={'result':dict(id=123,title='Album',artists=[{'name':'Artist'}],year=2026,
            volumes=[[dict(id=1,title='One',artists=[{'name':'Artist'}],durationMs=1000)],[dict(id=2,title='Two',durationMs=2000)]])}
        with patch('albums.requests.get',return_value=response):result=catalog('https://music.yandex.ru/album/123')
        self.assertEqual([r['url'] for r in result['tracks']],['https://music.yandex.ru/album/123/track/1','https://music.yandex.ru/album/123/track/2'])
        self.assertEqual(result['artist'],'Artist')

    def test_spotify_album_parses_track_links_and_rejects_partial_catalog(self):
        entity=dict(uri='spotify:album:abc',name='Album',subtitle='Artist',totalTracks=2,
                    trackList=[dict(uri='spotify:track:one',title='One',duration=120000),dict(uri='spotify:track:two',title='Two',duration=130000)])
        def response():
            r=MagicMock();r.text='<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'state':{'data':{'entity':entity}}}}})+'</script>';return r
        with patch('albums.requests.get',return_value=response()):result=catalog('https://open.spotify.com/album/abc')
        self.assertEqual(len(result['tracks']),2)
        entity['totalTracks']=3
        with patch('albums.requests.get',return_value=response()):
            with self.assertRaises(ValueError):catalog('https://open.spotify.com/album/abc')

    def test_soundcloud_and_youtube_use_playlist_extraction_without_audio(self):
        for url,link in [('https://soundcloud.com/a/sets/b','https://soundcloud.com/a/song'),
                         ('https://youtube.com/playlist?list=OLAK5uy_test','https://www.youtube.com/watch?v=one')]:
            with patch('albums.YoutubeDL') as ydl:
                ydl.return_value.__enter__.return_value.extract_info.return_value=dict(_type='playlist',title='Album',uploader='Artist',entries=[dict(url=link,title='One')])
                result=catalog(url)
                self.assertEqual(result['tracks'][0]['url'],link)
                self.assertFalse(ydl.call_args.args[0]['noplaylist'])
                self.assertTrue(ydl.call_args.args[0]['skip_download'])


class AlbumDownloadTests(unittest.TestCase):
    def test_audio_and_cover_use_album_folder(self):
        from media import Downloader
        from threading import Event
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();store=QueueStore(root/'q.db')
            try:
                metadata=album_metadata()
                for i,row in enumerate(metadata['tracks']):row['url']=f'https://youtu.be/track{i}'
                album,ids=create_album(store,root/'Альбомы',metadata)
                down=object.__new__(Downloader)
                down.store,down.folder,down.stop,down.emit=store,root/'tracks',Event(),lambda *_:None
                def fake_ydl(opts):
                    ydl=MagicMock();filename=Path(opts['outtmpl'].replace('%(ext)s','mp3'))
                    def extract(*args,**kwargs):
                        filename.write_bytes(b'offline-audio')
                        return dict(title='Downloaded',filepath=str(filename),duration=101)
                    ydl.extract_info.side_effect=extract
                    ydl.prepare_filename.return_value=str(filename)
                    ydl.__enter__.return_value=ydl
                    return ydl
                with patch('media.YoutubeDL',side_effect=fake_ydl),patch('media.download_cover',return_value='') as cover:
                    down.download(store.get(ids[0]))
                    row=store.get(ids[0])
                    self.assertEqual(Path(row['path']).parent,Path(row['album_folder']).resolve())
                    self.assertEqual(cover.call_args.args[1],Path(row['album_folder']).resolve())
                    self.assertTrue(Path(row['path']).is_file())
                    self.assertFalse((root/'tracks'/(ids[0]+'.mp3')).exists())
            finally:store.db.close()
