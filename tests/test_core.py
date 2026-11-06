import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import QueueStore, parse_link, telegram_delivery_note

class CoreTests(unittest.TestCase):
    def test_telegram_delivery_note_variants_and_url_boundaries(self):
        for text in ('в тг', 'скинул в тг', 'отправил в телеграмме', 'трек в телегу',
                     'В Т.Г.!', 'файл в telegram', 'в тг TT-INVALID',
                     'https://youtu.be/example — другой трек в тг'):
            self.assertTrue(telegram_delivery_note(text), text)
        for text in ('https://youtu.be/example', 'https://youtu.be/example?title=в%20тг',
                     'вторая песня', 'в тгшке', 'https://example.com/в-тг'):
            self.assertFalse(telegram_delivery_note(text), text)

    def test_parse_supported_sources(self):
        self.assertEqual(parse_link('https://youtu.be/abc')[1], 'YouTube')
        self.assertEqual(parse_link('https://open.spotify.com/track/abc123')[1], 'Spotify')
    def test_rejects_playlists_and_unknown(self):
        with self.assertRaises(ValueError): parse_link('https://youtube.com/playlist?list=x')
        with self.assertRaises(ValueError): parse_link('https://example.com/song')
    def test_persistent_and_duplicate_redemption(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'q.db'; s=QueueStore(p); one=s.add('https://youtu.be/abc','u','redemption')
            self.assertIsNotNone(one); self.assertIsNone(s.add('https://youtu.be/abc','u','redemption'))
            s.update(one,status='Готово',path='/tmp/a.mp3')
            other = QueueStore(p)
            self.assertEqual(other.get(one)['status'], 'Готово')
            other.db.close()
            s.db.close()

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'queue.db'
        self.store = QueueStore(self.path)
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.store.db.close)

    def test_average_excludes_unrated_and_updates_on_edit(self):
        a = self.store.add('https://youtu.be/a', 'viewer', viewer_id='42')
        b = self.store.add('https://youtu.be/b', 'viewer', viewer_id='42')
        self.store.add('https://youtu.be/c', 'viewer', viewer_id='42')
        self.store.rate(a, 10, ' Отлично ')
        self.store.rate(b, 6)
        profile = self.store.profiles()[0]
        self.assertEqual((profile['total'], profile['rated'], profile['average']), (3, 2, 8))
        self.store.rate(a, 2, 'передумал')
        self.assertEqual(self.store.profiles()[0]['average'], 4)
        self.assertEqual(self.store.get(a)['comment'], 'передумал')

    def test_rename_retains_history_and_merges_legacy_nickname(self):
        a = self.store.add('https://youtu.be/a', 'FirstName')
        self.store.rate(a, 9)
        self.store.add('https://youtu.be/b', 'firstname', viewer_id='42')
        self.store.add('https://youtu.be/c', 'NewName', viewer_id='42')
        profiles = self.store.profiles()
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0]['nickname'], 'NewName')
        self.assertEqual(profiles[0]['total'], 3)
        self.assertEqual(profiles[0]['average'], 9)
        self.assertEqual(len(self.store.viewer_tracks('twitch:42')), 3)
        self.store.add('https://youtu.be/d', 'newname')
        self.assertEqual(len(self.store.profiles()), 1)
        self.assertEqual(self.store.profiles()[0]['total'], 4)

    def test_invalid_rating_does_not_change_existing_review(self):
        ident = self.store.add('https://youtu.be/a')
        self.store.rate(ident, 7, 'сохранено')
        for invalid in (0, 11, -1, 5.5, True, '8', None):
            with self.assertRaises(ValueError):
                self.store.rate(ident, invalid)
        with self.assertRaises(ValueError):
            self.store.rate(ident, 8, 'x' * 2001)
        with self.assertRaises(ValueError):
            self.store.rate('missing', 8)
        self.assertEqual(self.store.get(ident)['rating'], 7)
        self.assertEqual(self.store.get(ident)['comment'], 'сохранено')

    def test_skip_cannot_be_revived_by_downloader(self):
        ident = self.store.add('https://youtu.be/a')
        self.store.update(ident, status='Пропущено')
        self.assertFalse(self.store.update_download(ident, status='Готово', path='a.mp3'))
        self.assertFalse(self.store.update_download(ident, status='Нужна замена', error='ошибка'))
        self.assertEqual(self.store.get(ident)['status'], 'Пропущено')

    def test_migrates_original_database_without_losing_queue(self):
        import sqlite3
        old_path = Path(self.folder.name) / 'old.db'
        db = sqlite3.connect(old_path)
        db.execute('''CREATE TABLE queue (id TEXT PRIMARY KEY, redemption TEXT UNIQUE,
            url TEXT, source TEXT, viewer TEXT, title TEXT, status TEXT, path TEXT DEFAULT '',
            error TEXT DEFAULT '', position INTEGER, replacement TEXT DEFAULT '')''')
        db.execute("INSERT INTO queue VALUES ('old','r','https://youtu.be/a','YouTube','Nick','Старый трек','Играет','C:/track.mp3','',1,'')")
        db.commit()
        db.close()
        migrated = QueueStore(old_path)
        self.addCleanup(migrated.db.close)
        row = migrated.get('old')
        self.assertEqual((row['title'], row['path'], row['status']), ('Старый трек', 'C:/track.mp3', 'Готово'))
        self.assertIsNone(row['rating'])
        self.assertEqual(migrated.profiles()[0]['total'], 1)
        migrated.rate('old', 8, 'из старой очереди')
        again = QueueStore(old_path)
        self.addCleanup(again.db.close)
        self.assertEqual(again.get('old')['comment'], 'из старой очереди')
        self.assertEqual(again.profiles()[0]['average'], 8)


class HistoryTests(unittest.TestCase):
    def test_sections_preserve_history_ratings_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'queue.db'
            store = QueueStore(path)
            first = store.add('https://youtu.be/a', 'nick')
            second = store.add('https://youtu.be/b', 'nick')
            third = store.add('https://youtu.be/c', 'nick')
            store.rate(first, 8, 'отзыв')
            store.update(first, status='Прослушано')
            store.update(second, status='Пропущено')
            self.assertEqual([r['id'] for r in store.rows('active')], [third])
            self.assertEqual([r['id'] for r in store.rows('history')], [second, first])
            store.db.close()
            store = QueueStore(path)
            try:
                self.assertEqual(len(store.rows('history')), 2)
                self.assertEqual(store.get(first)['comment'], 'отзыв')
                self.assertEqual(store.profiles()[0]['average'], 8)
                store.requeue(first)
                self.assertEqual([r['id'] for r in store.rows('active')], [third, first])
                self.assertEqual(store.get(first)['finished_at'], 0)
                self.assertEqual(store.profiles()[0]['total'], 3)
            finally:
                store.db.close()

    def test_movement_skips_archived_tracks(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QueueStore(Path(folder) / 'queue.db')
            try:
                first = store.add('https://youtu.be/a')
                middle = store.add('https://youtu.be/b')
                last = store.add('https://youtu.be/c')
                store.update(middle, status='Прослушано')
                store.move(first, 1)
                self.assertEqual([r['id'] for r in store.rows('active')], [last, first])
                self.assertEqual([r['id'] for r in store.rows('history')], [middle])
            finally:
                store.db.close()


if __name__ == '__main__':
    unittest.main()
