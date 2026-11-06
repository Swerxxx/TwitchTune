import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from twitch import TwitchClient

class VipCancelTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.path=Path(self.folder.name)/'q.db'
        self.store=QueueStore(self.path)

    def tearDown(self):
        self.store.db.close();self.folder.cleanup()

    def add(self,nick='Viewer',redemption=None,viewer_id=None):
        return self.store.add('https://youtu.be/test',nick,redemption,viewer_id=viewer_id)

    def test_three_vip_orders_are_fifo_then_regular_reset_and_revoke(self):
        first,second=self.add('regular'),self.add('regular')
        key=self.store.grant_vip('VIPViewer')
        vip=[self.add('VIPViewer') for _ in range(4)]
        self.assertEqual([r['id'] for r in self.store.rows('active')],vip[:3]+[first,second,vip[3]])
        self.assertEqual([self.store.get(i)['vip_priority'] for i in vip],[1,1,1,0])
        self.assertEqual(self.store.profiles()[0]['vip_used'],3)
        self.store.set_vip(key,True,reset=True)
        fresh=self.add('VIPViewer')
        self.assertEqual([r['id'] for r in self.store.rows('active')],vip[:3]+[fresh,first,second,vip[3]])
        self.store.set_vip(key,False)
        regular=self.add('VIPViewer')
        self.assertEqual(self.store.get(regular)['vip_priority'],0)

    def test_playing_song_is_kept_first_and_requeue_does_not_reuse_priority(self):
        playing=self.add('regular');self.store.update(playing,status='Играет')
        self.add('regular')
        self.store.grant_vip('VIPViewer');vip=self.add('VIPViewer')
        self.assertEqual([r['id'] for r in self.store.rows('active')][:2],[playing,vip])
        self.store.update(vip,status='Прослушано');self.store.requeue(vip)
        self.assertEqual(self.store.get(vip)['vip_priority'],0)

    def test_vip_identity_and_quota_survive_rename_duplicates_delete_and_restart(self):
        self.store.grant_vip('FirstName')
        one=self.add('FirstName','order-1','42')
        self.add('NewName','order-2','42')
        self.assertIsNone(self.add('NewName','order-2','42'))
        profile=self.store.profiles()[0]
        self.assertEqual((profile['viewer_key'],profile['nickname'],profile['vip_used']),('twitch:42','NewName',2))
        self.store.cancel_redemption('order-1')
        self.assertIsNone(self.store.get(one))
        self.store.db.close();self.store=QueueStore(self.path)
        self.assertEqual(self.store.profiles()[0]['vip_used'],2)
        for row in self.store.rows():self.store.delete(row['id'])
        self.assertEqual(self.store.profiles()[0]['vip'],1)
        self.assertEqual(self.store.profiles()[0]['total'],0)

    def test_concurrent_submissions_spend_at_most_three_priority_slots(self):
        self.store.grant_vip('Viewer')
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids=list(pool.map(lambda _:self.add(),range(8)))
        self.assertEqual(sum(self.store.get(i)['vip_priority'] for i in ids),3)
        self.assertEqual(self.store.profiles()[0]['vip_used'],3)

    def test_cancel_before_add_tombstones_and_never_consumes_vip_quota(self):
        self.store.grant_vip('Viewer')
        self.store.cancel_redemption('cancel-first')
        self.assertIsNone(self.add('Viewer','cancel-first'))
        self.assertEqual(self.store.profiles()[0]['vip_used'],0)

    def test_update_notifications_are_not_mistaken_for_new_orders(self):
        emit=Mock();client=TwitchClient('id',emit)
        for suffix,event_name in (('update','redemption_update'),('add','redemption')):
            event={'id':'order','status':'canceled'}
            client.notification({'metadata':{'subscription_type':'channel.channel_points_custom_reward_redemption.'+suffix},'payload':{'event':event}})
            emit.assert_called_with(event_name,event)
