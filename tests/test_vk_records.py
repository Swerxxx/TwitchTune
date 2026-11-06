"""Synthetic VK JSON envelopes and diagnostic stages; no credentials or live URLs."""
import sys,json,unittest
from pathlib import Path
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from yt_dlp import YoutubeDL
from vk_records import structured_records
from vk_audio import audio_record,record_by_id,TwitchTuneVKAudioIE,extract_vk_audio,VKAudioError
from test_vk_audio import URL,STREAM,page

IDENT='-2001062996_148062996'
RAW=dict(id=148062996,owner_id=-2001062996,title='Test Song',artist='Test Artist',duration=120,url=STREAM)


class VKRecordsTests(unittest.TestCase):
    def setUp(self):
        import vk_audio
        vk_audio._reload_after=0
    def test_json_script_and_audio_page_init_are_read_without_executing_js(self):
        state={'playlist':{'list':[RAW]}}
        for script in ('<script type="application/json">'+json.dumps(state)+'</script>',
                       '<script>new AudioPage('+json.dumps(state)+'); throw Error("must never execute")</script>'):
            result=audio_record(script,IDENT)
            self.assertEqual(result['url'],STREAM)
    def test_nested_envelopes_and_long_arrays_are_not_mistaken_for_tracks(self):
        for payload in ({'data':[[RAW]]},{'payload':[0,[[RAW]]]},{'response':{'items':[RAW]}},[{}, {}, {}, {}, {}, {'playlist':{'list':[RAW]}}]):
            self.assertEqual(record_by_id(structured_records(payload),IDENT)['url'],STREAM)
    def test_camel_case_owner_and_explicit_full_id(self):
        for raw in (dict(RAW,ownerId=RAW['owner_id']),dict(RAW,fullId=IDENT)):
            raw.pop('owner_id')
            self.assertEqual(record_by_id(structured_records(raw),IDENT)['url'],STREAM)
        self.assertFalse(structured_records(dict(RAW,fullId='1_2')))
    def test_malformed_json_or_unrelated_data_never_selects_another_track(self):
        for script in ('<script>new AudioPage(not_json)</script>', '<script type="application/json">'+json.dumps({'id':77,'name':'Account'})+'</script>',page(id=999)):
            self.assertIsNone(audio_record(script,IDENT))
    def test_complete_record_is_preferred_over_duplicate_metadata_stub(self):
        result=audio_record(page(url='')+'<script type="application/json">'+json.dumps(RAW)+'</script>',IDENT)
        self.assertEqual(result['url'],STREAM)
    def test_html_labels_are_cleaned_before_using_metadata(self):
        result=record_by_id([dict(RAW,artist='<b>Test Artist</b>',title='Test &amp; Song')],IDENT)
        self.assertEqual(result['artist'],'Test Artist');self.assertEqual(result['track'],'Test & Song')
    def test_json_initialized_track_reaches_extractor(self):
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value='<script>new AudioPage('+json.dumps({'playlist':{'list':[RAW]}})+')</script>'):
            self.assertEqual(extract_vk_audio(ydl,URL,download=False)['url'],STREAM)
    def test_payload_playback_response_is_accepted(self):
        with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch('vk_audio.attach_vk_session',return_value=True),patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value=page(url='',actionHash='action',urlHash='hash')),patch.object(TwitchTuneVKAudioIE,'_download_json',return_value={'payload':[0,[[RAW]]]}):
            self.assertEqual(extract_vk_audio(ydl,URL,download=False)['url'],STREAM)
    def test_missing_track_and_invalid_stream_have_distinct_safe_codes(self):
        for html,code in [('<html>Unknown shell</html>','VK-A01'),(page(url='https://example.com/private.mp3?secret=1'),'VK-A04')]:
            with YoutubeDL({'quiet':True,'no_warnings':True},auto_init=False) as ydl,patch.object(TwitchTuneVKAudioIE,'_download_webpage',return_value=html):
                with self.assertRaises(VKAudioError) as error:extract_vk_audio(ydl,URL,download=False)
            self.assertIn(code,str(error.exception));self.assertNotIn('secret',str(error.exception))
