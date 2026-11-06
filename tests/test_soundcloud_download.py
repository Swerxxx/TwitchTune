import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from soundcloud_download import resolve_soundcloud, extract_soundcloud, SoundCloudError
from media import options


class SoundCloudDownloadTests(unittest.TestCase):
    def test_share_link_resolves_before_using_the_soundcloud_extractor(self):
        response=Mock(url='https://soundcloud.com/artist/track?si=tracking&utm_source=copy')
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        ydl=Mock()
        with patch('soundcloud_download.requests.get',return_value=response) as get:
            extract_soundcloud(ydl,'https://on.soundcloud.com/shareCode',download=True)
        ydl.extract_info.assert_called_once_with('https://soundcloud.com/artist/track',download=True,ie_key='Soundcloud')
        self.assertEqual(get.call_args.kwargs['timeout'],(10,45))

    def test_full_and_private_links_keep_secret_tokens_and_need_no_redirect_lookup(self):
        with patch('soundcloud_download.requests.get') as get:
            result=resolve_soundcloud('https://m.soundcloud.com/artist/track/s-Private?secret_token=s-Private&utm_medium=copy')
            get.assert_not_called()
        self.assertEqual(result,'https://soundcloud.com/artist/track/s-Private?secret_token=s-Private')
        with self.assertRaises(SoundCloudError):resolve_soundcloud('https://soundcloud.com/shareCode')

    def test_transient_timeout_retries_and_can_succeed(self):
        ydl=Mock()
        ydl.extract_info.side_effect=[RuntimeError('HTTPSConnectionPool Read timed out PRIVATE_URL'),{'title':'Track'}]
        notice=Mock()
        result=extract_soundcloud(ydl,'https://soundcloud.com/artist/track',download=True,retry_notice=notice)
        self.assertEqual(result['title'],'Track')
        self.assertEqual(ydl.extract_info.call_count,2)
        notice.assert_called_once()
        self.assertEqual(options(soundcloud=True)['socket_timeout'],45)
        self.assertEqual(options()['socket_timeout'],15)

    def test_repeated_timeout_is_bounded_and_does_not_expose_raw_urls(self):
        ydl=Mock()
        ydl.extract_info.side_effect=RuntimeError('Read timed out PRIVATE_URL')
        with self.assertRaises(SoundCloudError) as error:
            extract_soundcloud(ydl,'https://soundcloud.com/artist/track',download=True)
        self.assertEqual(ydl.extract_info.call_count,2)
        self.assertIn('SoundCloud не ответил',str(error.exception))
        self.assertNotIn('PRIVATE_URL',str(error.exception))

    def test_access_denial_and_cancel_are_not_retried(self):
        ydl=Mock()
        ydl.extract_info.side_effect=RuntimeError('HTTP Error 403')
        with self.assertRaises(SoundCloudError):extract_soundcloud(ydl,'https://soundcloud.com/artist/track',download=True)
        self.assertEqual(ydl.extract_info.call_count,1)
        ydl.reset_mock()
        with self.assertRaises(SoundCloudError):extract_soundcloud(ydl,'https://soundcloud.com/artist/track',download=True,cancelled=lambda:True)
        ydl.extract_info.assert_not_called()
