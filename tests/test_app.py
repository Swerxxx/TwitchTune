"""Offline GUI regression checks; no Twitch account or downloads required."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from threading import Event
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QThread, QUrl, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer
import app as module


class FakeDownloader:
    def __init__(self, *args, **kwargs):
        self.stop = Event()
        self.submitted = []
    def submit(self, ident):
        self.submitted.append(ident)


class FakePlayer:
    def __init__(self):
        self.state = QMediaPlayer.StoppedState
        self.source = QUrl()
    def play(self): self.state = QMediaPlayer.PlayingState
    def pause(self): self.state = QMediaPlayer.PausedState
    def stop(self): self.state = QMediaPlayer.StoppedState
    def setSource(self, source): self.source = source
    def playbackState(self): return self.state


class FakeClient:
    def __init__(self, client_id, emit):
        self.client_id, self.emit = client_id, emit
        self.tokens, self.user, self.last_validate = {}, {}, 0
        self.closed = False
        self.connected = None
    def close(self): self.closed = True
    def connect(self, reward_id): self.connected = reward_id


class FakeLyrics:
    def search(self, *args, **kwargs):
        return []


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = [patch.object(module, 'APP', Path(self.folder.name)),
                        patch.object(module, 'Downloader', FakeDownloader),
                        patch.object(module, 'TwitchClient', FakeClient),
                        patch.object(module, 'load_saved_cover', return_value=''),
                        patch.object(module, 'load_album_cover', return_value=''),
                        patch.object(module, 'LyricsClient', FakeLyrics),
                        patch('lyrics_sources.GeniusProvider.search', return_value=[]),
                        patch('lyrics_sources.LyricsOvhProvider.search', return_value=[]),
                        patch('lyrics_sources.YandexLyricsProvider.search', return_value=[])]
        for mock in self.patches: mock.start()
        self.window = module.Main()
        self.real_player = self.window.player
        self.window.player = FakePlayer()

    def tearDown(self):
        self.window.close()
        deadline = time.monotonic() + 3
        while self.window.jobs and time.monotonic() < deadline:
            self.application.processEvents()
        self.assertFalse(self.window.jobs, 'Worker did not finish during shutdown')
        self.window.store.db.close()
        self.window.deleteLater()
        self.application.processEvents()
        for mock in reversed(self.patches): mock.stop()
        self.folder.cleanup()

    def ready(self, viewer='nick'):
        ident = self.window.store.add('https://youtu.be/test', viewer)
        path = Path(self.folder.name) / (ident + '.wav')
        path.write_bytes(b'offline-test-placeholder')
        self.window.store.update(ident, status='Готово', path=str(path), title='Test Song')
        self.window.refresh()
        return ident

    def test_local_mp3_batch_import_keeps_playback_and_skips_network_downloader(self):
        current = self.ready()
        self.window.next_track()
        self.window.viewer.setText('mp3_viewer')
        fixture = str(Path(__file__).parent / 'fixtures' / 'silence-6s.mp3')
        bad = Path(self.folder.name) / 'bad.mp3'
        bad.write_text('broken')
        with patch.object(module.QFileDialog, 'getOpenFileNames', return_value=([fixture, str(bad)], 'MP3')), \
                patch.object(module.QMessageBox, 'warning') as warning:
            self.window.choose_local_mp3()
            deadline = time.monotonic() + 4
            while self.window.jobs and time.monotonic() < deadline:
                self.application.processEvents()
            self.assertFalse(self.window.jobs)
            warning.assert_called_once()
        imported = [row for row in self.window.store.rows() if row['source'] == 'MP3']
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]['viewer'], 'mp3_viewer')
        self.assertEqual(self.window.current, current)
        self.assertEqual(self.window.selected()['id'], imported[0]['id'])
        self.assertEqual(self.window.down.submitted, [])
        self.assertTrue(self.window.import_mp3_button.isEnabled())

    def test_cancel_mp3_picker_does_not_add_jobs_or_requests(self):
        with patch.object(module.QFileDialog, 'getOpenFileNames', return_value=([], '')):
            self.window.choose_local_mp3()
        self.assertEqual(self.window.store.rows(), [])
        self.assertEqual(self.window.jobs, [])

    def donation_client(self,provider='donatex'):
        client=SimpleNamespace(provider=provider)
        self.window.donation_settings.active[provider]=client
        return client

    def test_live_donation_goes_first_after_playing_track_without_duplicate(self):
        from test_donations import raw_donation
        current=self.ready('current');other=self.ready('ordinary')
        self.window.next_track()
        self.window.player.pause()
        client=self.donation_client()
        self.window.donation_settings.handle(client,'donation',('account',raw_donation()))
        self.window.donation_settings.handle(client,'donation',('account',raw_donation()))
        rows=self.window.store.rows('active')
        self.assertEqual(len(rows),3)
        self.assertEqual([r['id'] for r in rows][::2],[current,other])
        self.assertEqual(rows[1]['donation_provider'],'donatex')
        self.assertEqual(self.window.current,current)
        self.assertEqual(self.window.player.playbackState(),QMediaPlayer.PausedState)
        self.assertEqual(self.window.down.submitted,[rows[1]['id']])

    def test_donation_waits_for_capacity_then_enters_before_normal_queue(self):
        from test_donations import raw_donation
        for i in range(100):self.window.store.add('https://youtu.be/test'+str(i),'viewer')
        client=self.donation_client()
        self.window.donation_settings.handle(client,'donation',('account',raw_donation()))
        self.assertEqual(self.window.store.donation_pending_count(),1)
        first=self.window.store.rows()[0]['id']
        self.window.store.rate(first,8);self.window.store.finish(first)
        self.window.donation_settings.process_pending()
        self.assertEqual(self.window.store.donation_pending_count(),0)
        self.assertEqual(self.window.store.rows('active')[0]['donation_provider'],'donatex')

    def test_next_track_waits_for_paid_download_and_resumes_when_ready(self):
        from test_donations import event
        ordinary=self.ready('ordinary');paid=event()
        ident=self.window.store.add(paid['url'],paid['viewer'],redemption=paid['event_key'],donation=paid)
        self.window.next_track()
        self.assertIsNone(self.window.current)
        self.assertTrue(self.window.waiting_paid)
        path=Path(self.folder.name)/'paid.wav';path.write_bytes(b'offline-placeholder')
        self.window.store.update(ident,status='Готово',path=str(path))
        self.window.handle_event('queue',None)
        self.assertEqual(self.window.current,ident)
        self.assertNotEqual(self.window.current,ordinary)

    def test_donated_album_is_one_prioritized_request(self):
        from test_donations import raw_donation
        from test_albums import album_metadata
        self.ready('ordinary')
        client=self.donation_client()
        with patch('donation_settings.catalog',return_value=album_metadata()):
            self.window.donation_settings.handle(client,'donation',('account',raw_donation(amount=400,message='https://music.yandex.ru/album/123')))
            deadline=time.monotonic()+4
            while self.window.jobs and time.monotonic()<deadline:self.application.processEvents()
        rows=self.window.store.rows('active')
        self.assertEqual(len(rows),2)
        self.assertEqual((rows[0]['source'],rows[0]['donation_priority']),('Альбом',1))
        self.assertEqual(self.window.store.donation_pending_count(),0)
        self.assertEqual(len(self.window.store.album_tracks(rows[0]['album_id'])),2)

    def test_stale_donation_client_cannot_add_orders(self):
        from test_donations import raw_donation
        old=SimpleNamespace(provider='donatex')
        self.donation_client()
        self.window.donation_settings.handle(old,'donation',('account',raw_donation()))
        self.assertEqual(self.window.store.rows(),[])

    def test_da_history_selection_adds_only_checked_orders_and_deduplicates_live_delivery(self):
        from donation_settings import DonationHistoryDialog
        from test_donations import raw_donation
        from PySide6.QtCore import Qt
        old=raw_donation('old',message='old message',created_at='2024-05-13 00:03:54')
        youtube=raw_donation('youtube',created_at='2026-11-06 12:00:00',message='https://www.youtube.com/watch?v=V87frW4x250')
        yandex=raw_donation('yandex',created_at='2026-11-06 19:22:40',message='https://music.yandex.ru/album/42437247/track/143886429?utm_source=web&utm_medium=copy_link')
        ordinary=self.ready('ordinary');client=self.donation_client('donationalerts')
        dialog=DonationHistoryDialog([old,youtube,yandex],'account',self.window)
        self.assertEqual(dialog.selected_events(),[]);self.assertEqual(len(dialog.events),2)
        for item,_ in dialog.events:item.setCheckState(0,Qt.Checked)
        panel=self.window.donation_settings
        panel.import_da_history(client,dialog.selected_events())
        panel.import_da_history(client,dialog.selected_events())
        panel.handle(client,'donation',('account',youtube))
        rows=self.window.store.rows('active')
        self.assertEqual(len(rows),3);self.assertEqual(rows[-1]['id'],ordinary)
        self.assertEqual([r['donation_provider'] for r in rows[:2]],['donationalerts']*2)
        self.assertEqual(len(self.window.down.submitted),2)
        self.assertIn('уже обработано: 2',panel.da_history_status.text())
        dialog.deleteLater()

    def test_da_history_result_from_disconnected_client_is_ignored(self):
        from test_donations import event
        old=self.donation_client('donationalerts');self.donation_client('donationalerts')
        self.window.donation_settings.import_da_history(old,[event(provider='donationalerts')])
        self.assertEqual(self.window.store.rows(),[])

    def test_da_history_displays_all_rejected_rows_with_full_message_and_actual_reason(self):
        from donation_settings import DonationHistoryDialog
        from test_donations import raw_donation
        records=[raw_donation('no-link',message='hello'),raw_donation('cheap',amount=100),
                 raw_donation('wrong-type',name='custom'),raw_donation('ok',is_test='0')]
        dialog=DonationHistoryDialog(records,'account',self.window)
        self.assertEqual(dialog.list.topLevelItemCount(),4)
        self.assertEqual(len(dialog.events),1)
        item=dialog.list.topLevelItem(2);dialog.list.setCurrentItem(item)
        self.assertIn('Тип события: custom',dialog.details.toPlainText())
        self.assertIn("name='custom'",dialog.details.toPlainText())
        self.assertIn('https://youtu.be/test',dialog.details.toPlainText())
        self.assertEqual(dialog.selected_events(),[])
        dialog.deleteLater()

    def test_rejected_live_event_logs_the_reason_once_without_adding_a_song(self):
        from test_donations import raw_donation
        client=self.donation_client();raw=raw_donation(name='custom')
        panel=self.window.donation_settings
        for _ in range(2):panel.handle(client,'donation',('account',raw))
        self.assertEqual(self.window.store.rows(),[])
        self.assertEqual(self.window.log.toPlainText().count('Тип события: custom'),1)

    def test_actual_da_donations_history_can_be_selected_and_added_to_queue(self):
        from donation_settings import DonationHistoryDialog
        from test_donations import raw_donation
        from PySide6.QtCore import Qt
        client=self.donation_client('donationalerts');ordinary=self.ready('ordinary')
        raw=raw_donation('900000001',name='Donations',message_type='text',amount=200,
                         message='https://music.yandex.ru/album/19792517/track/96567677?utm_source=web&utm_medium=copy_link')
        dialog=DonationHistoryDialog([raw],'account',self.window)
        self.assertEqual(len(dialog.events),1)
        dialog.events[0][0].setCheckState(0,Qt.Checked)
        self.window.donation_settings.import_da_history(client,dialog.selected_events())
        rows=self.window.store.rows('active')
        self.assertEqual(len(rows),2);self.assertEqual(rows[-1]['id'],ordinary)
        self.assertEqual(rows[0]['viewer'],'viewer')
        self.assertEqual(rows[0]['donation_provider'],'donationalerts')
        self.assertEqual(self.window.down.submitted,[rows[0]['id']])
        dialog.deleteLater()

    def test_actual_da_donations_live_publication_adds_once_without_interrupting_player(self):
        from donation_clients import DonationClient
        from test_donations import raw_donation
        current=self.ready('current');self.window.next_track()
        panel=self.window.donation_settings
        client=DonationClient('donationalerts','fake-test-token',lambda kind,data:panel.handle(client,kind,data))
        client.account='account';client.channel='$alerts:donation_account';panel.active[client.provider]=client
        packet=dict(result=dict(channel=client.channel,data=dict(data=raw_donation(name='Donations'))))
        client.da_publication(packet);client.da_publication(packet)
        rows=self.window.store.rows('active')
        self.assertEqual(len(rows),2);self.assertEqual(self.window.current,current)
        self.assertEqual(rows[1]['donation_provider'],'donationalerts')
        self.assertEqual(self.window.down.submitted,[rows[1]['id']])

    def test_donation_prices_persist_and_below_minimum_receipt_is_skipped_permanently(self):
        import json
        from test_donations import raw_donation
        panel=self.window.donation_settings;client=self.donation_client()
        raw=raw_donation('cheap',amount='199.99')
        panel.handle(client,'donation',('account',raw))
        self.assertEqual(self.window.store.rows(),[])
        self.assertEqual(self.window.store.donation_pending_count(),0)
        self.assertIn('ниже минимума',self.window.log.toPlainText())
        panel.price.setValue(50)
        self.assertIn('2 — 100 ₽',panel.price_examples.text())
        self.assertEqual(json.loads((Path(self.folder.name)/'settings.json').read_text())['donation_track_price'],50)
        panel.handle(client,'donation',('account',raw))
        self.assertEqual(self.window.store.rows(),[])
        panel.handle(client,'donation',('account',raw_donation('new',amount=100,message='https://youtu.be/one https://youtu.be/two https://youtu.be/three')))
        rows=self.window.store.rows('active')
        self.assertEqual(len(rows),2);self.assertEqual(len(self.window.down.submitted),2)
        self.assertEqual([r['url'] for r in rows],['https://youtu.be/one','https://youtu.be/two'])

    def test_underpaid_album_is_not_imported_or_downloaded(self):
        from test_donations import raw_donation
        from test_albums import album_metadata
        client=self.donation_client()
        with patch('donation_settings.catalog',return_value=album_metadata()):
            self.window.donation_settings.handle(client,'donation',('account',raw_donation(amount=200,message='https://music.yandex.ru/album/123')))
            deadline=time.monotonic()+4
            while self.window.jobs and time.monotonic()<deadline:self.application.processEvents()
        self.assertEqual(self.window.store.rows(),[])
        self.assertEqual(self.window.down.submitted,[])
        self.assertEqual(self.window.store.donation_pending_count(),0)

    def test_donatex_token_never_goes_to_plain_settings(self):
        fake=SimpleNamespace(provider='donatex',start=unittest.mock.Mock(),close=unittest.mock.Mock(),alive=lambda:False)
        panel=self.window.donation_settings
        panel.dx_token.setText('private-test-token')
        with patch('donation_settings.save_token',return_value=True) as save,patch('donation_settings.DonationClient',return_value=fake):
            panel.connect_dx()
        self.assertEqual(panel.dx_token.text(),'')
        save.assert_called_once_with('donatex','private-test-token')
        self.assertNotIn('private-test-token',(Path(self.folder.name)/'settings.json').read_text())
        with patch('donation_settings.forget_token'):
            panel.disconnect('donatex')
        fake.close.assert_called_once()

    def ready_album(self):
        from albums import create_album
        from test_albums import album_metadata
        album, ids = create_album(self.window.store, module.APP/'Альбомы', album_metadata(), 'album_viewer')
        for ident in ids:
            row = self.window.store.get(ident)
            path = Path(row['album_folder'])/(ident+'.mp3')
            path.write_bytes(b'offline-test-placeholder')
            self.window.store.update(ident, status='Готово', path=str(path))
        self.window.store.update_album_progress(album)
        self.window.refresh()
        return album, ids, self.window.store.album_request(album)

    def test_whole_album_plays_in_order_and_gets_one_rating_in_history(self):
        album, ids, parent = self.ready_album()
        next_id = self.ready('next_viewer')
        self.window.albums_panel.open_album(album)
        self.assertEqual(self.window.selected()['id'], parent['id'])
        for spin in self.window.criterion_spins.values(): spin.setValue(4)
        self.window.next_track()
        self.assertEqual(self.window.current, parent['id'])
        self.assertEqual(self.window.playing_track_id(), ids[0])
        self.window.media_status(QMediaPlayer.EndOfMedia)
        self.assertEqual(self.window.current, parent['id'])
        self.assertEqual(self.window.playing_track_id(), ids[1])
        self.assertEqual(self.window.history_list.count(), 0)
        self.window.media_status(QMediaPlayer.EndOfMedia)
        self.assertIsNone(self.window.current)
        self.assertEqual(self.window.store.get(next_id)['status'],'Готово')
        self.assertEqual(self.window.store.get(parent['id'])['status'], 'Ждёт оценки')
        self.assertEqual(self.window.history_list.count(), 0)
        self.assertTrue(all(s.value()==4 for s in self.window.criterion_spins.values()))
        self.window.save_rating()
        self.assertIsNone(self.window.current)
        self.assertEqual(self.window.history_list.count(), 1)
        self.assertEqual(self.window.store.get(parent['id'])['rating'], 44)
        self.assertTrue(all(self.window.store.get(i)['rating'] is None for i in ids))
        self.assertEqual(len(self.window.store.viewer_tracks(parent['viewer_key'])), 1)
        for index in range(self.window.profiles.count()):
            if self.window.profiles.item(index).data(Qt.UserRole)['viewer_key']==parent['viewer_key']:
                self.window.profiles.setCurrentRow(index)
                break
        text=self.window.profile_history.item(0).text()
        self.assertIn('Альбом · ',text)
        self.assertIn('44/66',text)
        for label in ('Текст:', 'Бит:', 'Флоу:', 'Вайб:', 'Сведение / мастеринг:'):
            self.assertNotIn('\n'+label,text)

    def test_album_skip_waits_for_one_review_and_never_queues_child_tracks(self):
        album, ids, parent = self.ready_album()
        self.window.next_track()
        self.window.skip_current()
        self.assertIsNone(self.window.current)
        self.assertEqual(self.window.store.get(parent['id'])['status'], 'Ждёт оценки')
        self.window.next_track()
        self.assertIsNone(self.window.current)
        self.assertEqual(len(self.window.store.rows()), 1)
        self.window.albums_panel.open_album(album)
        for spin in self.window.criterion_spins.values():spin.setValue(6)
        self.window.save_rating()
        self.assertEqual(self.window.store.get(parent['id'])['status'], 'Пропущено')

    def test_album_card_and_details_show_cover_and_requesting_viewer(self):
        from PySide6.QtGui import QImage, QColor
        album, ids, parent = self.ready_album()
        cover=Path(parent['album_folder'])/'album.png'
        image=QImage(32,32,QImage.Format_RGB32);image.fill(QColor('red'));image.save(str(cover))
        self.window.store.update(parent['id'],cover_path=str(cover))
        self.window.albums_panel.refresh()
        item=self.window.albums_panel.list.item(0)
        self.assertIn('От: album_viewer',item.text())
        self.assertFalse(item.icon().isNull())
        self.window.albums_panel.open_album(album)
        self.assertEqual(self.window.albums_panel.viewer.text(),'От: album_viewer')
        self.assertFalse(self.window.albums_panel.cover.pixmap().isNull())

    def test_delete_album_button_targets_open_album_and_keeps_other_request(self):
        album, ids, parent=self.ready_album()
        other=self.ready('other')
        self.window.list.setCurrentRow(1)
        self.window.albums_panel.open_album(album)
        self.window.tabs.setCurrentIndex(4)
        with patch.object(module.QMessageBox,'question',return_value=module.QMessageBox.No):
            self.window.albums_panel.delete_button.click()
        self.assertIsNotNone(self.window.store.get(parent['id']))
        self.assertTrue(Path(parent['album_folder']).exists())
        with patch.object(module.QMessageBox,'question',return_value=module.QMessageBox.Yes):
            self.window.albums_panel.delete_button.click()
        self.assertIsNone(self.window.store.get(parent['id']))
        self.assertTrue(all(self.window.store.get(i) is None for i in ids))
        self.assertIsNotNone(self.window.store.get(other))
        self.assertFalse(Path(parent['album_folder']).exists())
        self.assertEqual(self.window.albums_panel.stack.currentIndex(),0)

    def test_deleting_playing_album_advances_and_closes_child_lyrics(self):
        album,ids,parent=self.ready_album()
        other=self.ready('other')
        self.window.next_track()
        lyrics=SimpleNamespace(row=self.window.store.get(ids[0]),reject=unittest.mock.Mock())
        self.window.lyrics_window=lyrics
        with patch.object(module.QMessageBox,'question',return_value=module.QMessageBox.Yes):
            self.window.delete_album(album)
        lyrics.reject.assert_called_once()
        self.window.lyrics_window=None
        self.assertEqual(self.window.current,other)
        self.assertFalse(Path(parent['album_folder']).exists())

    def test_profile_shows_one_average_for_legacy_and_new_ratings(self):
        first,second=self.ready('same'),self.ready('same')
        self.window.store.rate(first,1)
        self.window.store.rate_criteria(second,dict(text=6,beat=6,flow=6,vibe=6,mix=6))
        self.window.refresh()
        self.assertIn('Средний балл: 38.50/66',self.window.profiles.item(0).text())
        self.assertNotIn('Старые:',self.window.profiles.item(0).text())

    def test_twitch_album_import_and_refund_remove_whole_request_and_folder(self):
        from test_albums import album_metadata
        event = dict(id='album-redemption', user_name='viewer', user_id='123',
                     user_input='https://music.yandex.ru/album/123', reward=dict(id='album-reward', title='Album'))
        with patch.object(module, 'catalog', return_value=album_metadata()):
            self.window.redeem(event)
            deadline=time.monotonic()+4
            while self.window.jobs and time.monotonic()<deadline:self.application.processEvents()
        row=self.window.store.get_redemption('album-redemption')
        self.assertIsNotNone(row)
        self.assertEqual(row['source'],'Альбом')
        children=self.window.store.album_tracks(row['album_id'])
        self.assertEqual(len(children),2)
        self.assertEqual(self.window.down.submitted,[r['id'] for r in children])
        self.window.redemption_updated(dict(event,status='canceled'))
        self.assertEqual(self.window.store.rows('all'),[])
        self.assertFalse(Path(row['album_folder']).exists())

    def test_twitch_cancel_during_album_catalog_cannot_revive_request(self):
        from test_albums import album_metadata
        event = dict(id='pending-album', user_name='viewer', user_input='https://music.yandex.ru/album/123', reward=dict(id='r'))
        with patch.object(module,'catalog',return_value=album_metadata()):
            self.window.redeem(event)
            self.window.redemption_updated(dict(event,status='canceled'))
            deadline=time.monotonic()+4
            while self.window.jobs and time.monotonic()<deadline:self.application.processEvents()
        self.assertEqual(self.window.store.rows('all'),[])
        self.assertEqual(self.window.store.albums(),[])
        self.assertEqual(self.window.down.submitted,[])

    def test_last_song_ends_and_skip_does_not_repeat_it(self):
        ident = self.ready()
        self.window.next_track()
        self.assertEqual(self.window.current, ident)
        self.window.media_status(QMediaPlayer.EndOfMedia)
        self.assertIsNone(self.window.current)
        self.assertEqual(self.window.store.get(ident)['status'], 'Ждёт оценки')
        self.window.play_row(self.window.store.get(ident))
        self.window.skip_current()
        self.assertIsNone(self.window.current)
        self.assertEqual(self.window.store.get(ident)['status'], 'Ждёт оценки')

    def test_natural_end_stops_with_next_ready_then_manual_next_starts_it(self):
        first,second=self.ready('first'),self.ready('second')
        self.window.next_track();self.window.media_status(QMediaPlayer.EndOfMedia)
        self.assertIsNone(self.window.current)
        self.assertFalse(self.window.waiting_paid)
        self.assertEqual(self.window.player.playbackState(),QMediaPlayer.StoppedState)
        self.assertEqual(self.window.store.get(first)['status'],'Ждёт оценки')
        self.assertEqual(self.window.store.get(second)['status'],'Готово')
        self.window.handle_event('queue',None)
        self.assertIsNone(self.window.current)
        self.window.skip_current()
        self.assertEqual(self.window.current,second)

    def test_skip_plays_next_and_previous_really_goes_back(self):
        first, second = self.ready('a'), self.ready('b')
        self.window.next_track()
        self.window.skip_current()
        self.assertEqual(self.window.current, second)
        self.assertEqual(self.window.store.get(first)['status'], 'Ждёт оценки')
        self.window.store.update(first, status='Прослушано')
        self.window.previous()
        self.assertEqual(self.window.current, first)
        self.assertEqual(self.window.store.get(second)['status'], 'Ждёт оценки')
        playing = [r for r in self.window.store.rows() if r['status'] == 'Играет']
        self.assertEqual(len(playing), 1)

    def test_review_draft_survives_background_refresh_and_persists(self):
        ident = self.ready()
        self.window.list.setCurrentRow(0)
        for spin in self.window.criterion_spins.values(): spin.setValue(6)
        self.window.comment.setText('Мой комментарий')
        self.window.handle_event('queue', None)
        self.assertEqual(self.window.comment.text(), 'Мой комментарий')
        self.window.save_rating()
        self.assertEqual(self.window.store.get(ident)['rating'], 66)
        self.assertIn('66.00/66', self.window.profiles.item(0).text())
        self.window.select_history_track(self.window.profile_history.item(0))
        self.assertEqual(self.window.selected()['id'], ident)
        self.assertEqual(self.window.comment.text(), 'Мой комментарий')

    def test_without_text_reaches_66_and_restores_excluded_score(self):
        ident = self.ready()
        self.window.list.setCurrentRow(0)
        self.assertFalse(self.window.save_rating_btn.isEnabled())
        for key in ('beat', 'flow', 'vibe', 'mix'):
            self.window.criterion_spins[key].setValue(6)
        self.assertEqual(self.window.rating_total.text(), '— / 66')
        self.window.text_rating_enabled.setChecked(False)
        self.assertEqual(self.window.rating_total.text(), '66 / 66')
        self.assertFalse(self.window.criterion_spins['text'].isEnabled())
        self.window.save_rating()
        self.window.load_rating()
        self.assertFalse(self.window.text_rating_enabled.isChecked())
        self.assertEqual(self.window.store.get(ident)['rating'], 66)
        self.window.text_rating_enabled.setChecked(True)
        self.assertFalse(self.window.save_rating_btn.isEnabled())
        self.window.criterion_spins['text'].setValue(1)
        self.assertEqual(self.window.rating_total.text(), '55 / 66')
        self.window.refresh()
        self.assertEqual(self.window.criterion_spins['text'].value(), 1)

    def test_legacy_rating_is_shown_without_invented_criteria(self):
        ident = self.ready()
        self.window.store.rate(ident, 9, 'старый отзыв')
        self.window.refresh()
        self.window.list.setCurrentRow(0)
        self.assertIn('9/10', self.window.rating_hint.text())
        self.assertTrue(all(spin.value() == 0 for spin in self.window.criterion_spins.values()))
        self.assertFalse(self.window.save_rating_btn.isEnabled())
        self.assertIn('Средний балл: 59.89/66', self.window.profiles.item(0).text())
        self.assertEqual(self.window.comment.text(), 'старый отзыв')

    def test_remove_selected_retains_review(self):
        ident = self.ready()
        self.window.store.rate(ident, 7, 'оставить в профиле')
        self.window.refresh()
        self.window.list.setCurrentRow(0)
        self.window.skip_selected()
        row = self.window.store.get(ident)
        self.assertEqual((row['status'], row['rating']), ('Пропущено', 7))
        self.assertEqual(self.window.store.profiles()[0]['average'], 7)

    def test_volume_and_progress(self):
        self.window.volume.setValue(35)
        self.assertAlmostEqual(self.window.audio.volume(), .35, places=5)
        self.assertEqual(module.load_config(module.APP)['volume'], 35)
        self.window.duration_changed(125000)
        self.window.position_changed(61000)
        self.assertEqual(self.window.duration.text(), '2:05')
        self.assertEqual(self.window.elapsed.text(), '1:01')
        self.assertEqual(self.window.seek.value(), 61000)

    def test_reward_selection_saved_and_old_socket_ignored(self):
        old = self.window.make_client('id')
        self.window.client = old
        self.window.cfg['reward_id'] = 'second'
        self.window.show_rewards([
            dict(id='first', title='Первая', is_enabled=True, is_user_input_required=True),
            dict(id='second', title='Музыка', is_enabled=True, is_user_input_required=True),
            dict(id='ignored', title='Без текста', is_enabled=True, is_user_input_required=False)])
        self.assertEqual(self.window.rewards.currentIndex(), 1)
        self.assertEqual(self.window.client.connected, 'second')
        self.assertTrue(old.closed)
        self.assertEqual(module.load_config(module.APP)['reward_id'], 'second')
        old.emit('connection', 'STALE')
        self.application.processEvents()
        self.assertNotEqual(self.window.status.text(), 'STALE')
        self.window.redeem(dict(id='bad', user_id='42', user_input='https://youtu.be/a', reward={'id': 'first'}))
        self.assertEqual(len(self.window.store.rows()), 0)
        self.window.redeem(dict(id='ok', user_id='42', user_name='Nick', user_input='https://youtu.be/a', reward={'id': 'second'}))
        self.assertEqual(self.window.store.profiles()[0]['viewer_key'], 'twitch:42')

    def test_multiple_rewards_restore_and_attach_independent_duration_limits(self):
        rewards = [dict(id=str(n), title=f'Трек до {n} минут', is_enabled=True, is_user_input_required=True) for n in (2,3,4)]
        self.window.client = self.window.make_client('id')
        self.window.cfg['reward_limits'] = {'2':120, '3':180, '4':240}
        self.window.show_rewards(rewards)
        self.assertEqual(self.window.client.connected, ['2','3','4'])
        self.assertEqual(self.window.rewards.currentText(), 'Принимаются награды: 3')
        self.assertEqual(module.load_config(module.APP)['reward_limits'], {'2':120,'3':180,'4':240})
        for n in (2,3,4):
            self.window.redeem(dict(id=f'redemption-{n}', user_name='Viewer', user_id='42',
                user_input='https://youtu.be/test', reward={'id':str(n),'title':f'До {n} мин'}))
        self.assertEqual([r['max_duration'] for r in self.window.store.rows()], [120,180,240])
        self.assertEqual(len(self.window.down.submitted), 3)
        self.window.redeem(dict(id='ignore', user_input='https://youtu.be/test', reward={'id':'other'}))
        self.assertEqual(len(self.window.store.rows()), 3)
        # Opening/saving the available rewards again restores all three.
        self.window.show_rewards(rewards)
        self.assertEqual(self.window.client.connected, ['2','3','4'])

    def test_seconds_in_reward_limit_survive_settings_and_reach_request(self):
        self.window.client=self.window.make_client('id')
        self.window.cfg['reward_limits']={'music':157}
        self.window.show_rewards([dict(id='music',title='Заказ музыки',is_enabled=True,is_user_input_required=True)])
        self.assertEqual(module.load_config(module.APP)['reward_limits'],{'music':157})
        self.assertIn('2 мин 37 сек',self.window.rewards.toolTip())
        self.window.redeem(dict(id='exact-limit',user_name='Viewer',user_id='42',user_input='https://youtu.be/test',reward={'id':'music'}))
        self.assertEqual(self.window.store.rows()[0]['max_duration'],157)

    def test_refresh_applies_renamed_reward_limit_without_reconnecting(self):
        self.window.client = self.window.make_client('id')
        reward = dict(id='music', title='Трек до 5 минут', is_enabled=True, is_user_input_required=True)
        self.window.sync_reward_catalog([reward])
        socket_client = self.window.client
        self.window.handle_event('connection', 'Подключено · заявки принимаются')
        updated = dict(reward, title='Трек до 15 минут')
        socket_client.rewards = Mock(return_value=[updated])
        with patch.object(self.window, 'run_async', side_effect=lambda fn, done, failed: done(fn())):
            self.window.reward_refresh_timer.timeout.emit()
        self.assertIs(self.window.client, socket_client)
        self.assertEqual(self.window.rewards.currentText(), updated['title'])
        self.assertEqual(self.window.telegram_reward_options, (('music', 900, updated['title']),))
        self.window.redeem(dict(id='after-refresh', user_name='Viewer', user_id='42',
            user_input='https://youtu.be/test', reward={'id': 'music'}))
        self.assertEqual(self.window.store.rows()[0]['max_duration'], 900)
        self.assertEqual(module.load_config(module.APP)['reward_limits'], {'music': 900})

    def test_manual_refresh_adopts_title_and_disabled_reward_is_not_accepted(self):
        self.window.client = self.window.make_client('id')
        self.window.cfg['reward_limits'] = {'music': 157}
        reward = dict(id='music', title='Трек до 5 минут', is_enabled=True, is_user_input_required=True)
        self.window.sync_reward_catalog([reward])
        self.assertEqual(self.window.active_reward_limits, {'music': 157})
        self.window.sync_reward_catalog([reward], manual=True)
        self.assertEqual(self.window.active_reward_limits, {'music': 300})
        self.window.sync_reward_catalog([dict(reward, is_enabled=False)])
        self.window.redeem(dict(id='disabled', user_input='https://youtu.be/test', reward={'id': 'music'}))
        self.assertEqual(self.window.store.rows(), [])
        self.window.sync_reward_catalog([reward])
        self.assertEqual(self.window.active_reward_limits, {'music': 300})

    def test_worker_callbacks_run_on_gui_thread(self):
        results = []
        self.window.run_async(lambda: QThread.currentThread(),
                              lambda worker_thread: results.append((worker_thread, QThread.currentThread())))
        deadline = time.monotonic() + 3
        while (self.window.jobs or not results) and time.monotonic() < deadline:
            self.application.processEvents()
        self.assertEqual(len(results), 1)
        self.assertIs(results[0][1], self.application.thread())
        self.assertIsNot(results[0][0], self.application.thread())
        self.assertFalse(self.window.jobs)

    def test_close_waits_for_worker_without_destroying_running_thread(self):
        release = Event()
        self.window.run_async(lambda: release.wait(2), lambda _: self.fail('Callback after closing'))
        self.window.close()
        self.assertTrue(self.window.closing)
        release.set()
        deadline = time.monotonic() + 3
        while self.window.jobs and time.monotonic() < deadline:
            self.application.processEvents()
        self.assertFalse(self.window.jobs)

    @patch.object(module, 'TELEGRAM_PAUSED', False)
    def test_telegram_redemption_is_not_sent_to_url_downloader(self):
        self.window.redeem(dict(id='tg-order', user_id='42', user_name='viewer', user_input='в тг'))
        rows = self.window.store.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['source'], 'Telegram')
        self.assertEqual(rows[0]['status'], 'Ожидает Telegram')
        self.assertEqual(self.window.down.submitted, [])
        self.assertEqual(self.window.telegram_pending.count(), 1)

    @patch.object(module, 'TELEGRAM_PAUSED', False)
    def test_telegram_manual_assignment_uses_twitch_profile(self):
        self.window.telegram = SimpleNamespace(chat_id='-1001', bot_id='12345')
        file = self.window.telegram_inbox.remember('12345', '-1001', {
            'message_id': 1, 'date': time.time(), 'from': {'id': 900, 'username': 'different_tg_nick'},
            'audio': {'file_id': 'f', 'file_name': 'song.mp3', 'file_size': 500}})
        self.window.redeem(dict(id='tg-order', user_id='42', user_name='twitch_nick', user_input='в тг'))
        self.window.telegram_files.setCurrentRow(0)
        self.window.telegram_pending.setCurrentIndex(0)
        self.window.assign_telegram()
        row = self.window.store.rows()[0]
        self.assertEqual(row['status'], 'Ожидает загрузки')
        self.assertEqual(row['telegram_code'], file['code'])
        self.assertEqual(row['viewer'], 'twitch_nick')
        self.assertEqual(self.window.store.profiles()[0]['viewer_key'], 'twitch:42')

    def test_telegram_settings_dialog_opens_without_network(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog
        def cancel():
            for widget in self.application.topLevelWidgets():
                if isinstance(widget, QDialog):
                    widget.reject()
        QTimer.singleShot(20, cancel)
        with patch('app.keyring.get_password', return_value=None), patch('telegram_support.requests.post') as network:
            self.window.configure_telegram()
        network.assert_not_called()

    def test_telegram_is_paused_hidden_and_never_autoconnects(self):
        self.assertTrue(module.TELEGRAM_PAUSED)
        self.assertFalse(self.window.tabs.isTabVisible(5))
        self.window.cfg.update(telegram_enabled=True, telegram_chat='-1001')
        with patch('app.keyring.get_password') as credentials:
            self.window.telegram_if_ready()
        credentials.assert_not_called()
        with self.assertRaises(ValueError):
            self.window.add_request('в тг', 'viewer')
        self.assertEqual(self.window.store.rows(), [])

    def test_twitch_telegram_notes_are_skipped_without_queue_profile_or_download(self):
        for n,text in enumerate(('в тг','скинул в тг','В Т.Г.!','в телеграмме TT-BAD',
                                 'https://youtu.be/example трек в тг')):
            self.window.redeem(dict(id=f'skip-{n}',user_id='42',user_name='Viewer',user_input=text))
        self.assertEqual(self.window.store.rows(), [])
        self.assertEqual(self.window.store.profiles(), [])
        self.assertEqual(self.window.down.submitted, [])
        self.assertIn('пропущена — трек в Telegram', self.window.log.toPlainText())
        self.assertNotIn('Заявка отклонена', self.window.log.toPlainText())
        self.window.redeem(dict(id='ordinary',user_id='42',user_name='Viewer',user_input='https://youtu.be/example'))
        self.assertEqual(len(self.window.store.rows()), 1)
        self.assertEqual(len(self.window.down.submitted), 1)

    def test_player_cover_changes_with_track_and_resets_to_app_icon(self):
        from PySide6.QtGui import QImage,QColor
        ident=self.ready('Viewer')
        path=module.APP/'cover.png'
        image=QImage(64,64,QImage.Format_RGB32)
        image.fill(QColor('#c6f52b'))
        image.save(str(path))
        self.window.store.update(ident,cover_path=str(path))
        self.window.play_row(self.window.store.get(ident))
        self.assertFalse(self.window.cover_badge.pixmap().isNull())
        self.assertEqual(self.window.cover_badge.pixmap().toImage().pixelColor(20,20).name(),'#c6f52b')
        self.window.clear_current()
        self.assertEqual(self.window.cover_badge.toolTip(),'TwitchTune')
        self.assertFalse(self.window.cover_badge.pixmap().isNull())

    def test_player_stays_at_bottom_and_transport_is_centered(self):
        for width, height in ((1280, 800), (1040, 700)):
            self.window.resize(width, height)
            self.window.show()
            self.application.processEvents()
            center = self.window.play.mapTo(self.window, self.window.play.rect().center())
            self.assertLess(abs(center.x() - self.window.width()/2), 3)
            self.assertGreater(self.window.player_dock.geometry().bottom(), self.window.height()-22)
            self.assertEqual(self.window.player_dock.height(), 112)

    def test_transport_artwork_follows_playback_state(self):
        self.window.playback_changed(QMediaPlayer.PlayingState)
        self.assertTrue(self.window.play.playing)
        self.assertTrue(self.window.deck.timer.isActive())
        self.window.playback_changed(QMediaPlayer.PausedState)
        self.assertFalse(self.window.play.playing)
        self.assertFalse(self.window.deck.timer.isActive())

    def test_settings_palettes_apply_without_interrupting_playback(self):
        from theme import THEMES
        ident = self.ready()
        self.window.play_row(self.window.store.get(ident))
        self.window.volume.setValue(37)
        self.window.nav_buttons[3].click()
        self.assertEqual(self.window.tabs.currentIndex(), 3)
        self.assertTrue(self.window.tabs.isTabVisible(3))
        for key, theme in THEMES.items():
            self.window.theme_buttons[key].click()
            self.assertEqual(self.window.theme_key, key)
            self.assertEqual(module.load_config(module.APP)['theme'], key)
            self.assertEqual(module.load_config(module.APP)['volume'], 37)
            self.assertEqual(self.window.current, ident)
            self.assertEqual(self.window.player.playbackState(), QMediaPlayer.PlayingState)
            self.assertEqual(self.window.deck.ink('#c6f52b').name(), theme['accent'])
            self.assertEqual(self.window.play.ink('#c6f52b').name(), theme['accent'])
            self.assertEqual(sum(button.isChecked() for button in self.window.theme_buttons.values()), 1)

    def test_biohazard_all_palettes_preserve_playback_and_restore_style(self):
        from theme import THEMES, bio_image
        from PySide6.QtGui import QPixmap
        ident = self.ready()
        self.window.play_row(self.window.store.get(ident))
        self.window.interface_style_picker.setCurrentIndex(
            self.window.interface_style_picker.findData('biohazard'))
        self.window.show()
        for key, theme in THEMES.items():
            self.window.apply_theme(key)
            self.application.processEvents()
            self.assertEqual(self.window.play.ink('#c6f52b').name(), theme['accent'])
            self.assertEqual(self.window.current, ident)
            self.assertEqual(self.window.player.playbackState(), QMediaPlayer.PlayingState)
            self.assertFalse(self.window.palette_options.isHidden())
            self.assertFalse(self.window.bio_speaker_left.isHidden())
            self.assertFalse(self.window.grab().isNull())
            for kind in ('panel', 'button'):
                self.assertFalse(QPixmap(bio_image(kind, key)).isNull())
        self.assertEqual(module.load_config(module.APP)['interface_style'], 'biohazard')
        other = module.Main()
        try:
            self.assertEqual(other.interface_style, 'biohazard')
            self.assertEqual(other.theme_key, key)
        finally:
            other.close()
            other.store.db.close()
            other.deleteLater()
        self.window.interface_style_picker.setCurrentIndex(
            self.window.interface_style_picker.findData('classic'))
        self.assertTrue(self.window.bio_speaker_left.isHidden())
        self.assertEqual(self.window.play.ink('#c6f52b').name(), THEMES[key]['accent'])

    def test_saved_theme_restores_and_does_not_change_other_windows(self):
        module.save_config(module.APP, {'theme': 'violet', 'volume': 44})
        other = module.Main()
        try:
            self.assertEqual(other.theme_key, 'violet')
            self.assertTrue(other.theme_buttons['violet'].isChecked())
            self.assertEqual(other.volume.value(), 44)
            self.assertEqual(self.window.theme_key, 'green')
            self.assertNotEqual(other.play.ink('#c6f52b'), self.window.play.ink('#c6f52b'))
        finally:
            other.close()
            other.store.db.close()
            other.deleteLater()
            self.application.processEvents()

    def test_unknown_theme_falls_back_to_readable_default(self):
        for invalid in ('unknown', None, ['red']):
            self.window.apply_theme(invalid)
            self.assertEqual(self.window.theme_key, 'green')
            self.assertTrue(self.window.theme_buttons['green'].isChecked())

    def test_finished_song_leaves_queue_and_keeps_review_draft(self):
        first, second = self.ready('a'), self.ready('b')
        self.window.list.setCurrentRow(0)
        for spin in self.window.criterion_spins.values(): spin.setValue(4)
        self.window.comment.setText('Отзыв после прослушивания')
        self.window.next_track()
        self.window.media_status(QMediaPlayer.EndOfMedia)
        self.assertIsNone(self.window.current)
        self.assertEqual(self.window.store.get(second)['status'],'Готово')
        self.assertEqual(self.window.list.count(), 2)
        self.assertEqual(self.window.store.get(first)['status'], 'Ждёт оценки')
        self.assertEqual(self.window.history_list.count(), 0)
        self.assertEqual(self.window.selected()['id'], first)
        self.assertEqual(self.window.comment.text(), 'Отзыв после прослушивания')
        self.window.save_rating()
        self.assertEqual(self.window.store.get(first)['rating'], 44)
        self.assertEqual(self.window.history_list.count(), 1)
        self.assertEqual(self.window.history_list.item(0).data(Qt.UserRole)['id'], first)
        self.assertEqual(self.window.queue_count.text(), '01')

    def test_history_review_and_profile_open_archived_track(self):
        ident = self.ready('viewer')
        self.window.store.update(ident, status='Прослушано')
        self.window.refresh()
        self.assertEqual(self.window.list.count(), 0)
        self.window.tabs.setCurrentIndex(2)
        self.window.history_list.setCurrentRow(0)
        for spin in self.window.criterion_spins.values(): spin.setValue(6)
        self.window.comment.setText('Комментарий в истории')
        self.window.save_rating()
        self.assertEqual(self.window.store.get(ident)['comment'], 'Комментарий в истории')
        self.assertEqual(self.window.store.profiles()[0]['average66'], 66)
        self.window.select_history_track(self.window.profile_history.item(0))
        self.assertEqual(self.window.tabs.currentIndex(), 2)
        self.assertEqual(self.window.selected()['id'], ident)
        self.assertEqual(self.window.criterion_spins['text'].value(), 6)

    def test_skipped_track_can_return_to_queue_without_duplicate_profile(self):
        ident = self.ready('viewer')
        self.window.list.setCurrentRow(0)
        self.window.store.rate(ident, 7, 'сохранить')
        self.window.skip_selected()
        self.assertEqual(self.window.list.count(), 0)
        self.assertEqual(self.window.history_list.count(), 1)
        self.window.tabs.setCurrentIndex(2)
        self.window.history_list.setCurrentRow(0)
        self.window.restore_history()
        self.assertEqual(self.window.tabs.currentIndex(), 0)
        self.assertEqual(self.window.history_list.count(), 0)
        self.assertEqual(self.window.list.count(), 1)
        self.assertEqual(self.window.store.get(ident)['status'], 'Готово')
        self.assertEqual(self.window.store.get(ident)['rating'], 7)
        self.assertEqual(self.window.store.profiles()[0]['total'], 1)

    def test_lyrics_popup_is_nonmodal_and_preserves_playback(self):
        ident = self.ready('viewer')
        self.window.store.update(ident, title='Test Artist — Test Song', artist='Test Artist', track='Test Song')
        self.window.play_row(self.window.store.get(ident))
        record = dict(id=1, artistName='Test Artist', trackName='Test Song', duration=120,
                      plainLyrics='Тестовая строка\nЭто текст для проверки интерфейса.', instrumental=False)
        self.window.lyrics_candidates = [record]
        self.window.show_lyrics()
        dialog = self.window.lyrics_window
        self.assertEqual(dialog.windowModality(), Qt.NonModal)
        self.assertEqual(dialog.body.toPlainText(), record['plainLyrics'])
        self.assertEqual(self.window.current, ident)
        self.assertEqual(self.window.player.playbackState(), QMediaPlayer.PlayingState)
        self.window.show_lyrics()
        self.assertIs(self.window.lyrics_window, dialog)
        dialog.reject()
        self.assertIsNone(self.window.lyrics_window)

    def test_soundcloud_lyrics_search_ignores_saved_description(self):
        ident = self.window.store.add('https://soundcloud.com/artist/track', 'Viewer')
        self.window.store.update(ident, title='Artist — Song', description_checked=2,
                                 description_lyrics='Строка из описания', soundcloud_description='Описание трека')
        self.window.current = ident
        record = dict(artistName='Artist', trackName='Song', plainLyrics='Найденный текст', source='LRCLIB')
        with patch.object(self.window.lyrics_client, 'search', return_value=[record]) as external, \
                patch.object(self.window, 'run_async', side_effect=lambda fn, done, failed=None: done(fn())):
            self.window.start_lyrics_lookup(self.window.store.get(ident))
            external.assert_called_once()
        self.window.show_lyrics()
        dialog = self.window.lyrics_window
        self.assertEqual(dialog.body.toPlainText(), 'Найденный текст')
        self.assertIn('LRCLIB', dialog.source_label.text())
        dialog.reject()

    def test_lyrics_refresh_failure_keeps_previous_text_visible(self):
        from lyrics import LyricsError
        ident=self.ready('viewer');self.window.current=ident
        self.window.lyrics_candidates=[dict(artistName='Artist',trackName='Song',plainLyrics='Собственный прежний текст',source='Genius',duration=0)]
        self.window.show_lyrics();dialog=self.window.lyrics_window
        dialog.versions.setCurrentIndex(0)
        before=dialog.body.toPlainText()
        self.assertEqual(before,'Собственный прежний текст')
        def run(fn,done,failed=None):
            try:done(fn())
            except Exception as error:failed(error)
        with patch.object(self.window.lyrics_resolver,'search',side_effect=LyricsError('Genius недоступен')),patch.object(self.window,'run_async',side_effect=run):
            dialog.search()
        self.assertEqual(dialog.body.toPlainText(),before)
        self.assertIn('предыдущий результат',dialog.info.text())
        dialog.reject()

    def test_soundcloud_without_description_lyrics_falls_back_to_lrclib(self):
        ident=self.window.store.add('https://soundcloud.com/artist/track','Viewer')
        self.window.store.update(ident,title='Artist — Song',description_checked=2)
        self.window.current=ident
        with patch.object(self.window.lyrics_client,'search',return_value=[]) as external, patch.object(self.window,'run_async',side_effect=lambda fn,done,failed=None:done(fn())):
            self.window.start_lyrics_lookup(self.window.store.get(ident))
        external.assert_called_once()
        self.assertEqual(external.call_args.args[:2],('Artist','Song'))

    def test_soundcloud_description_opens_separately_and_refreshes_without_audio_download(self):
        ident = self.window.store.add('https://soundcloud.com/artist/track', 'Viewer')
        raw = 'Короткий текст\nhttps://example.org\n@artist'
        self.window.store.update(ident, title='Artist — Song', description_checked=2, soundcloud_description=raw)
        self.window.current = ident
        record = dict(artistName='Artist', trackName='Song', plainLyrics='Текст песни', source='LRCLIB')
        with patch.object(self.window.lyrics_client, 'search', return_value=[record]), \
                patch.object(self.window, 'run_async', side_effect=lambda fn, done, failed=None: done(fn())):
            self.window.start_lyrics_lookup(self.window.store.get(ident))
            self.assertFalse(self.window.description_button.isHidden())
            self.window.description_button.click()
            description = self.window.description_window
            self.assertEqual(description.body.toPlainText(), raw)
            self.window.show_lyrics()
            lyrics = self.window.lyrics_window
            lyrics.description_button.click()
            self.assertIs(self.window.description_window, description)
            with patch('media.extract_soundcloud', return_value={'description': 'Обновлённое описание'}) as extract:
                description.refresh_button.click()
                self.assertFalse(extract.call_args.kwargs['download'])
                self.assertEqual(description.body.toPlainText(), 'Обновлённое описание')
            self.assertEqual(lyrics.body.toPlainText(), 'Текст песни')
            description.reject()
            lyrics.reject()
            self.assertIsNone(self.window.description_window)

    def test_lyrics_fallback_is_connected_to_main_and_reader_with_source_label(self):
        from lyrics import LyricsError
        ident=self.ready()
        self.window.store.update(ident,title='Artist — Song')
        self.window.current=ident
        record=dict(trackName='Song',artistName='Artist',plainLyrics='Собственная строка',source='Genius',sourceUrl='https://genius.com/test-lyrics')
        with patch.object(self.window.lyrics_client,'search',side_effect=LyricsError('busy')), patch.object(self.window.lyrics_resolver.genius,'search',return_value=[record]) as genius, patch.object(self.window,'run_async',side_effect=lambda fn,done,failed=None:done(fn())):
            self.window.start_lyrics_lookup(self.window.store.get(ident))
            self.window.show_lyrics()
            dialog=self.window.lyrics_window
            self.assertEqual(dialog.body.toPlainText(),record['plainLyrics'])
            self.assertEqual(dialog.source_label.text(),'Источник текста: Genius')
            dialog.search()
            self.assertEqual(genius.call_count,2)
            self.assertIn('Genius',dialog.source_label.text())
        dialog.reject()

    def test_yandex_lyrics_reader_uses_exact_source_id_even_without_artist(self):
        from lyrics_window import LyricsDialog
        row=dict(id='reader',title='Song',source='Яндекс Музыка')
        record=dict(trackName='Song',artistName='',plainLyrics='Собственная строка',source='Яндекс Музыка')
        dialog=LyricsDialog(row,FakeLyrics(),self.window,[record])
        self.assertEqual(dialog.body.toPlainText(),record['plainLyrics'])
        self.assertEqual(dialog.source_label.text(),'Источник текста: Яндекс Музыка')
        dialog.reject()

    def test_lyrics_reader_cleans_plain_timestamps_and_offers_font_controls(self):
        from lyrics_window import LyricsDialog
        row = dict(id='reader', title='Artist — Song', artist='Artist', track='Song', duration=120)
        record = dict(artistName='Artist', trackName='Song', duration=120,
                      plainLyrics='[00:01.20]Первая строка\n[00:02.30]Вторая строка')
        dialog = LyricsDialog(row, FakeLyrics(), self.window, [record])
        self.assertEqual(dialog.body.toPlainText(), 'Первая строка\nВторая строка')
        self.assertEqual(dialog.body.document().firstBlock().blockFormat().lineHeight(), 150)
        self.assertTrue(dialog.versions.isHidden())
        self.assertTrue(dialog.search_panel.isHidden())
        dialog.resize_text(1)
        self.assertEqual(dialog.body.font().pointSize(), 16)
        dialog.show_results([dict(record, artistName='Other artist')])
        self.assertFalse(dialog.versions.isHidden())
        self.assertFalse(dialog.search_panel.isHidden())
        dialog.versions.setCurrentIndex(0)
        self.assertIn('Первая строка', dialog.body.toPlainText())
        dialog.reject()

    def test_vk_settings_profile_check_disconnect_and_error_shortcut(self):
        settings=self.window.vk_settings
        settings.source.setCurrentIndex(settings.source.findData('firefox'))
        settings.set_profile('dummy-profile')
        self.assertEqual(module.load_config(module.APP)['vk_profiles']['firefox'],'dummy-profile')
        with patch('vk_settings.check_vk_session',return_value='Вход VK подтверждён') as check, patch.object(self.window,'run_async',side_effect=lambda fn,done,failed=None:done(fn())):
            settings.check_session()
            self.assertIn('подтверждён',settings.info.text())
            self.assertEqual(check.call_args.args[0]['vk_auth'],'firefox')
        ident=self.window.store.add('https://vk.ru/audio1_2','Viewer')
        self.window.store.update(ident,status='Нужна замена',error='Нужен вход VK')
        self.window.refresh();self.window.list.setCurrentRow(0)
        self.assertFalse(self.window.vk_settings_button.isHidden())
        self.window.vk_settings_button.click()
        self.assertEqual(self.window.tabs.currentIndex(),3)
        settings.disconnect()
        self.assertEqual(module.load_config(module.APP)['vk_auth'],'')
        self.assertFalse(settings.check.isEnabled())

    def test_yandex_download_error_is_visible_and_opens_settings(self):
        ident = self.window.store.add('https://music.yandex.ru/track/155671332')
        self.window.store.update(ident, status='Ошибка Яндекса', error='Вход Яндекса не найден')
        self.window.refresh()
        self.window.list.setCurrentRow(0)
        self.assertFalse(self.window.download_error_box.isHidden())
        self.assertIn('Вход Яндекса не найден', self.window.download_error.text())
        self.window.yandex_settings_button.click()
        self.assertEqual(self.window.tabs.currentIndex(), 3)
        self.window.store.update(ident, error='', status='Ожидает загрузки')
        self.window.refresh()
        self.assertTrue(self.window.download_error_box.isHidden())

    def test_stale_lyrics_result_cannot_replace_next_track(self):
        first, second = self.ready('a'), self.ready('b')
        calls = []
        with patch.object(self.window, 'ensure_cover'), patch.object(self.window, 'run_async', side_effect=lambda fn, done, failed=None: calls.append((done, failed))):
            self.window.play_row(self.window.store.get(first))
            self.window.play_row(self.window.store.get(second))
        calls[0][0]([dict(trackName='wrong song')])
        self.assertIsNone(self.window.lyrics_candidates)
        calls[1][0]([])
        self.assertEqual(self.window.lyrics_candidates, [])
        self.assertEqual(self.window.lyrics_button.text(), 'Найти текст')
        self.assertEqual(self.window.current, second)

    def test_lyrics_popup_can_close_while_request_is_running(self):
        ident = self.ready('viewer')
        with patch.object(self.window, 'start_lyrics_lookup'):
            self.window.play_row(self.window.store.get(ident))
        release = Event()
        self.window.lyrics_client.search = lambda *args, **kwargs: (release.wait(2) and []) or []
        self.window.lyrics_candidates = None
        self.window.show_lyrics()
        dialog = self.window.lyrics_window
        dialog.reject()
        self.application.processEvents()
        release.set()
        deadline = time.monotonic()+3
        while self.window.jobs and time.monotonic() < deadline:
            self.application.processEvents()
        self.assertFalse(self.window.jobs)
        self.assertIsNone(self.window.lyrics_window)
        self.assertEqual(self.window.current, ident)

    def test_replacement_dialog_allows_editing_query_after_empty_search(self):
        callbacks = []
        with patch.object(self.window, 'run_async', side_effect=lambda fn, done, failed=None: callbacks.append(done)):
            dialog = module.SearchDialog('Artist Song', self.window)
            callbacks[0]([])
            self.assertIn('ничего не найдено', dialog.info.text())
            dialog.query.setText('Исполнитель Другая песня')
            dialog.run_search()
            callbacks[1]([dict(title='Нужная песня', url='https://youtu.be/result', duration=180, channel='Artist')])
            self.assertEqual(dialog.list.count(), 1)
            dialog.list.setCurrentRow(0)
            dialog.accept()
        self.assertEqual(dialog.choice['url'], 'https://youtu.be/result')

    def test_replacement_dialog_can_accept_youtube_link_without_search(self):
        with patch.object(self.window, 'run_async') as network:
            dialog = module.SearchDialog('', self.window)
            dialog.manual.setText('https://music.yandex.ru/album/1/track/2')
            dialog.choose_manual()
            self.assertIsNone(dialog.choice)
            dialog.manual.setText('https://youtu.be/example')
            dialog.choose_manual()
        network.assert_not_called()
        self.assertEqual(dialog.choice['url'], 'https://youtu.be/example')

    def test_spotify_replacement_resolves_source_url_and_searches_youtube(self):
        url = 'https://open.spotify.com/track/68zS1pu1EGdiQQ5pZf4A00?si=example'
        ident = self.window.store.add(url)
        self.window.store.update(ident, status='Нужна замена')
        self.window.refresh()
        self.window.list.setCurrentRow(0)
        metadata = dict(artist='Artist', track='Song', title='Artist — Song', duration=180)
        result = dict(title='Artist — Song', url='https://youtu.be/abcdefghijk', duration=180, channel='Artist')

        def inspect_dialog(dialog):
            self.assertEqual(dialog.query.text(), 'Artist Song')
            self.assertEqual(dialog.list.count(), 1)
            return 0

        with patch.object(module, 'spotify_metadata', return_value=metadata) as lookup, \
                patch.object(module, 'search', return_value=[result]) as search, \
                patch.object(self.window, 'run_async', side_effect=lambda fn, done, failed=None: done(fn())), \
                patch.object(module.SearchDialog, 'exec', new=inspect_dialog):
            self.window.find_version()
        lookup.assert_called_once()
        self.assertEqual(lookup.call_args.args[0].split('?')[0], url.split('?')[0])
        search.assert_called_once_with('Artist Song')
        self.assertEqual(self.window.store.get(ident)['title'], 'Artist — Song')

    def test_failed_metadata_lookup_does_not_search_for_the_source_url(self):
        calls = []
        with patch.object(self.window, 'run_async', side_effect=lambda fn, done, failed=None: calls.append((done, failed))):
            dialog = module.SearchDialog('', self.window, 'https://music.yandex.ru/album/1/track/2', lambda: None)
            self.assertEqual(len(calls), 1)
            calls[0][1]('Яндекс ограничил доступ (HTTP 451).')
            self.assertEqual(dialog.query.text(), '')
            self.assertIn('451', dialog.info.text())
            self.assertTrue(dialog.search_button.isEnabled())
            dialog.reject()

    def test_delete_current_track_removes_file_and_plays_next(self):
        from PySide6.QtWidgets import QMessageBox
        first, second = self.ready('a'), self.ready('b')
        folder = module.APP/'tracks'
        folder.mkdir(exist_ok=True)
        audio = folder/(first+'.mp3')
        audio.write_bytes(b'test audio')
        self.window.store.update(first,path=str(audio))
        self.window.play_row(self.window.store.get(first))
        self.window.list.setCurrentRow(0)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.window.delete_selected()
        self.assertIsNone(self.window.store.get(first))
        self.assertFalse(audio.exists())
        self.assertEqual(self.window.current,second)
        self.assertEqual(self.window.history_list.count(),0)

    def test_delete_confirmation_can_be_cancelled(self):
        from PySide6.QtWidgets import QMessageBox
        ident=self.ready()
        self.window.list.setCurrentRow(0)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.No):
            self.window.delete_selected()
        self.assertIsNotNone(self.window.store.get(ident))

    def test_history_delete_updates_profiles(self):
        from PySide6.QtWidgets import QMessageBox
        ident=self.ready()
        self.window.store.rate(ident,8,'remove')
        self.window.store.update(ident,status='Прослушано')
        self.window.refresh()
        self.window.tabs.setCurrentIndex(2)
        self.window.history_list.setCurrentRow(0)
        with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
            self.window.delete_selected()
        self.assertEqual(self.window.history_list.count(),0)
        self.assertEqual(self.window.store.profiles(),[])
        self.assertIsNone(self.window.review_id)

    def test_yandex_session_preference_and_direct_retry_are_saved(self):
        self.window.yandex_auth.setCurrentIndex(self.window.yandex_auth.findData('firefox'))
        self.assertEqual(module.load_config(module.APP)['yandex_auth'],'firefox')
        ident=self.window.store.add('https://music.yandex.ru/album/43917005/track/155671332')
        self.window.store.update(ident,status='Ошибка Яндекса',replacement='https://youtu.be/old')
        self.window.refresh();self.window.list.setCurrentRow(0)
        self.window.retry_selected()
        row=self.window.store.get(ident)
        self.assertEqual(row['status'],'Ожидает загрузки')
        self.assertEqual(row['replacement'],'')
        self.assertIn(ident,self.window.down.submitted)

    def test_yandex_profile_choice_and_session_check_show_safe_result(self):
        self.window.yandex_auth.setCurrentIndex(self.window.yandex_auth.findData('firefox'))
        self.window.set_yandex_profile('C:/TestFirefoxProfile')
        self.assertEqual(module.load_config(module.APP)['yandex_profiles']['firefox'],'C:/TestFirefoxProfile')
        with patch('app.check_session',return_value='Сессия найдена') as check, patch.object(self.window,'run_async',side_effect=lambda fn,done,failed=None:done(fn())):
            self.window.check_yandex_session()
        self.assertEqual(check.call_args.args[0]['yandex_profiles']['firefox'],'C:/TestFirefoxProfile')
        self.assertEqual(self.window.yandex_session_info.text(),'Сессия найдена')

    def test_canceled_redemption_stops_current_song_deletes_files_and_plays_next(self):
        first=self.window.store.add('https://youtu.be/first','Viewer','order-first')
        cache=module.APP/'tracks';cache.mkdir(exist_ok=True)
        audio=cache/(first+'.mp3');audio.write_bytes(b'audio')
        cover=cache/(first+'.cover.png');cover.write_bytes(b'cover placeholder')
        self.window.store.update(first,status='Готово',path=str(audio),cover_path=str(cover))
        second=self.ready('Viewer')
        self.window.store.rate(first,10);self.window.store.rate(second,2)
        self.window.play_row(self.window.store.get(first))
        self.window.redemption_updated({'id':'order-first','status':'fulfilled'})
        self.assertIsNotNone(self.window.store.get(first))
        self.window.redemption_updated({'id':'order-first','status':'canceled'})
        self.assertIsNone(self.window.store.get(first))
        self.assertFalse(audio.exists());self.assertFalse(cover.exists())
        self.assertEqual(self.window.current,second)
        self.assertEqual(self.window.store.profiles()[0]['average'],2)
        self.assertFalse(self.window.store.update_download(first,status='Готово'))
        self.window.redeem({'id':'order-first','user_name':'Viewer','user_input':'https://youtu.be/first'})
        self.assertEqual(len(self.window.store.rows()),1)

    def test_cancel_before_add_from_selected_reward_prevents_late_order(self):
        self.window.active_reward_limits={'music':120}
        self.window.redemption_updated({'id':'early-cancel','status':'canceled','reward':{'id':'music'}})
        self.window.redeem({'id':'early-cancel','user_name':'Viewer','user_input':'https://youtu.be/song','reward':{'id':'music'}})
        self.assertEqual(self.window.store.rows(),[])
        self.assertEqual(self.window.down.submitted,[])

    def test_vip_profile_buttons_grant_reset_and_remove_local_priority(self):
        self.ready('Viewer')
        self.window.tabs.setCurrentIndex(1)
        self.window.profiles.setCurrentRow(0)
        self.window.vip_grant_button.click()
        profile=self.window.store.profiles()[0]
        self.assertEqual((profile['vip'],profile['vip_used']),(1,0))
        for _ in range(3):self.window.store.add('https://youtu.be/song','Viewer')
        self.window.refresh()
        self.assertIn('0/3',self.window.vip_info.text())
        self.window.vip_reset_button.click()
        self.assertEqual(self.window.store.profiles()[0]['vip_used'],0)
        self.window.vip_remove_button.click()
        self.assertEqual(self.window.store.profiles()[0]['vip'],0)
        last=self.window.store.add('https://youtu.be/last','Viewer')
        self.assertEqual(self.window.store.get(last)['vip_priority'],0)


if __name__ == '__main__':
    unittest.main()
