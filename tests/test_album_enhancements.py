import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from albums import create_album
from album_covers import load_album_cover
from media import Downloader
from yandex_direct import YandexAccessError
from test_albums import album_metadata
from PySide6.QtGui import QImage,QColor

class AlbumEnhancementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=QueueStore(self.root/'q.db')
        meta=album_metadata()
        for row in meta['tracks']:row.update(artist='Artist',track=row['title'])
        meta['cover_url']='https://example.com/album.jpg'
        self.album,self.ids=create_album(self.store,self.root/'Альбомы',meta,'viewer')
        self.down=object.__new__(Downloader)
        self.down.store,self.down.folder,self.down.stop,self.down.emit=self.store,self.root/'tracks',Event(),lambda *_:None
        self.row=self.store.get(self.ids[0])
        self.candidate=dict(title='Artist Track 1',channel='Artist',duration=101,url='https://www.youtube.com/watch?v=good')
    def tearDown(self):self.store.db.close();self.temp.cleanup()

    def test_blocked_yandex_album_track_gets_exact_automatic_replacement(self):
        wrong=dict(self.candidate,title='Artist Track 1 Remix',url='https://www.youtube.com/watch?v=wrong')
        with patch.object(self.down,'_download',side_effect=[YandexAccessError('Трек недоступен'),None]) as download, \
                patch('media.search',return_value=[wrong,self.candidate]):
            self.down.download(self.row)
            self.assertEqual(download.call_count,2)
            replacement=download.call_args.args[0]
            self.assertEqual(replacement['replacement'],self.candidate['url'])
            self.assertEqual((replacement['artist'],replacement['track'],replacement['duration']),('Artist','Track 1',101))
        self.assertEqual(len(self.store.rows()),1)
        self.assertEqual(len(self.store.album_tracks(self.album)),2)

    def test_no_confident_match_does_not_substitute_wrong_recording(self):
        with patch.object(self.down,'_download',side_effect=YandexAccessError('Нет доступа')), \
                patch('media.search',return_value=[dict(self.candidate,duration=50)]):
            with self.assertRaisesRegex(YandexAccessError,'Не найдена точная замена'):self.down.download(self.row)
        self.assertEqual(self.store.get(self.row['id'])['replacement'],'')

    def test_limits_and_single_track_errors_do_not_trigger_album_fallback(self):
        for message in ('Трек длиннее лимита награды','Файл больше 100 МБ.','Загрузка отменена.'):
            with patch.object(self.down,'_download',side_effect=ValueError(message)),patch('media.search') as search:
                with self.assertRaises(ValueError):self.down.download(self.row)
                search.assert_not_called()
        ident=self.store.add('https://music.yandex.ru/album/123/track/99')
        with patch.object(self.down,'_download',side_effect=YandexAccessError('Нет доступа')),patch('media.search') as search:
            with self.assertRaises(YandexAccessError):self.down.download(self.store.get(ident))
            search.assert_not_called()

    def test_cancel_during_search_cannot_revive_album(self):
        parent=self.store.album_request(self.album)
        def cancel(query):
            self.store.delete(parent['id'])
            return [self.candidate]
        with patch.object(self.down,'_download',side_effect=YandexAccessError('Нет доступа')),patch('media.search',side_effect=cancel):
            self.down.download(self.row)
        self.assertIsNone(self.store.get(self.row['id']))

    def test_downloaded_replacement_preserves_identity_and_checks_actual_duration(self):
        self.store.update(self.row['id'],replacement=self.candidate['url'])
        def fake_ydl(opts):
            ydl=MagicMock();path=Path(opts['outtmpl'].replace('%(ext)s','mp3'))
            def extract(*args,**kwargs):
                path.write_bytes(b'fake-complete-audio')
                return dict(title='YouTube upload',filepath=str(path),duration=101)
            ydl.extract_info.side_effect=extract;ydl.prepare_filename.return_value=str(path)
            ydl.__enter__.return_value=ydl;return ydl
        with patch('media.YoutubeDL',side_effect=fake_ydl),patch('media.validate_full_track') as validate,patch('media.download_cover',return_value=''):
            self.down.download(self.store.get(self.row['id']))
        row=self.store.get(self.row['id'])
        self.assertEqual((row['title'],row['artist'],row['track']),('Artist — Track 1','Artist','Track 1'))
        self.assertEqual(row['status'],'Готово')
        self.assertEqual(validate.call_args.args[1],101)

    def test_album_cover_is_saved_and_not_overwritten_by_track_cover(self):
        parent=self.store.album_request(self.album)
        self.assertEqual(parent['cover_url'],'https://example.com/album.jpg')
        def download(info,folder,ident,**kwargs):
            path=Path(folder)/(ident+'.cover.png')
            image=QImage(32,32,QImage.Format_RGB32);image.fill(QColor('green'));image.save(str(path))
            return str(path)
        with patch('album_covers.download_cover',side_effect=download),patch('album_covers.catalog') as catalog:
            path=load_album_cover(self.store,self.root/'tracks',parent)
            catalog.assert_not_called()
        self.assertTrue(Path(path).is_file())
        for ident in self.ids:self.store.update(ident,status='Готово',path='cache.mp3',cover_path='track.png')
        self.store.update_album_progress(self.album)
        self.assertEqual(self.store.album_request(self.album)['cover_path'],path)

    def test_old_album_reuses_track_cover_when_catalog_art_is_unavailable(self):
        parent=self.store.album_request(self.album)
        self.store.update(parent['id'],cover_url='')
        cover=Path(parent['album_folder'])/'existing.png';cover.write_bytes(b'oldcover')
        self.store.update(self.ids[0],cover_path=str(cover))
        with patch('album_covers.catalog',return_value=dict(tracks=[])) as catalog,patch('album_covers.download_cover') as download:
            result=load_album_cover(self.store,self.root/'tracks',self.store.get(parent['id']))
            self.assertEqual(result,str(cover));catalog.assert_called_once();download.assert_not_called()
