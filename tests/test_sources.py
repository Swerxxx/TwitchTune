import sys
import tempfile
import unittest
import shutil
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import QueueStore, parse_link
from media import Downloader, search, options
from yandex_direct import YandexAccessError
from source_metadata import yandex_reference, yandex_record, yandex_metadata, page_metadata, replacement_query, MetadataError

URL = 'https://music.yandex.ru/album/43917005/track/155671332?utm_source=web&utm_medium=copy_link'


class SourceTests(unittest.TestCase):
    def record(self):
        return dict(id=155671332, title='Проверочный трек', artists=[dict(name='Исполнитель')], durationMs=184000, available=False)

    def test_user_url_and_short_track_url_are_accepted_without_tracking(self):
        ident, canonical = yandex_reference(URL)
        self.assertEqual(ident, '155671332')
        self.assertEqual(canonical, 'https://music.yandex.ru/album/43917005/track/155671332')
        self.assertEqual(yandex_reference('https://music.yandex.ru/track/155671332')[0], ident)
        with self.assertRaises(ValueError): parse_link('https://music.yandex.ru/album/43917005')
        with self.assertRaises(ValueError): parse_link('https://music.yandex.ru/users/test/playlists/1')

    def test_metadata_survives_unavailable_audio_and_preserves_version(self):
        record = self.record()
        record.update(version='Live', artists=[dict(name='Первый'), dict(name='Второй')])
        metadata = yandex_record(record, '155671332')
        self.assertEqual(metadata['title'], 'Первый, Второй — Проверочный трек (Live)')
        self.assertEqual(metadata['duration'], 184)
        self.assertEqual(replacement_query(metadata), 'Первый, Второй Проверочный трек (Live)')

    def test_wrong_id_never_becomes_the_requested_track(self):
        self.assertIsNone(yandex_record(self.record(), 'other'))
        self.assertEqual(replacement_query({'title': URL}), '')

    def test_api_metadata_does_not_require_audio_download(self):
        response = Mock(status_code=200, json=Mock(return_value={'result': [self.record()]}))
        with patch('source_metadata.requests.get', return_value=response) as request:
            metadata = yandex_metadata(URL)
        self.assertEqual(metadata['artist'], 'Исполнитель')
        request.assert_called_once()

    def test_451_and_generic_frontend_produce_manual_metadata_hint(self):
        responses = [Mock(status_code=451), Mock(status_code=200, text='<title>Яндекс Музыка — собираем музыку для вас</title>')]
        with patch('source_metadata.requests.get', side_effect=responses):
            with self.assertRaises(MetadataError) as error: yandex_metadata(URL)
        self.assertIn('451', str(error.exception))
        self.assertIn('Впиши исполнителя', str(error.exception))

    def test_public_structured_page_metadata_is_a_safe_fallback(self):
        html = '<script type="application/ld+json">{"@type":"MusicRecording","name":"Песня","byArtist":{"name":"Артист"}}</script>'
        with patch('source_metadata.requests.get', side_effect=[Mock(status_code=451), Mock(status_code=200, text=html)]):
            self.assertEqual(yandex_metadata(URL)['title'], 'Артист — Песня')
        self.assertIsNone(page_metadata('<meta property="og:title" content="Яндекс Музыка"><meta property="og:type" content="website">'))

    def downloader(self, store, folder):
        # Isolate the per-request path without starting persistent worker threads.
        downloader = Downloader.__new__(Downloader)
        downloader.store, downloader.folder = store, Path(folder)
        downloader.stop, downloader.emit = Event(), Mock()
        return downloader

    def test_yandex_request_downloads_directly_without_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QueueStore(Path(folder)/'queue.db')
            try:
                ident = store.add(URL, 'viewer')
                path = Path(folder)/(ident+'.mp3')
                path.write_bytes(b'test audio')
                ydl = Mock()
                ydl.__enter__ = Mock(return_value=ydl)
                ydl.__exit__ = Mock(return_value=False)
                ydl.prepare_filename.return_value = str(path)
                info = dict(id='155671332', title='Проверочный трек', artist='Исполнитель', duration=184)
                with patch('media.extract_direct', return_value=info) as direct, patch('media.validate_full_track', return_value=184), patch('media.YoutubeDL', return_value=ydl):
                    self.downloader(store, folder).download(store.get(ident))
                row = store.get(ident)
                self.assertEqual(row['status'], 'Готово')
                self.assertEqual(row['title'], 'Исполнитель — Проверочный трек')
                self.assertEqual(row['path'], str(path.resolve()))
                self.assertEqual(row['replacement'], '')
                self.assertEqual(row['viewer'], 'viewer')
                self.assertEqual(direct.call_args.args[1], URL)
                ydl.extract_info.assert_not_called()
            finally: store.db.close()

    def test_direct_download_accepts_real_tagless_audio_and_keeps_file(self):
        for ext in ('mp3', 'flac'):
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as folder:
                store = QueueStore(Path(folder)/'queue.db')
                try:
                    ident = store.add(URL, 'viewer')
                    path = Path(folder)/(ident+'.'+ext)
                    shutil.copyfile(Path(__file__).parent/'fixtures'/('silence-6s.'+ext), path)
                    ydl = Mock()
                    ydl.__enter__ = Mock(return_value=ydl)
                    ydl.__exit__ = Mock(return_value=False)
                    ydl.prepare_filename.return_value = str(path)
                    info = dict(title='Проверочный трек', artist='Исполнитель', duration=6)
                    # Real Mutagen validation runs here; only the network is mocked.
                    with patch('media.extract_direct', return_value=info), patch('media.YoutubeDL', return_value=ydl):
                        self.downloader(store, folder).download(store.get(ident))
                    self.assertEqual(store.get(ident)['status'], 'Готово')
                    self.assertEqual(store.get(ident)['error'], '')
                    self.assertTrue(path.is_file())
                finally:
                    store.db.close()

    def test_direct_failure_does_not_search_replacement_or_revive_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QueueStore(Path(folder)/'queue.db')
            try:
                ident = store.add(URL)
                row = store.get(ident)
                downloader = self.downloader(store, folder)
                with patch('media.extract_direct', side_effect=YandexAccessError('Нужен вход')):
                    with self.assertRaises(YandexAccessError): downloader.download(row)
                self.assertNotEqual(store.get(ident)['status'], 'Готово')
                self.assertEqual(store.get(ident)['replacement'], '')
                store.delete(ident)
                with patch('media.extract_direct') as direct:
                    downloader.download(row)
                direct.assert_not_called()
                self.assertIsNone(store.get(ident))
            finally: store.db.close()

    def test_youtube_search_refuses_source_url_and_filters_long_live_results(self):
        with self.assertRaises(ValueError): search(URL)
        ydl = Mock()
        ydl.__enter__ = Mock(return_value=ydl)
        ydl.__exit__ = Mock(return_value=False)
        ydl.extract_info.return_value = {'entries': [
            dict(id='good', title='Song', duration=200, channel='Artist'),
            dict(id='long', title='Mix', duration=5401),
            dict(id='live', title='Stream', is_live=True)]}
        with patch('media.YoutubeDL', return_value=ydl):
            rows = search('Artist Song')
        self.assertEqual([row['url'] for row in rows], ['https://www.youtube.com/watch?v=good'])
        self.assertNotIn('nocheckcertificate', options())
        self.assertIn('no-certifi', options()['compat_opts'])


if __name__ == '__main__': unittest.main()
