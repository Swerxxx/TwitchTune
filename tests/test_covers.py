import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtGui import QImage, QColor
from PySide6.QtCore import QBuffer, QIODevice
from covers import download_cover, image_url, MAX_BYTES
from core import QueueStore
from media import load_saved_cover
from track_files import remove_track_files
from spotify_support import parse_spotify_embed
import json


class CoverTests(unittest.TestCase):
    def png(self):
        image = QImage(64,32,QImage.Format_RGB32)
        image.fill(QColor('#c6f52b'))
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer,'PNG')
        return bytes(buffer.data())

    def response(self, data, length=None):
        result = Mock(headers={'Content-Length':str(length if length is not None else len(data))})
        result.iter_content.return_value = [data]
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=False)
        return result

    def test_image_download_validates_and_stores_readable_png(self):
        with tempfile.TemporaryDirectory() as folder, patch('covers.requests.get',return_value=self.response(self.png())):
            path = download_cover({'thumbnail':'https://images.example/cover'},folder,'a'*32)
            image = QImage(path)
            self.assertEqual((image.width(),image.height()),(64,32))
            self.assertEqual(Path(path).name,'a'*32+'.cover.png')
            self.assertFalse(list(Path(folder).glob('*.tmp')))
            remove_track_files(folder,'a'*32)
            self.assertFalse(Path(path).exists())

    def test_missing_corrupt_oversized_and_cancelled_artwork_are_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            for data,length in [(b'not an image',None),(self.png(),MAX_BYTES+1)]:
                with patch('covers.requests.get',return_value=self.response(data,length)):
                    self.assertEqual(download_cover({'thumbnail':'https://example.org/image'},folder,'a'*32),'')
            with patch('covers.requests.get') as get:
                self.assertEqual(download_cover({},folder,'a'*32),'')
                self.assertEqual(download_cover({'thumbnail':'https://example.org/image'},folder,'a'*32,cancelled=lambda:True),'')
                self.assertEqual(download_cover({'thumbnail':'file:///tmp/private'},folder,'a'*32),'')
                get.assert_not_called()
            self.assertFalse(list(Path(folder).iterdir()))

    def test_backfill_persists_artwork_without_redownloading_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            store=QueueStore(Path(folder)/'queue.db')
            try:
                ident=store.add('https://youtu.be/test')
                ydl=Mock()
                ydl.__enter__=Mock(return_value=ydl)
                ydl.__exit__=Mock(return_value=False)
                ydl.extract_info.return_value={'thumbnail':'https://images.example/cover'}
                with patch('media.YoutubeDL',return_value=ydl), patch('covers.requests.get',return_value=self.response(self.png())):
                    path=load_saved_cover(store,folder,store.get(ident))
                self.assertEqual(store.get(ident)['cover_path'],path)
                ydl.extract_info.assert_called_once_with('https://youtu.be/test',download=False)
                store.db.close()
                store=QueueStore(Path(folder)/'queue.db')
                self.assertTrue(Path(store.get(ident)['cover_path']).is_file())
            finally: store.db.close()

    def test_delete_during_cover_fetch_never_leaves_an_orphan(self):
        with tempfile.TemporaryDirectory() as folder:
            store=QueueStore(Path(folder)/'queue.db')
            try:
                ident=store.add('https://youtu.be/test')
                store.update(ident,cover_url='https://images.example/cover')
                response=self.response(self.png())
                def chunks(_):
                    store.delete(ident)
                    yield self.png()
                response.iter_content.side_effect=chunks
                with patch('covers.requests.get',return_value=response):
                    self.assertEqual(load_saved_cover(store,folder,store.get(ident)),'')
                self.assertFalse(list(Path(folder).glob(ident+'.*')))
            finally: store.db.close()

    def test_spotify_album_art_is_preferred_and_urls_are_validated(self):
        entity=dict(type='track',uri='spotify:track:abc',title='Song',artists=[{'name':'Artist'}],duration=120000,
                    visualIdentity={'image':[{'url':'https://images.example/small','width':64},{'url':'https://images.example/album','width':640}]})
        html='<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'state':{'data':{'entity':entity}}}}})+'</script>'
        info=parse_spotify_embed(html,'abc')
        self.assertEqual(image_url(info),'https://images.example/album')
        self.assertEqual(image_url({'thumbnail':'https://user:password@example.org/img'}),'')
