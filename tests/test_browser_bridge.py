import io
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from twitch_completion import TwitchCompletions
from browser_bridge import BrowserBridge, MAX_MESSAGE
from browser_native_host import read_message, write_message


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.profile=Path(self.temp.name)
        self.store=QueueStore(self.profile/'queue.sqlite3');self.completions=TwitchCompletions(self.store)
        self.bridge=BrowserBridge(self.profile)
        self.client=SimpleNamespace(client_id='app',user={'user_id':'channel','login':'streamer'})
        self.bridge.heartbeat(True,self.client)
        self.ident=self.store.add('https://youtu.be/a',redemption='redemption',reward_id='reward')
        self.event=dict(id='redemption',broadcaster_user_id='channel',broadcaster_user_login='streamer',
                        reward={'id':'reward','title':'Музыка'},user_login='viewer',user_input='https://youtu.be/a')
        self.completions.bind(self.event,'app')
    def tearDown(self):
        self.bridge.close(); self.store.db.close(); self.temp.cleanup()
    def claim(self):return self.bridge.handle({'action':'claim','channel':'streamer'})['task']
    def ready(self):
        self.completions.listened(self.store.get(self.ident))
        self.store.finish(self.ident)
        self.store.rate(self.ident,8)
    def report(self,task,outcome):
        return self.bridge.handle(dict(action='report',channel='streamer',id=task['id'],lease=task['lease'],outcome=outcome))
    def test_rating_alone_and_playback_alone_cannot_claim(self):
        self.store.rate(self.ident,8);self.assertIsNone(self.claim())
        self.store.db.execute('UPDATE queue SET rating=NULL WHERE id=?',(self.ident,));self.store.db.commit()
        self.completions.listened(self.store.get(self.ident));self.assertIsNone(self.claim())
        self.store.finish(self.ident);self.store.rate(self.ident,8)
        self.assertEqual(self.claim()['id'],'redemption')
    def test_two_browsers_cannot_claim_same_request(self):
        self.ready();task=self.claim();self.assertIsNone(self.claim())
        self.assertTrue(self.bridge.actionable(task['id'],task['lease'],'streamer'))
    def test_wrong_channel_expired_session_and_disabled_mode_are_rejected(self):
        self.ready()
        with self.assertRaises(ValueError):self.bridge.handle({'action':'claim','channel':'somebody'})
        self.bridge.heartbeat(False,self.client)
        with self.assertRaises(ValueError):self.claim()
        self.bridge.heartbeat(True,self.client)
        self.bridge.db.execute('UPDATE browser_bridge_session SET expires=0');self.bridge.db.commit()
        with self.assertRaises(ValueError):self.claim()
    def test_click_is_not_success_until_twitch_confirms_exact_redemption(self):
        self.ready();task=self.claim()
        self.assertEqual(self.report(task,'clicked')['state'],'browser_verify')
        self.assertIsNone(self.claim())
        self.assertFalse(self.completions.remote_status(dict(self.event,broadcaster_user_id='foreign',status='FULFILLED')))
        self.assertTrue(self.completions.remote_status(dict(self.event,status='FULFILLED')))
        self.assertEqual(self.bridge.db.execute('SELECT state FROM twitch_completions').fetchone()[0],'fulfilled')
    def test_event_arriving_before_report_cannot_be_overwritten(self):
        self.ready();task=self.claim()
        self.completions.remote_status(dict(self.event,status='FULFILLED'))
        self.assertEqual(self.report(task,'clicked')['state'],'fulfilled')
    def test_deleted_canceled_or_skipped_requests_are_not_actionable(self):
        self.ready();task=self.claim();self.store.cancel_redemption('redemption')
        self.assertFalse(self.bridge.actionable(task['id'],task['lease'],'streamer'))
    def test_skipped_rated_request_cannot_be_claimed(self):
        self.ready();self.store.finish(self.ident,'Пропущено')
        self.assertIsNone(self.claim())
    def test_wrong_lease_and_arbitrary_commands_are_rejected(self):
        self.ready();task=self.claim();task['lease']='x'*48
        with self.assertRaises(ValueError):self.report(task,'clicked')
        with self.assertRaises(ValueError):self.bridge.handle({'action':'delete_all'})
    def test_claim_expires_and_can_be_recovered_but_clicked_never_replays(self):
        self.ready();first=self.claim()
        self.bridge.db.execute('UPDATE twitch_completions SET lease_until=0');self.bridge.db.commit()
        second=self.claim();self.assertNotEqual(first['lease'],second['lease'])
        self.report(second,'clicked')
        self.bridge.db.execute('UPDATE twitch_completions SET lease_until=0,retry_at=0');self.bridge.db.commit()
        self.assertIsNone(self.claim())
    def test_duplicate_known_orders_are_exposed_and_metadata_is_preserved(self):
        self.ready()
        self.completions.bind(dict(self.event,id='duplicate'),'app')
        task=self.claim();self.assertFalse(task['uniqueKnownOrder']);self.assertEqual(task['viewer'],'viewer')
        self.assertEqual(task['input'],'https://youtu.be/a')
    def test_unrelated_client_cannot_receive_task(self):
        self.ready();self.bridge.heartbeat(True,SimpleNamespace(client_id='other',user=self.client.user))
        self.assertIsNone(self.claim())

    def test_claim_only_considers_card_ids_from_the_open_page(self):
        self.ready()
        for visible in ([], ['off-page']):
            with self.subTest(visible=visible):
                reply = self.bridge.handle(dict(action='claim', channel='streamer', visibleIds=visible))
                self.assertIsNone(reply['task'])
        reply = self.bridge.handle(dict(action='claim', channel='streamer', visibleIds=['redemption']))
        self.assertEqual(reply['task']['id'], 'redemption')

    def test_listened_and_rated_legacy_order_can_be_adopted(self):
        ident = self.store.add('https://youtu.be/a', redemption='legacy', reward_id='reward')
        self.store.rate(ident, 8)
        self.completions.adopt_browser_history(self.client, ['reward'])
        self.assertIsNone(self.claim())
        self.store.finish(ident)
        self.completions.adopt_browser_history(self.client, ['other'])
        self.assertIsNone(self.claim())
        self.completions.adopt_browser_history(self.client, ['reward'])
        self.assertEqual(self.claim()['id'], 'legacy')

    def test_twitch_only_history_can_be_enqueued_and_processed_once(self):
        order = dict(id='daf11e60-ad17-439f-aa41-f2cfff6d93eb', rewardId='ad60aa17-a861-4218-abc2-91df2482728d',
                     viewer='Viewer', viewerId='123', input='https://youtu.be/a')
        message = dict(action='import_history', channel='streamer', orders=[order])
        with self.assertRaises(ValueError):
            self.bridge.handle(dict(message, channel='other'))
        self.assertEqual(self.bridge.handle(message)['queued'], 1)
        self.assertEqual(self.bridge.handle(message)['queued'], 0)
        event, = self.bridge.pending_history()
        self.assertEqual(event['reward']['id'], order['rewardId'])
        self.assertEqual(event['broadcaster_user_id'], 'channel')
        self.store.add(event['user_input'], event['user_name'], event['id'], reward_id=event['reward']['id'])
        self.bridge.acknowledge_history(event['id'], self.client)
        self.assertEqual(self.bridge.handle(message)['queued'], 0)
        self.assertEqual(self.bridge.pending_history(), [])


class NativeProtocolTests(unittest.TestCase):
    def test_utf8_round_trip(self):
        stream=io.BytesIO();value={'action':'status','text':'Тест'};write_message(stream,value);stream.seek(0)
        self.assertEqual(read_message(stream),value)
        self.assertIsNone(read_message(stream))
    def test_truncated_and_oversized_messages_are_rejected(self):
        for data in (b'x',struct.pack('=I',MAX_MESSAGE+1),struct.pack('=I',10)+b'{}'):
            with self.assertRaises(ValueError):read_message(io.BytesIO(data))
