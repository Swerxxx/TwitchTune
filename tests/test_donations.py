import io
import json
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
from urllib.parse import parse_qs,urlparse,urlencode
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from donations import normalize_event,priced_message,priced_message_checked,allocate_orders
from donation_clients import DonationClient,AuthError
from donation_auth import authorize,authorization_url


def raw_donation(ident='one',**extra):
    return dict(dict(id=ident,username='viewer',amount=200,currency='RUB',message='Песня https://youtu.be/test',timestamp='2026-11-06T10:00:00Z'),**extra)

def event(ident='one',provider='donatex',**extra):
    return normalize_event(provider,'account',raw_donation(ident,**extra))

class DonationCoreTests(unittest.TestCase):
    def setUp(self):self.store=QueueStore(':memory:')
    def tearDown(self):self.store.db.close()
    def add_paid(self,ident,provider='donatex',**extra):
        paid=event(ident,provider,**extra)
        return self.store.add(paid['url'],paid['viewer'],redemption=paid['event_key'],donation=paid)

    def test_donations_above_vip_fifo_without_interrupting_current(self):
        playing=self.store.add('https://youtu.be/playing','playing')
        self.store.update(playing,status='Играет')
        ordinary=self.store.add('https://youtu.be/ordinary','ordinary')
        self.store.grant_vip('vip')
        vip=self.store.add('https://youtu.be/vip','vip')
        later=self.add_paid('later',timestamp='2026-11-06T10:02:00Z')
        earlier=self.add_paid('earlier',provider='donationalerts',timestamp='2026-11-06T10:01:00Z')
        second_vip=self.store.add('https://youtu.be/vip2','vip')
        self.assertEqual([r['id'] for r in self.store.rows('active')],[playing,earlier,later,vip,second_vip,ordinary])
        self.assertEqual(self.store.get(playing)['status'],'Играет')

    def test_persistent_inbox_dedup_and_delete_prevent_resurrection(self):
        paid=event()
        self.assertTrue(self.store.record_donation(paid));self.assertFalse(self.store.record_donation(paid))
        ident=self.add_paid('one');self.store.donation_added(paid['event_key'],ident)
        self.store.delete(ident)
        self.assertFalse(self.store.record_donation(paid))
        self.assertIsNone(self.store.add(paid['url'],paid['viewer'],redemption=paid['event_key'],donation=paid))
        self.assertEqual(self.store.pending_donations(),[])

    def test_donor_nickname_does_not_impersonate_twitch_profile_or_spend_vip(self):
        original=self.store.add('https://youtu.be/a','viewer',viewer_id='123')
        self.store.set_vip('twitch:123',True)
        paid=self.add_paid('one')
        self.assertNotEqual(self.store.get(paid)['viewer_key'],self.store.get(original)['viewer_key'])
        self.assertTrue(self.store.get(paid)['viewer_key'].startswith('donor:donatex:'))
        self.assertEqual(next(p['vip_used'] for p in self.store.profiles() if p['viewer_key']=='twitch:123'),0)
        other=self.add_paid('two',provider='donationalerts')
        self.assertNotEqual(self.store.get(paid)['viewer_key'],self.store.get(other)['viewer_key'])

    def test_history_requeue_does_not_purchase_priority_again(self):
        paid=self.add_paid('one');self.store.rate(paid,10);self.store.finish(paid)
        self.store.requeue(paid)
        self.assertEqual(self.store.get(paid)['donation_priority'],0)
        self.assertEqual(self.store.get(paid)['donation_provider'],'donatex')

    def test_paid_download_jobs_are_taken_before_waiting_regular_downloads(self):
        from media import Downloader
        from albums import create_album
        from test_albums import album_metadata
        normal=self.store.add('https://youtu.be/regular')
        paid=self.add_paid('one')
        album_event=event('album',message='https://music.yandex.ru/album/123')
        with tempfile.TemporaryDirectory() as folder:
            album,children=create_album(self.store,Path(folder)/'Альбомы',album_metadata(),'viewer',redemption=album_event['event_key'],donation=album_event)
            with patch('media.threading.Thread.start'):
                downloader=Downloader(self.store,Path(folder)/'tracks',lambda *_:None)
            downloader.submit(normal);downloader.submit(paid)
            for child in children:downloader.submit(child)
            downloader.submit(paid) # Same id cannot create a duplicate job.
            jobs=[downloader.jobs.get_nowait()[3] for _ in range(4)]
            self.assertEqual(jobs,[paid,*children,normal])

    def test_priority_and_pending_inbox_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'q.db';store=QueueStore(path)
            original=store.add('https://youtu.be/ordinary','viewer')
            store.update(original,status='Играет')
            paid=event();store.record_donation(paid)
            ident=store.add(paid['url'],paid['viewer'],redemption=paid['event_key'],donation=paid)
            store.update(ident,status='Поиск замены')
            store.db.close();store=QueueStore(path)
            try:
                self.assertEqual(store.rows('active')[0]['id'],ident)
                self.assertEqual(store.get(ident)['status'],'Ожидает загрузки')
                self.assertEqual(store.pending_donations()[0]['event_key'],paid['event_key'])
                store.donation_added(paid['event_key'],ident)
                self.assertEqual(store.pending_donations(),[])
            finally:store.db.close()

    def test_album_failure_does_not_make_paid_request_permanently_deleted(self):
        from albums import create_album
        from test_albums import album_metadata
        paid=event(message='https://music.yandex.ru/album/123')
        metadata=album_metadata();metadata['tracks'][0]['duration']='invalid'
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                create_album(self.store,Path(root)/'Альбомы',metadata,paid['viewer'],redemption=paid['event_key'],donation=paid)
            metadata=album_metadata()
            album,ids=create_album(self.store,Path(root)/'Альбомы',metadata,paid['viewer'],redemption=paid['event_key'],donation=paid)
            self.assertTrue(album);self.assertEqual(len(ids),2)

    def test_normalization_filters_invalid_events_and_uses_music_link(self):
        for extra in (dict(amount=0),dict(amount=-1),dict(amount='NaN'),dict(isTest=True),dict(isPotentiallyUnsafe=True),
                      dict(message='в тг https://youtu.be/a'),dict(message='https://youtu.be/a https://youtu.be/b'),
                      dict(message='https://music.yandex.ru:bad/album/123'),dict(message='https://example.com/a'),dict(message='без ссылки')):
            self.assertIsNone(normalize_event('donatex','account',raw_donation(**extra)),extra)
        self.assertIsNotNone(normalize_event('donatex','account',raw_donation(amount=0,isTest=True),True))
        chosen=normalize_event('donatex','account',raw_donation(message='hello',musicLink='https://soundcloud.com/artist/song'))
        self.assertEqual(chosen['url'],'https://soundcloud.com/artist/song')
        self.assertIsNotNone(event(message='https://music.yandex.ru/album/123'))

    def test_price_threshold_floor_multiple_links_and_currency(self):
        raw=raw_donation(message='https://youtu.be/one https://youtu.be/two https://youtu.be/three')
        for amount,count in [('199.99',0),(200,1),(399,1),(400,2),(550,2),(600,3)]:
            receipt=priced_message('donationalerts','account',dict(raw,amount=amount))
            self.assertEqual(len(allocate_orders(receipt)),count)
        receipt=priced_message('donatex','account',dict(raw,amount=750),250)
        self.assertEqual(len(allocate_orders(receipt)),3)
        receipt=priced_message('donatex','account',dict(raw,amount=1000,currency='USD'))
        self.assertEqual(allocate_orders(receipt),[])

    def test_false_flags_from_api_are_not_treated_as_true_strings(self):
        raw=raw_donation(is_test='0',isTest='false',is_potentially_unsafe='0',isPotentiallyUnsafe='False',currency=' rub ')
        receipt,reason=priced_message_checked('donationalerts','account',raw)
        self.assertEqual(reason,'');self.assertEqual(receipt['paid_slots'],1)
        self.assertIsNone(priced_message('donationalerts','account',dict(raw,is_test='1')))
        self.assertIsNone(priced_message('donationalerts','account',dict(raw,is_potentially_unsafe='true')))

    def test_actual_donationalerts_donations_type_is_accepted_without_relaxing_payment_rules(self):
        raw=dict(id=900000001,name='Donations',username='viewer',message_type='text',amount=200,currency='RUB',
                 message='https://music.yandex.ru/album/19792517/track/96567677?utm_source=web&utm_medium=copy_link',created_at='2026-11-06 17:29:40')
        for name in ('Donations','donations',' DONATIONS ','donation','Donation'):
            receipt,reason=priced_message_checked('donationalerts','account',dict(raw,name=name))
            self.assertEqual(reason,'');self.assertEqual(receipt['paid_slots'],1)
        receipt,reason=priced_message_checked('donationalerts','account',dict(raw,amount=199))
        self.assertEqual(receipt['paid_slots'],0);self.assertIn('ниже минимума',reason)
        self.assertIsNone(priced_message('donationalerts','account',dict(raw,name='subscription')))
        self.assertIsNone(priced_message('donatex','account',raw))

    def test_rejection_reasons_distinguish_missing_link_price_and_api_fields(self):
        cases=[(dict(message='hello'),'нет ссылки'),(dict(name='custom'),'Тип события'),
               (dict(currency=''),'код валюты'),(dict(amount='bad'),'формат суммы'),
               (dict(amount=199),'ниже минимума'),(dict(is_test=True),'Тестовое')]
        for extra,expected in cases:
            receipt,reason=priced_message_checked('donationalerts','account',raw_donation(**extra))
            self.assertIn(expected,reason)

    def test_paid_surplus_never_duplicates_one_link_and_does_not_count_identical_urls_twice(self):
        receipt=priced_message('donatex','account',raw_donation(amount=600,message='https://youtu.be/one https://youtu.be/one'))
        self.assertEqual(len(allocate_orders(receipt)),1)
        self.assertEqual(len(allocate_orders(priced_message('donatex','account',raw_donation(amount='1e1000')))),1)

    def test_album_is_charged_per_song_and_accepted_whole(self):
        from test_albums import album_metadata
        raw=raw_donation(amount=400,message='https://music.yandex.ru/album/123 https://youtu.be/one')
        receipt=priced_message('donationalerts','account',raw)
        orders=allocate_orders(receipt,lambda _:album_metadata())
        self.assertEqual(len(orders),1);self.assertEqual(orders[0]['paid_tracks'],2)
        self.assertIn('album_metadata',orders[0])
        receipt=priced_message('donationalerts','account',dict(raw,amount=200))
        orders=allocate_orders(receipt,lambda _:album_metadata())
        self.assertEqual([o['url'] for o in orders],['https://youtu.be/one'])

    def test_receipt_expansion_is_atomic_and_cannot_be_repriced_on_replay(self):
        receipt=priced_message('donatex','account',raw_donation(amount=400,message='https://youtu.be/one https://youtu.be/two https://youtu.be/three'))
        self.assertTrue(self.store.record_donation(receipt))
        orders=allocate_orders(receipt)
        self.assertTrue(self.store.expand_donation(receipt['event_key'],orders))
        self.assertEqual(len(self.store.pending_donations()),2)
        cheaper=priced_message('donatex','account',raw_donation(amount=400,message='https://youtu.be/one https://youtu.be/two https://youtu.be/three'),100)
        self.assertFalse(self.store.record_donation(cheaper))
        self.assertFalse(self.store.expand_donation(receipt['event_key'],allocate_orders(cheaper)))
        self.assertEqual(len(self.store.pending_donations()),2)

    def test_captured_price_and_expanded_children_survive_restart_without_duplication(self):
        receipt=priced_message('donatex','account',raw_donation(amount=400,message='https://youtu.be/one https://youtu.be/two https://youtu.be/three'))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'q.db';store=QueueStore(path)
            store.record_donation(receipt);store.db.close();store=QueueStore(path)
            pending=store.pending_donations()[0]
            self.assertEqual(pending['unit_price'],200)
            self.assertEqual(len(allocate_orders(pending)),2)
            store.expand_donation(pending['event_key'],allocate_orders(pending));store.db.close();store=QueueStore(path)
            try:
                self.assertEqual(len(store.pending_donations()),2)
                self.assertFalse(store.record_donation(receipt))
                self.assertFalse(store.expand_donation(receipt['event_key'],allocate_orders(receipt)))
            finally:store.db.close()

