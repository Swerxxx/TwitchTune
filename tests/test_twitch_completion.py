import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from twitch_completion import TwitchCompletions
from twitch import TwitchClient, TwitchAPIError, MANAGE_SCOPE


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'queue.db'
        self.store=QueueStore(self.path)
        self.service=TwitchCompletions(self.store)
        self.ident=self.store.add('https://youtu.be/test',redemption='redeem',reward_id='reward')
        self.store.rate(self.ident,8)
        self.client=Mock(client_id='app',user={'user_id':'channel'},tokens={'access_token':'secret'})
        self.client.fulfill_redemption.return_value='FULFILLED'
        self.service.bind({'id':'redeem','reward':{'id':'reward'},'broadcaster_user_id':'channel'},'app')
    def tearDown(self):
        self.store.db.close(); self.temp.cleanup()
    def test_waits_for_listened_and_confirms_once_when_rated_before_end(self):
        self.assertEqual(self.service.flush(self.client),[])
        self.client.fulfill_redemption.assert_not_called()
        self.service.listened(self.store.get(self.ident),self.client)
        self.assertEqual(len(self.service.flush(self.client)),1)
        self.service.listened(self.store.get(self.ident),self.client)
        self.service.flush(self.client)
        self.client.fulfill_redemption.assert_called_once_with('reward','redeem')
    def test_listened_without_rating_stays_open_until_rating_saved(self):
        self.store.db.execute('UPDATE queue SET rating=NULL WHERE id=?',(self.ident,));self.store.db.commit()
        self.service.listened(self.store.get(self.ident),self.client)
        self.assertEqual(self.service.flush(self.client),[])
        self.client.fulfill_redemption.assert_not_called()
        self.store.rate(self.ident,8)
        self.assertEqual(len(self.service.flush(self.client)),1)
        self.client.fulfill_redemption.assert_called_once()

    def test_network_failure_survives_restart_and_retries(self):
        self.service.listened(self.store.get(self.ident))
        self.client.fulfill_redemption.side_effect=RuntimeError('network')
        self.service.flush(self.client)
        self.store.db.close(); self.store=QueueStore(self.path); self.service=TwitchCompletions(self.store)
        self.store.db.execute('UPDATE twitch_completions SET retry_at=0');self.store.db.commit()
        self.client.fulfill_redemption.side_effect=None
        self.assertEqual(len(self.service.flush(self.client)),1)
        self.assertEqual(self.client.fulfill_redemption.call_count,2)
    def test_other_account_and_client_cannot_send_pending_confirmation(self):
        self.service.listened(self.store.get(self.ident))
        other=Mock(client_id='app',user={'user_id':'other'},tokens={1:1})
        self.service.flush(other);other.fulfill_redemption.assert_not_called()
        other.user={'user_id':'channel'};other.client_id='other-app'
        self.service.flush(other);other.fulfill_redemption.assert_not_called()
    def test_permission_error_stops_polling_until_reauthorization(self):
        self.service.listened(self.store.get(self.ident))
        self.client.fulfill_redemption.side_effect=TwitchAPIError(403,'ownership')
        self.service.flush(self.client);self.service.flush(self.client)
        self.client.fulfill_redemption.assert_called_once()
        self.service.resume();self.client.fulfill_redemption.side_effect=None
        self.service.flush(self.client)
        self.assertEqual(self.client.fulfill_redemption.call_count,2)
    def test_deleted_or_canceled_order_is_not_fulfilled(self):
        self.service.listened(self.store.get(self.ident))
        self.store.cancel_redemption('redeem')
        self.service.flush(self.client)
        self.client.fulfill_redemption.assert_not_called()
    def test_no_manual_or_donation_request_is_sent(self):
        for redemption in (None,'donation:provider:id'):
            ident=self.store.add('https://youtu.be/other',redemption=redemption)
            self.service.listened(self.store.get(ident),self.client)
        self.service.flush(self.client)
        self.client.fulfill_redemption.assert_not_called()


class TwitchCompletionApiTests(unittest.TestCase):
    def client(self):
        c=TwitchClient('app',Mock())
        c.user={'user_id':'channel','scopes':[MANAGE_SCOPE]}
        return c
    def test_fulfill_uses_exact_reward_and_redemption(self):
        c=self.client()
        with patch.object(c,'api',return_value={'data':[{'id':'request','status':'FULFILLED'}]}) as api:
            self.assertEqual(c.fulfill_redemption('reward','request'),'FULFILLED')
        self.assertEqual(api.call_args.args,('PATCH','channel_points/custom_rewards/redemptions'))
        self.assertEqual(api.call_args.kwargs['json'],{'status':'FULFILLED'})
        self.assertEqual(api.call_args.kwargs['params'],{'broadcaster_id':'channel','reward_id':'reward','id':'request'})
    def test_already_fulfilled_after_lost_response_is_success(self):
        c=self.client()
        with patch.object(c,'api',side_effect=[TwitchAPIError(404,'not unfulfilled'),{'data':[{'id':'r','status':'FULFILLED'}]}]) as api:
            self.assertEqual(c.fulfill_redemption('reward','r'),'FULFILLED')
        self.assertEqual(api.call_args.args[0],'GET')
    def test_missing_manage_scope_never_sends_patch(self):
        c=self.client();c.user['scopes']=['channel:read:redemptions']
        with patch.object(c,'api') as api:
            with self.assertRaises(TwitchAPIError):c.fulfill_redemption('reward','r')
        api.assert_not_called()
    def test_create_reward_requires_user_input_and_keeps_request_unfulfilled(self):
        c=self.client()
        with patch.object(c,'api',return_value={'data':[{'id':'new'}]}) as api:
            self.assertEqual(c.create_music_reward('Музыка',1000)['id'],'new')
        data=api.call_args.kwargs['json']
        self.assertTrue(data['is_user_input_required'])
        self.assertFalse(data['should_redemptions_skip_request_queue'])
