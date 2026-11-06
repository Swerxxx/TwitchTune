import sys
import tempfile
import unittest
from pathlib import Path
from http.cookiejar import Cookie, CookieJar
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yt_dlp import YoutubeDL
from mutagen import File as ReadAudio
from yt_dlp.utils import ExtractorError
from media import options
from yandex_direct import attach_session, extract_direct, validate_full_track, FullYandexTrackIE, YandexAccessError, download_error_message


def cookie(domain, name='Session_id', value='TEST_SESSION_NOT_REAL'):
    return Cookie(0,name,value,None,False,domain,True,domain.startswith('.'),'/',True,True,None,True,None,None,{},False)


class YandexDirectTests(unittest.TestCase):
    def test_real_tagless_mp3_and_flac_are_valid_audio(self):
        for ext in ('mp3', 'flac'):
            with self.subTest(ext=ext):
                path = Path(__file__).parent / 'fixtures' / ('silence-6s.' + ext)
                audio = ReadAudio(path)
                self.assertIsNotNone(audio)
                self.assertFalse(bool(audio), 'Fixture must have no tags to reproduce the bug')
                self.assertAlmostEqual(validate_full_track(path, 6), 6, delta=.1)
                with self.assertRaises(YandexAccessError):
                    validate_full_track(path, 180)

    def test_explicit_modern_extractor_is_used_for_original_yandex_url(self):
        with YoutubeDL(options()) as ydl, patch('yandex_direct.attach_session') as session:
            ydl.extract_info = Mock(return_value={'title':'Track'})
            result = extract_direct(ydl,'https://music.yandex.ru/album/43917005/track/155671332?utm_source=web',{'yandex_auth':'firefox'})
            self.assertEqual(result['title'],'Track')
            ydl.extract_info.assert_called_once_with('https://music.yandex.ru/album/43917005/track/155671332',download=True,ie_key=FullYandexTrackIE.ie_key())
            self.assertIn(FullYandexTrackIE.ie_key(),ydl._ies)
            session.assert_called_once()

    def test_browser_session_copies_only_yandex_cookies(self):
        ydl = Mock(cookiejar=CookieJar())
        source = [cookie('.yandex.ru'),cookie('.example.org','other','UNRELATED')]
        with patch('yandex_direct.extract_cookies_from_browser',return_value=source) as extract:
            attach_session(ydl,{'yandex_auth':'firefox'})
        self.assertEqual(extract.call_args.args[0],'firefox')
        self.assertEqual([c.domain for c in ydl.cookiejar],['.yandex.ru'])

    def test_no_session_source_never_reads_a_browser(self):
        with patch('yandex_direct.extract_cookies_from_browser') as extract:
            with self.assertRaises(YandexAccessError):attach_session(Mock(),{})
        extract.assert_not_called()

    def test_cookie_file_is_filtered_and_never_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cookies.txt'
            content='# Netscape HTTP Cookie File\n.yandex.ru\tTRUE\t/\tTRUE\t2147483647\tSession_id\tTEST_ONLY\n.example.org\tTRUE\t/\tTRUE\t2147483647\tother\tUNRELATED\n'
            path.write_text(content)
            ydl=Mock(cookiejar=CookieJar())
            attach_session(ydl,{'yandex_auth':'cookies','yandex_cookie_file':str(path)})
            self.assertEqual(len(list(ydl.cookiejar)),1)
            self.assertEqual(path.read_text(),content)

    def test_preview_and_short_streams_are_rejected(self):
        with self.assertRaises(ExtractorError):FullYandexTrackIE()._warn_if_preview_served('1',{'durationMs':180000},{'quality':'smart_preview'})
        with patch('yandex_direct.AudioFile',return_value=SimpleNamespace(info=SimpleNamespace(length=30))):
            with self.assertRaises(YandexAccessError):validate_full_track('file.mp3',180)
        with patch('yandex_direct.AudioFile',return_value=SimpleNamespace(info=SimpleNamespace(length=180))):
            self.assertEqual(validate_full_track('file.mp3',180),180)
        with self.assertRaises(YandexAccessError):validate_full_track('file.mp3',0)

    def test_private_error_details_are_not_exposed(self):
        ydl=Mock()
        ydl.extract_info.side_effect=RuntimeError('signed-url PRIVATE_COOKIE_VALUE')
        with patch('yandex_direct.attach_session'):
            with self.assertRaises(YandexAccessError) as error:extract_direct(ydl,'https://music.yandex.ru/track/1',{'yandex_auth':'firefox'})
        self.assertNotIn('PRIVATE_COOKIE_VALUE',str(error.exception))

    def test_nested_download_failures_have_safe_actionable_messages(self):
        for detail, expected in [('HTTP Error 401', '401'), ('HTTP Error 403', '403'),
                                 ('HTTP Error 451', '451'), ('HTTP Error 429', '429'),
                                 ('smart_preview', 'превью'), ('certificate verify failed', 'сертификат'),
                                 ('Connection timed out', 'соединиться')]:
            with self.subTest(detail=detail):
                cause = ExtractorError(detail+' PRIVATE_SESSION https://private.example')
                outer = RuntimeError('Download failed')
                outer.__cause__ = cause
                message = download_error_message(outer)
                self.assertIn(expected, message)
                self.assertNotIn('PRIVATE_SESSION', message)
                self.assertNotIn('private.example', message)


if __name__=='__main__':unittest.main()