class FakeSocket:
    def __init__(self,client,frames):self.client,self.frames,self.sent=client,list(frames),[]
    def settimeout(self,value):pass
    def send(self,value):self.sent.append(value)
    def recv(self):
        if self.frames:return self.frames.pop(0)
        self.client.stopping.set()
        return '{}' if self.client.provider=='donationalerts' else '{"type":6}\x1e'
    def close(self,**kwargs):pass

class DonationProtocolTests(unittest.TestCase):
    def test_da_poll_detects_backdated_manual_insert_without_importing_old_history(self):
        emitted=[];client=DonationClient('donationalerts','secret',lambda *x:emitted.append(x))
        client.account='123';client.started_at=100
        old=raw_donation('old',created_at='1970-01-01 00:00:01')
        manual=raw_donation('manual',created_at='1970-01-01 00:00:02')
        with patch.object(client,'fetch_da_history',side_effect=[[old],[manual,old],[manual,old]]):
            client.reconcile_da();client.reconcile_da();client.reconcile_da()
        self.assertEqual([x[1][1]['id'] for x in emitted if x[0]=='donation'],['manual'])

    def test_da_recovery_reads_new_pages_and_retries_after_api_failure(self):
        emitted=[];client=DonationClient('donationalerts','secret',lambda *x:emitted.append(x))
        client.account='123';client.da_seen={'old'}
        a=raw_donation('a',created_at='2026-11-06 12:00:00')
        b=raw_donation('b',created_at='2026-11-06 12:01:00')
        with patch.object(client,'fetch_da_history',side_effect=[[b],RuntimeError()]):
            with self.assertRaises(RuntimeError):client.reconcile_da()
        self.assertEqual(client.da_seen,{'old'})
        self.assertFalse(any(x[0]=='donation' for x in emitted))
        with patch.object(client,'fetch_da_history',side_effect=[[b],[a,raw_donation('old')]]) as fetch:
            client.reconcile_da()
        self.assertEqual([x[1][1]['id'] for x in emitted if x[0]=='donation'],['a','b'])
        self.assertEqual(fetch.call_args_list[-1].args,(2,))

    def test_da_history_monitor_starts_even_if_websocket_is_unavailable(self):
        client=DonationClient('donationalerts','secret',lambda *_:None)
        with patch.object(client,'request',return_value=dict(data=dict(id=123))),patch.object(client,'catchup') as catchup, \
                patch('donation_clients.websocket.create_connection',side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):client.connect_da()
        catchup.assert_called_once();self.assertEqual(client.account,'123')

    def test_donationalerts_documented_connect_subscribe_publication(self):
        emitted=[];client=DonationClient('donationalerts','secret',lambda *x:emitted.append(x));client.catchup=Mock()
        channel='$alerts:donation_123'
        frames=[json.dumps(dict(id=1,result=dict(client='socket-id'))),
                json.dumps(dict(result=dict(type=1,channel=channel,data=dict(info={})))),
                json.dumps(dict(result=dict(channel=channel,data=dict(data=raw_donation()))))]
        socket=FakeSocket(client,frames)
        with patch.object(client,'request',side_effect=[dict(data=dict(id=123,name='streamer',socket_connection_token='socket-token')),dict(channels=[dict(channel=channel,token='private-token')])]) as request, \
                patch('donation_clients.websocket.create_connection',return_value=socket):
            client.connect_da()
        self.assertEqual(json.loads(socket.sent[1])['method'],1)
        self.assertEqual(request.call_args.kwargs['json'],dict(channels=[channel],client='socket-id'))
        self.assertEqual([x[1][1]['id'] for x in emitted if x[0]=='donation'],['one'])
        client.catchup.assert_called_once()

    def test_donatex_signalr_handles_fragmented_and_batched_records(self):
        emitted=[];client=DonationClient('donatex','secret',lambda *x:emitted.append(x));client.catchup=Mock()
        packet=json.dumps(dict(type=1,target='DonationCreated',arguments=[raw_donation()]))+'\x1e'
        socket=FakeSocket(client,['{}\x1e'+packet[:20],packet[20:]+'{"type":6}\x1e'])
        with patch.object(client,'request',side_effect=[dict(id='account',username='streamer'),dict(connectionToken='connection-token',availableTransports=[dict(transport='WebSockets')])]), \
                patch('donation_clients.websocket.create_connection',return_value=socket) as connect:
            client.connect_dx()
        self.assertEqual(socket.sent[0],'{"protocol":"json","version":1}\x1e')
        self.assertNotIn('secret',connect.call_args.args[0])
        self.assertEqual(connect.call_args.kwargs['header']['Authorization'],'Bearer secret')
        self.assertEqual([x[1][1]['id'] for x in emitted if x[0]=='donation'],['one'])

    def test_history_is_only_recovered_since_this_session_and_sorted(self):
        emitted=[];client=DonationClient('donatex','secret',lambda *x:emitted.append(x));client.account='account';client.started_at=100
        rows=[raw_donation('later',timestamp='1970-01-01T00:03:00Z'),raw_donation('earlier',timestamp='1970-01-01T00:02:00Z'),raw_donation('old',timestamp='1970-01-01T00:00:01Z')]
        with patch.object(client,'request',return_value=rows):
            client.catchup();client.history_thread.join(3)
        self.assertFalse(client.alive())
        self.assertEqual([x[1][1]['id'] for x in emitted if x[0]=='donation'],['earlier','later'])

    def test_http_auth_error_never_discloses_token_or_response(self):
        client=DonationClient('donatex','private-token',lambda *_:None)
        response=SimpleNamespace(status_code=401)
        with patch('donation_clients.requests.request',return_value=response):
            with self.assertRaises(AuthError) as caught:client.request('GET','v1/user/me')
        self.assertNotIn('private-token',str(caught.exception))

