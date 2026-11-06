import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from local_audio import import_mp3
from track_files import remove_track_files
from mutagen.id3 import TIT2,TPE1,APIC
from mutagen.mp3 import MP3
from PySide6.QtCore import QByteArray,QBuffer,QIODevice
from PySide6.QtGui import QImage,QColor

FIXTURE=Path(__file__).parent/'fixtures'/'silence-6s.mp3'

class LocalAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.source=self.root/'Своя песня.MP3'
        self.source.write_bytes(FIXTURE.read_bytes())
        self.cache=self.root/'tracks'
        self.store=QueueStore(self.root/'queue.sqlite3')
    def tearDown(self):
        self.store.db.close(); self.temp.cleanup()

    def test_private_copy_persistence_history_rating_and_delete_keep_original(self):
        original=self.source.read_bytes()
        ident=import_mp3(self.store,self.cache,self.source,'viewer')
        row=self.store.get(ident)
        self.assertEqual((row['source'],row['status'],row['viewer']),('MP3','Готово','viewer'))
        self.assertEqual(row['title'], 'Своя песня')
        self.assertGreater(row['duration'],5)
        self.assertEqual(Path(row['path']).read_bytes(),original)
        self.assertNotEqual(Path(row['path']),self.source)
        self.assertEqual(Path(row['path']).name,ident+'.mp3')
        self.store.rate_criteria(ident,dict(text=6,beat=6,flow=6,vibe=6,mix=6))
        self.store.update(ident,status='Прослушано')
        self.store.db.close();self.store=QueueStore(self.root/'queue.sqlite3')
        self.assertEqual(self.store.rows('history')[0]['rating'],66)
        self.store.delete(ident)
        remove_track_files(self.cache,ident,row['path'])
        self.assertFalse(Path(row['path']).exists())
        self.assertEqual(self.source.read_bytes(),original)

    def test_id3_tags_and_embedded_cover(self):
        audio=MP3(self.source)
        if audio.tags is None:audio.add_tags()
        audio.tags.add(TIT2(encoding=3,text='Название'))
        audio.tags.add(TPE1(encoding=3,text='Исполнитель'))
        image=QImage(8,8,QImage.Format_RGB32);image.fill(QColor('red'))
        data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.WriteOnly)
        image.save(buffer,'PNG');buffer.close()
        audio.tags.add(APIC(encoding=3,mime='image/png',type=3,data=bytes(data)))
        audio.save()
        ident=import_mp3(self.store,self.cache,self.source)
        row=self.store.get(ident)
        self.assertEqual((row['artist'],row['track'],row['title']),('Исполнитель','Название','Исполнитель — Название'))
        self.assertFalse(QImage(row['cover_path']).isNull())

    def test_invalid_cancelled_full_queue_and_copy_failures_leave_no_files(self):
        bad=self.root/'bad.mp3';bad.write_text('not audio')
        for path in (bad,self.root/'missing.mp3'):
            with self.assertRaises(ValueError):import_mp3(self.store,self.cache,path)
        with self.assertRaises(ValueError):import_mp3(self.store,self.cache,self.source,cancelled=lambda:True)
        with patch.object(self.store,'add_local',side_effect=ValueError('Очередь заполнена')):
            with self.assertRaises(ValueError):import_mp3(self.store,self.cache,self.source)
        self.assertEqual(list(self.cache.iterdir()),[])
        self.assertEqual(self.store.rows(),[])
        self.assertTrue(self.source.is_file())

    def test_local_import_respects_vip_and_distinct_imports(self):
        self.store.grant_vip('vip')
        self.store.add('https://youtu.be/example','ordinary')
        first=import_mp3(self.store,self.cache,self.source,'vip')
        second=import_mp3(self.store,self.cache,self.source,'vip')
        self.assertNotEqual(first,second)
        self.assertEqual([r['id'] for r in self.store.rows('active')][:2],[first,second])
        self.assertEqual(self.store.profiles()[0]['vip_used'],2)
