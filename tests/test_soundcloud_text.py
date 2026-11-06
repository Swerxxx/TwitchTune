import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from soundcloud_text import description_lyrics, description_text, lyrics_record
from media import soundcloud_lyrics, Downloader
from core import QueueStore
from threading import Event

TEXT = 'За окном гаснут фонари\nМы на общей частоте\nЭтот вечер сохрани\nВ незнакомом городе'
DESCRIPTION = 'Listen on https://example.org\n\nLYRICS:\n[Куплет]\n'+TEXT+'\n\nProduced by Test\nhttps://example.org'


class SoundCloudTextTests(unittest.TestCase):
    def test_lyrics_block_ignores_intro_links_and_trailing_credits(self):
        self.assertEqual(description_lyrics(DESCRIPTION), '[Куплет]\n'+TEXT)
        self.assertEqual(description_lyrics('Текст песни:\n[00:01.25]Первая тестовая строка\n[00:02.5]Вторая тестовая строка'), 'Первая тестовая строка\nВторая тестовая строка')
        self.assertEqual(description_lyrics('LYRICS:\nFollow me across a static sky\nWe carry light until the night goes by\nSubscribe: channel'),
                         'Follow me across a static sky\nWe carry light until the night goes by')

    def test_unlabelled_verses_are_supported_but_promo_and_prose_are_not(self):
        lines = '\n'.join(['Мы рисуем свет на тёмном стекле','Новый вечер остаётся во мне']*3)
        self.assertEqual(description_lyrics(lines), lines)
        for description in ('New single out now!\nhttps://example.org',
                            'Produced by Person\nMixed by Person\n'+lines,
                            'This is a long release announcement. '*20, 'Lyrics by Test Author', '', None):
            self.assertEqual(description_lyrics(description), '')

    def test_saved_text_returns_without_another_network_request(self):
        row=dict(id='test',source='SoundCloud',replacement='',url='https://soundcloud.com/a/b',title='Artist — Track',
                 description_lyrics=TEXT,description_checked=1)
        with patch('media.YoutubeDL') as ydl:
            result=soundcloud_lyrics(Mock(),row)
            ydl.assert_not_called()
        self.assertEqual(result['plainLyrics'],TEXT)
        self.assertEqual(result['source'],'SoundCloud')
        self.assertEqual(result['trackName'],'Track')

    def test_old_track_inspection_persists_description_without_audio_download(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'queue.db'
            store=QueueStore(path)
            try:
                ident=store.add('https://soundcloud.com/artist/track')
                store.update(ident,title='Artist — Track')
                ydl=Mock()
                ydl.__enter__=Mock(return_value=ydl)
                ydl.__exit__=Mock(return_value=False)
                ydl.extract_info.return_value={'description':DESCRIPTION}
                with patch('media.YoutubeDL',return_value=ydl):
                    result=soundcloud_lyrics(store,store.get(ident))
                self.assertEqual(result['plainLyrics'],'[Куплет]\n'+TEXT)
                ydl.extract_info.assert_called_once_with('https://soundcloud.com/artist/track',download=False,ie_key='Soundcloud')
                store.db.close();store=QueueStore(path)
                self.assertEqual(store.get(ident)['description_checked'],2)
                self.assertIn('https://example.org',store.get(ident)['soundcloud_description'])
                self.assertEqual(lyrics_record(store.get(ident))['plainLyrics'],result['plainLyrics'])
            finally:store.db.close()

    def test_deleted_or_cancelled_requests_do_not_reappear(self):
        with tempfile.TemporaryDirectory() as folder:
            store=QueueStore(Path(folder)/'q.db')
            try:
                ident=store.add('https://soundcloud.com/artist/track')
                row=store.get(ident)
                with patch('media.YoutubeDL') as ydl:
                    self.assertIsNone(soundcloud_lyrics(store,row,cancelled=lambda:True))
                    ydl.assert_not_called()
                ydl=Mock()
                ydl.__enter__=Mock(return_value=ydl)
                ydl.__exit__=Mock(return_value=False)
                def extract(*args,**kwargs):
                    store.delete(ident)
                    return {'description':DESCRIPTION}
                ydl.extract_info.side_effect=extract
                with patch('media.YoutubeDL',return_value=ydl):
                    self.assertIsNone(soundcloud_lyrics(store,row))
                self.assertIsNone(store.get(ident))
            finally:store.db.close()

    def test_soundcloud_download_saves_lyrics_from_extracted_description(self):
        with tempfile.TemporaryDirectory() as folder:
            store=QueueStore(Path(folder)/'q.db')
            try:
                ident=store.add('https://soundcloud.com/artist/track')
                audio=Path(folder)/(ident+'.mp3');audio.write_bytes(b'offline audio placeholder')
                ydl=Mock()
                ydl.__enter__=Mock(return_value=ydl)
                ydl.__exit__=Mock(return_value=False)
                ydl.extract_info.return_value=dict(title='Track',artist='Artist',track='Track',duration=120,description=DESCRIPTION)
                ydl.prepare_filename.return_value=str(audio)
                downloader=Downloader.__new__(Downloader)
                downloader.store,downloader.folder,downloader.stop,downloader.emit=store,Path(folder),Event(),Mock()
                with patch('media.YoutubeDL',return_value=ydl):
                    downloader.download(store.get(ident))
                row=store.get(ident)
                self.assertEqual(row['status'],'Готово')
                self.assertEqual(row['description_lyrics'],'[Куплет]\n'+TEXT)
                self.assertEqual(row['description_checked'],2)
                self.assertIn('Listen on',row['soundcloud_description'])
            finally:store.db.close()

    def test_short_mixed_description_is_available_in_full_without_guessing_lyrics(self):
        raw='Listen on https://example.org\n\nЯ не сплю\nСнова жду\n\n@artist · mixed by Test'
        self.assertEqual(description_lyrics(raw),'')
        row=dict(id='a',title='Track',url='https://soundcloud.com/a/b',soundcloud_description=description_text(raw))
        result=lyrics_record(row)
        self.assertEqual(result['plainLyrics'],raw);self.assertTrue(result['descriptionFallback'])
        row['description_lyrics']=TEXT
        self.assertEqual(lyrics_record(row)['plainLyrics'],TEXT)
        self.assertEqual(lyrics_record(row,full=True)['plainLyrics'],raw)

    def test_legacy_failed_check_is_retried_and_full_description_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'q.db';store=QueueStore(path)
            ident=store.add('https://soundcloud.com/a/b');store.update(ident,description_checked=1)
            ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
            ydl.extract_info.return_value={'description':'Короткий текст\nhttps://example.org'}
            with patch('media.YoutubeDL',return_value=ydl):result=soundcloud_lyrics(store,store.get(ident))
            self.assertTrue(result['descriptionFallback']);store.db.close();store=QueueStore(path)
            try:
                with patch('media.YoutubeDL') as network:cached=soundcloud_lyrics(store,store.get(ident))
                network.assert_not_called();self.assertEqual(cached['plainLyrics'],result['plainLyrics'])
            finally:store.db.close()

    def test_missing_description_does_not_permanently_cache_a_failed_lookup(self):
        store=QueueStore(':memory:');ident=store.add('https://soundcloud.com/a/b')
        ydl=Mock();ydl.__enter__=Mock(return_value=ydl);ydl.__exit__=Mock(return_value=False)
        ydl.extract_info.return_value={}
        try:
            with patch('media.YoutubeDL',return_value=ydl):self.assertIsNone(soundcloud_lyrics(store,store.get(ident)))
            self.assertEqual(store.get(ident)['description_checked'],0)
        finally:store.db.close()

    def test_soundcloud_description_uses_original_url_even_when_audio_has_a_replacement(self):
        store=QueueStore(':memory:');ident=store.add('https://soundcloud.com/a/b')
        store.update(ident,replacement='https://youtu.be/replacement')
        try:
            with patch('media.YoutubeDL'),patch('media.extract_soundcloud',return_value={'description':'Оригинальное описание'}) as extract:
                record=soundcloud_lyrics(store,store.get(ident))
            self.assertEqual(extract.call_args.args[1],'https://soundcloud.com/a/b')
            self.assertEqual(record['plainLyrics'],'Оригинальное описание')
            self.assertEqual(record['sourceUrl'],'https://soundcloud.com/a/b')
        finally:store.db.close()

    def test_youtube_eof_is_retried_but_not_access_errors_or_cancelled_orders(self):
        store=QueueStore(':memory:');ident=store.add('https://youtu.be/test')
        downloader=Downloader.__new__(Downloader)
        downloader.store,downloader.stop,downloader.emit=store,Mock(),Mock()
        downloader.stop.is_set.return_value=False;downloader.stop.wait.return_value=False
        try:
            with patch.object(downloader,'_download',side_effect=[RuntimeError('SSL: UNEXPECTED_EOF_WHILE_READING'),'ready']) as download:
                self.assertEqual(downloader.download(store.get(ident)),'ready');self.assertEqual(download.call_count,2)
            with patch.object(downloader,'_download',side_effect=RuntimeError('SSL: UNEXPECTED_EOF_WHILE_READING')) as download:
                with self.assertRaisesRegex(RuntimeError,'Заказ сохранён'):downloader.download(store.get(ident))
                self.assertEqual(download.call_count,2)
            with patch.object(downloader,'_download',side_effect=RuntimeError('HTTP Error 403')) as download:
                with self.assertRaises(RuntimeError):downloader.download(store.get(ident))
                self.assertEqual(download.call_count,1)
        finally:store.db.close()
