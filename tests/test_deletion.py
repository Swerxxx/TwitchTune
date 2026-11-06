import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from media import Downloader
from track_files import remove_track_files


class DeletionTests(unittest.TestCase):
    def test_delete_removes_rating_profile_and_prevents_redemption_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            store=QueueStore(Path(folder)/'db.sqlite')
            try:
                first=store.add('https://youtu.be/a','viewer','redemption-a')
                second=store.add('https://youtu.be/b','viewer','redemption-b')
                store.rate(first,10,'remove')
                store.rate(second,6,'keep')
                store.update(first,status='Прослушано')
                store.delete(first)
                self.assertIsNone(store.get(first))
                self.assertEqual(store.profiles()[0]['average'],6)
                self.assertEqual(store.profiles()[0]['total'],1)
                self.assertIsNone(store.add('https://youtu.be/a','viewer','redemption-a'))
                self.assertFalse(store.update_download(first,status='Готово',path='stale.mp3'))
                store.delete(second)
                self.assertEqual(store.rows(),[])
                self.assertEqual(store.profiles(),[])
            finally:store.db.close()

    def test_file_cleanup_is_restricted_to_managed_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'tracks';root.mkdir()
            ident='a'*32
            audio=root/(ident+'.mp3');audio.write_bytes(b'audio')
            partial=root/(ident+'.mp3.part');partial.write_bytes(b'partial')
            outside=Path(folder)/'personal.mp3';outside.write_bytes(b'keep')
            remove_track_files(root,ident,str(outside))
            self.assertFalse(audio.exists())
            self.assertFalse(partial.exists())
            self.assertTrue(outside.exists())

    def test_delete_during_download_cannot_leave_ready_row_or_orphan_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            store=QueueStore(Path(folder)/'db.sqlite')
            root=Path(folder)/'tracks';root.mkdir()
            try:
                ident=store.add('https://youtu.be/a')
                row=store.get(ident)
                audio=root/(ident+'.mp3')
                def extract(*args,**kwargs):
                    store.delete(ident)
                    audio.write_bytes(b'late data')
                    return dict(title='Track',duration=180)
                ydl=Mock()
                ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
                ydl.extract_info.side_effect=extract;ydl.prepare_filename.return_value=str(audio)
                downloader=Downloader.__new__(Downloader)
                downloader.store,downloader.folder,downloader.stop,downloader.emit=store,root,Event(),Mock()
                with patch('media.YoutubeDL',return_value=ydl):downloader.download(row)
                self.assertIsNone(store.get(ident))
                self.assertFalse(audio.exists())
            finally:store.db.close()


if __name__=='__main__':unittest.main()
