"""Regression: API returned nonempty HTTPS URL on a new CDN without known suffix."""
import sys,unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yt_dlp import YoutubeDL
from vk_audio import extract_vk_audio,TwitchTuneVKAudioIE,VKAudioError
from vk_stream import audio_type,public_https_target,verified_audio_formats,VKStreamError
from test_vk_audio import URL

CDN='https://audio.example.net/play/opaque?signature=DUMMY_SECRET'
PUBLIC=[(2,1,6,'',('8.8.8.8',443))]
RAW=dict(id=148062996,owner_id=-2001062996,title='Test Song',artist='Test Artist',duration=120,url=CDN)


def response(data=b'ID3test',mime='audio/mpeg',url=CDN,status=206):
    stream=Mock(status=status,url=url,headers={'Content-Type':mime})
    stream.read.return_value=data
    stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock(return_value=False)
    return stream


class VKStreamTests(unittest.TestCase):
    def test_audio_mime_and_signatures_work_without_path_extension(self):
        for mime,data,ext in [('audio/mpeg',b'ID3test','mp3'),('audio/mp4',b'\0\0\0\x14ftypM4A ','m4a'),('application/octet-stream',b'ID3test','mp3'),('application/octet-stream',b'OggS000OpusHead','opus'),('application/octet-stream',b'fLaCtest','flac'),('application/octet-stream',b'RIFF0000WAVE','wav'),('application/vnd.apple.mpegurl',b'\xef\xbb\xbf#EXTM3U\n','m3u8')]:
            self.assertEqual(audio_type(mime,data),ext)
    def test_html_json_and_empty_data_are_not_audio_even_when_mislabeled(self):
        for data in (b'',b'<html>Login</html>',b'{"error":"denied"}',b'  [1,2]'):
            self.assertEqual(audio_type('audio/mpeg',data),'')
    def test_dns_and_url_checks_reject_private_and_unsafe_targets(self):
        with patch('vk_stream.socket.getaddrinfo',return_value=PUBLIC):self.assertTrue(public_https_target(CDN))
        for target in ('http://audio.example.net/a','https://user:secret@audio.example.net/a','https://audio.example.net:8080/a'):
            self.assertFalse(public_https_target(target))
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','224.0.0.1'):
            with patch('vk_stream.socket.getaddrinfo',return_value=[(2,1,6,'',(address,443))]):self.assertFalse(public_https_target(CDN))
    def test_probe_uses_bounded_range_and_keeps_signed_url_out_of_errors(self):
        stream=response();extractor=Mock();extractor.cancelled.return_value=False;extractor._downloader.urlopen.return_value=stream
        with patch('vk_stream.socket.getaddrinfo',return_value=PUBLIC):formats=verified_audio_formats(extractor,CDN,'1_2','https://vk.ru/')
        self.assertEqual(formats[0]['ext'],'mp3');self.assertEqual(formats[0]['url'],CDN)
        stream.read.assert_called_once_with(4096)
        self.assertEqual(extractor._downloader.urlopen.call_args.args[0].headers['Range'],'bytes=0-4095')
        extractor._downloader.urlopen.side_effect=RuntimeError(CDN)
        with patch('vk_stream.socket.getaddrinfo',return_value=PUBLIC),self.assertRaises(VKStreamError) as error:verified_audio_formats(extractor,CDN,'1_2','https://vk.ru/')
        self.assertNotIn('DUMMY_SECRET',str(error.exception));self.assertNotIn('https://',str(error.exception))
    def test_extensionless_hls_uses_playlist_parser_and_excludes_drm_formats(self):
        extractor=Mock();extractor.cancelled.return_value=False
        extractor._downloader.urlopen.return_value=response(b'#EXTM3U\n','text/plain')
        extractor._extract_m3u8_formats.return_value=[{'url':'https://audio.example.net/public','has_drm':False},{'url':'https://audio.example.net/drm','has_drm':True}]
        with patch('vk_stream.socket.getaddrinfo',return_value=PUBLIC):formats=verified_audio_formats(extractor,CDN,'1_2','https://vk.ru/')
        self.assertEqual(len(formats),1);extractor._extract_m3u8_formats.assert_called_once()
    def test_reported_unknown_cdn_case_reaches_ytdlp_with_mp3_format(self):
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value='<html>VK shell</html>'),patch('vk_audio.web_audio_record',return_value=RAW),patch.object(ydl,'urlopen',return_value=response()) as request,patch('vk_stream.socket.getaddrinfo',return_value=PUBLIC):
            result=extract_vk_audio(ydl,URL+'_TestAccessKey123',download=False,settings={'vk_auth':'firefox'})
        self.assertEqual(result['url'],CDN);self.assertEqual(result['ext'],'mp3')
        self.assertEqual(result['http_headers']['Referer'],'https://vk.ru/')
        self.assertNotIn('TestAccessKey123',request.call_args.args[0].headers['Referer'])
    def test_non_audio_response_is_not_accepted_as_a_new_cdn(self):
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value='<html>VK shell</html>'),patch('vk_audio.web_audio_record',return_value=RAW),patch.object(ydl,'urlopen',return_value=response(b'<html>Not audio</html>','text/html')),patch('vk_stream.socket.getaddrinfo',return_value=PUBLIC):
            with self.assertRaisesRegex(VKAudioError,'VK-A07'):extract_vk_audio(ydl,URL,download=False)
    def test_cancelled_probe_does_not_start_network_request(self):
        extractor=Mock();extractor.cancelled.return_value=True
        with self.assertRaises(VKStreamError):verified_audio_formats(extractor,CDN,'1_2','https://vk.ru/')
        extractor._downloader.urlopen.assert_not_called()
    def test_public_probe_does_not_accept_private_redirect(self):
        extractor=Mock();extractor.cancelled.return_value=False;extractor._downloader.urlopen.return_value=response(url='https://127.0.0.1/private')
        with patch('vk_stream.public_https_target',side_effect=[True,False]),self.assertRaisesRegex(VKStreamError,'VK-A06'):verified_audio_formats(extractor,CDN,'1_2','https://vk.ru/')