class DonationAuthTests(unittest.TestCase):
    def test_loopback_login_validates_state_and_receives_fragment_by_post(self):
        holder={};responses=[]
        class Server:
            def __init__(self,address,handler):holder.update(server=self,handler=handler);self.calls=0
            def handle_request(self):
                handler=object.__new__(holder['handler']);self.calls+=1
                state='wrong' if self.calls==1 else holder['state']
                body=urlencode(dict(state=state,access_token='private-token')).encode()
                handler.path='/callback';handler.headers={'Origin':'http://127.0.0.1:8765','Content-Length':str(len(body))}
                handler.rfile=io.BytesIO(body);handler.wfile=io.BytesIO()
                handler.send_response=lambda status:responses.append(status)
                handler.send_header=lambda *_:None;handler.end_headers=lambda:None
                handler.do_POST()
            def server_close(self):holder['closed']=True
        def opener(url):holder['state']=parse_qs(urlparse(url).query)['state'][0]
        with patch('donation_auth.HTTPServer',Server):token=authorize('123',opener=opener)
        self.assertEqual(token,'private-token');self.assertEqual(responses,[403,200]);self.assertTrue(holder['closed'])
        query=parse_qs(urlparse(authorization_url('123','state')).query)
        self.assertEqual(query['response_type'],['token']);self.assertNotIn('client_secret',query)
