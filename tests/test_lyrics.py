import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from lyrics import LyricsClient, LyricsError, song_query, plain_text, exact_match


class LyricsTests(unittest.TestCase):
    def record(self, ident=1, **values):
        row = dict(id=ident, artistName='Test Artist', trackName='Test Song', duration=120,
                   plainLyrics='Оригинальный тестовый текст\nВторая строка', instrumental=False)
        row.update(values)
        return row

    def response(self, data, status=200, headers=None):
        return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=data))

    def test_metadata_cleanup_keeps_song_versions(self):
        self.assertEqual(song_query({'title': 'Artist — Song (Official Music Video)'}), ('Artist', 'Song'))
        self.assertEqual(song_query({'title': 'Artist - Song (Live)'}), ('Artist', 'Song (Live)'))
        self.assertEqual(song_query({'title': 'video name', 'artist': 'A', 'track': 'Actual Song'}), ('A', 'Actual Song'))
        self.assertEqual(song_query({'title': 'https://youtu.be/id'}), ('', ''))
        self.assertEqual(song_query({'title':'Artist - Song (Официальный клип) (prod. Producer)'}),('Artist','Song'))
        self.assertEqual(song_query({'title':'Artist - Song (Remix) | Official Audio'}),('Artist','Song (Remix)'))

    def test_empty_artist_title_search_uses_one_broader_keyword_query(self):
        with patch('lyrics.requests.get',side_effect=[self.response([]),self.response([self.record()])]) as request,patch('lyrics.time.sleep'):
            rows=LyricsClient().search('Test Artist','Test Song',120)
        self.assertEqual(rows[0]['trackName'],'Test Song')
        self.assertEqual(request.call_count,2)
        self.assertEqual(request.call_args.kwargs['params'],{'q':'Test Artist Test Song'})

    def test_search_cache_filters_empty_and_ranks_exact_version(self):
        client = LyricsClient()
        data = [self.record(ident=2, trackName='Test Song (Live)'), self.record(),
                self.record(ident=3, plainLyrics=None), self.record()]
        with patch('lyrics.requests.get', return_value=self.response(data)) as request:
            rows = client.search('Test Artist', 'Test Song', 120)
            cached = client.search('Test Artist', 'Test Song', 120)
        self.assertEqual([row['id'] for row in rows], [1, 2])
        self.assertEqual(cached, rows)
        request.assert_called_once()
        self.assertEqual(request.call_args.kwargs['params'], {'artist_name': 'Test Artist', 'track_name': 'Test Song'})
        self.assertIn('TwitchTune', request.call_args.kwargs['headers']['User-Agent'])

    def test_title_only_search_and_instrumental_are_supported(self):
        record = self.record(instrumental=True, plainLyrics=None)
        with patch('lyrics.requests.get', return_value=self.response([record])) as request:
            rows = LyricsClient().search('', 'Test Song')
        self.assertEqual(request.call_args.kwargs['params'], {'q': 'Test Song'})
        self.assertTrue(rows[0]['instrumental'])
        self.assertEqual(plain_text(record), '')

    def test_lrc_fallback_removes_timecodes_without_losing_sections(self):
        record = dict(plainLyrics=None, syncedLyrics='[ar:Test]\n[00:01.20]Строка один\n[00:02.00][00:03.50]Строка два\n[Verse]')
        self.assertEqual(plain_text(record), 'Строка один\nСтрока два\n[Verse]')
        self.assertEqual(plain_text(dict(plainLyrics='<script>test</script>')), '<script>test</script>')

    def test_plain_lyrics_also_remove_lrc_and_word_timestamps(self):
        text = '[ar:Test]\n[00:04.41] Первая строка\n [01:02][01:03.2] Вторая <01:03.25>строка\n\n[Припев]'
        self.assertEqual(plain_text({'plainLyrics': text}), 'Первая строка\nВторая строка\n\n[Припев]')
        self.assertEqual(plain_text({'plainLyrics': '  ', 'syncedLyrics': '[00:01]Текст'}), 'Текст')

    def test_network_error_is_distinct_from_not_found(self):
        with patch('lyrics.requests.get', side_effect=requests.Timeout()):
            with self.assertRaises(LyricsError): LyricsClient().search('A', 'B')
        with patch('lyrics.requests.get', return_value=self.response([])):
            self.assertEqual(LyricsClient().search('A', 'B'), [])
        with patch('lyrics.requests.get', return_value=self.response({'error': 'bad'})):
            with self.assertRaises(LyricsError): LyricsClient().search('A', 'B')

    def test_rate_limit_wait_is_honored_without_a_second_request(self):
        client = LyricsClient()
        with patch('lyrics.requests.get', return_value=self.response({}, 429, {'Retry-After': '120'})) as request:
            with self.assertRaises(LyricsError): client.search('A', 'B')
            with self.assertRaises(LyricsError): client.search('C', 'D')
        request.assert_called_once()

    def test_cancelled_search_never_uses_network(self):
        with patch('lyrics.requests.get') as request:
            self.assertEqual(LyricsClient().search('A', 'B', cancelled=lambda: True), [])
        request.assert_not_called()

    def test_automatic_display_requires_artist_title_and_duration(self):
        self.assertTrue(exact_match(self.record(), 'Test Artist', 'Test Song', 120))
        self.assertFalse(exact_match(self.record(), '', 'Test Song', 120))
        self.assertFalse(exact_match(self.record(), 'Other Artist', 'Test Song', 120))
        self.assertFalse(exact_match(self.record(), 'Test Artist', 'Test Song', 200))


if __name__ == '__main__': unittest.main()
