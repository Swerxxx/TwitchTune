import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from media import Downloader
from spotify_support import spotify_reference, parse_spotify_embed, spotify_metadata, choose_spotify_version, SpotifyError

URL = 'https://open.spotify.com/intl-ru/track/Test123?si=example'
META = dict(title='Artist — Song', artist='Artist', track='Song', duration=180)


class SpotifyTests(unittest.TestCase):
    def embed(self, **changes):
        entity = dict(type='track', uri='spotify:track:Test123', title='Song', artists=[{'name': 'Artist'}], duration=180000)
        entity.update(changes)
        return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props': {'pageProps': {'state': {'data': {'entity': entity}}}}})+'</script>'

    def candidate(self, **changes):
        item = dict(title='Artist - Song (Official Audio)', channel='Artist - Topic', duration=181,
                    url='https://www.youtube.com/watch?v=match')
        item.update(changes)
        return item

    def test_public_embed_resolves_exact_track_and_canonical_url(self):
        self.assertEqual(spotify_reference(URL), ('Test123', 'https://open.spotify.com/track/Test123'))
        self.assertEqual(parse_spotify_embed(self.embed(), 'Test123'), META)
        with patch('spotify_support.requests.get', return_value=Mock(text=self.embed())) as get:
            self.assertEqual(spotify_metadata(URL), META)
        self.assertEqual(get.call_args.args[0], 'https://open.spotify.com/embed/track/Test123')

    def test_search_accepts_missing_duration_but_auto_download_does_not(self):
        from spotify_support import spotify_search_metadata
        html = self.embed(duration=0)
        with patch('spotify_support.requests.get', return_value=Mock(text=html)):
            self.assertEqual(spotify_search_metadata(URL)['title'], 'Artist — Song')
            with self.assertRaises(SpotifyError):
                spotify_metadata(URL)

    def test_search_falls_back_to_public_page_after_embed_schema_change(self):
        from spotify_support import spotify_search_metadata
        page = '<script type="application/ld+json">' + json.dumps({
            '@type': 'MusicRecording', 'name': 'Song', 'byArtist': [{'name': 'Artist'}]}) + '</script>'
        with patch('spotify_support.requests.get', side_effect=[Mock(text='<html>New embed</html>'), Mock(text=page)]) as get:
            metadata = spotify_search_metadata(URL)
        self.assertEqual(metadata['title'], 'Artist — Song')
        self.assertEqual(metadata['duration'], 0)
        self.assertEqual(get.call_count, 2)

    def test_search_uses_oembed_title_if_other_resolvers_fail(self):
        from spotify_support import spotify_search_metadata
        import requests
        with patch('spotify_support.requests.get', side_effect=[requests.Timeout(), Mock(text='<html>Login</html>'), Mock(json=Mock(return_value={'title': 'Song'}))]) as get:
            self.assertEqual(spotify_search_metadata(URL)['track'], 'Song')
        self.assertEqual(get.call_args.kwargs['params']['url'], 'https://open.spotify.com/track/Test123')

    def test_search_rejects_generic_spotify_pages(self):
        from spotify_support import spotify_search_metadata
        with patch('spotify_support.requests.get', side_effect=[Mock(text=''), Mock(text=''), Mock(json=Mock(return_value={'title': 'Spotify'}))]):
            with self.assertRaisesRegex(SpotifyError, 'второе поле'):
                spotify_search_metadata(URL)

    def test_missing_or_wrong_metadata_cannot_select_a_song(self):
        for html in ('<html>Sign in</html>', self.embed(uri='spotify:track:Other'), self.embed(artists=[]), self.embed(duration=0), self.embed(type='album')):
            with self.subTest(html=html), self.assertRaises(SpotifyError):
                parse_spotify_embed(html, 'Test123')

    def test_matching_rejects_wrong_artist_version_duration_and_untrusted_source(self):
        wrong = [self.candidate(title='Other song'), self.candidate(title='Other - Song', channel='Other'),
                 self.candidate(title='Artist - Song Live'), self.candidate(title='Artist - Song Cover'),
                 self.candidate(duration=30), self.candidate(duration=None), self.candidate(url='https://example.com/watch')]
        self.assertIsNone(choose_spotify_version(META, wrong))
        match = self.candidate()
        self.assertEqual(choose_spotify_version(META, wrong + [match]), match)
        remix = dict(META, track='Song Remix')
        self.assertIsNone(choose_spotify_version(remix, [match]))

    def downloader(self, store, folder):
        down = Downloader.__new__(Downloader)
        down.store, down.folder, down.stop, down.emit = store, Path(folder), Event(), Mock()
        return down

    def test_auto_match_downloads_audio_and_preserves_request_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QueueStore(Path(folder)/'queue.db')
            try:
                ident = store.add(URL, 'Viewer')
                path = Path(folder)/(ident+'.mp3')
                shutil.copyfile(Path(__file__).parent/'fixtures'/'silence-6s.mp3', path)
                ydl = Mock()
                ydl.__enter__ = Mock(return_value=ydl)
                ydl.__exit__ = Mock(return_value=False)
                ydl.extract_info.return_value = dict(title='Upload title', duration=180)
                ydl.prepare_filename.return_value = str(path)
                with patch('media.spotify_metadata', return_value=META), patch('media.search', return_value=[self.candidate()]) as search, patch('media.YoutubeDL', return_value=ydl) as factory:
                    self.downloader(store, folder).download(store.get(ident))
                guard = factory.call_args.args[0]['match_filter']
                self.assertIsNone(guard({'duration': 180}))
                self.assertIsNotNone(guard({'duration': 30}))
                row = store.get(ident)
                self.assertEqual(row['status'], 'Готово')
                self.assertEqual(row['url'], URL)
                self.assertEqual(row['source'], 'Spotify')
                self.assertEqual(row['viewer'], 'Viewer')
                self.assertEqual(row['artist'], 'Artist')
                self.assertEqual(row['track'], 'Song')
                self.assertEqual(row['replacement'], self.candidate()['url'])
                search.assert_called_once_with('Artist Song')
                ydl.extract_info.assert_called_once_with(self.candidate()['url'], download=True)
            finally:
                store.db.close()

    def test_no_match_never_downloads_an_arbitrary_first_result(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QueueStore(Path(folder)/'queue.db')
            try:
                ident = store.add(URL)
                with patch('media.spotify_metadata', return_value=META), patch('media.search', return_value=[self.candidate(duration=30)]), patch('media.YoutubeDL') as ydl:
                    with self.assertRaises(SpotifyError):
                        self.downloader(store, folder).download(store.get(ident))
                    ydl.assert_not_called()
                self.assertEqual(store.get(ident)['replacement'], '')
                self.assertEqual(store.get(ident)['title'], META['title'])
            finally:
                store.db.close()

    def test_delete_while_searching_cannot_revive_request(self):
        with tempfile.TemporaryDirectory() as folder:
            store = QueueStore(Path(folder)/'queue.db')
            try:
                ident = store.add(URL)
                def search(_):
                    store.delete(ident)
                    return [self.candidate()]
                with patch('media.spotify_metadata', return_value=META), patch('media.search', side_effect=search), patch('media.YoutubeDL') as ydl:
                    self.downloader(store, folder).download(store.get(ident))
                    ydl.assert_not_called()
                self.assertIsNone(store.get(ident))
            finally:
                store.db.close()
