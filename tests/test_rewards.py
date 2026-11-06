import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from PySide6.QtCore import QCoreApplication, QEvent
from core import QueueStore
from media import metadata_filter
from reward_settings import guess_limit, RewardDialog, DurationControl, limit_text
from twitch import TwitchClient


class RewardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_guess_russian_reward_limits_and_fallback(self):
        for title,expected in [('Заказ трека до 2 мин',120),('до 3 минут',180),('Песня до 4 минуты',240),('До 90 мин',5400),('Заказ песни',5400)]:
            self.assertEqual(guess_limit(title), expected)

    def test_dialog_keeps_explicit_multiple_selection_and_limits(self):
        dialog = RewardDialog([{'id':str(n),'title':f'До {n} мин'} for n in (2,3,4)], {'2':120,'3':180,'4':240})
        self.assertEqual(dialog.selection(), {'2':120,'3':180,'4':240})
        dialog.controls['3'][0].setChecked(False)
        dialog.controls['4'][1].minutes.setValue(5)
        self.assertEqual(dialog.selection(), {'2':120,'4':300})
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_seconds_preserved_on_open_edit_and_save(self):
        rewards=[dict(id='music',title='Трек до 3 мин')]
        dialog=RewardDialog(rewards,{'music':185})
        duration=dialog.controls['music'][1]
        self.assertEqual((duration.minutes.value(),duration.seconds.value()),(3,5))
        self.assertEqual(dialog.selection(),{'music':185})
        duration.minutes.setValue(2)
        duration.seconds.setValue(37)
        self.assertEqual(dialog.selection(),{'music':157})
        restored=RewardDialog(rewards,dialog.selection())
        self.assertEqual(restored.selection(),{'music':157})
        for window in (dialog,restored):window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_duration_boundaries_and_keyboard_commit(self):
        duration=DurationControl(1,'Тест')
        self.assertEqual(duration.value(),1)
        duration.seconds.setValue(59)
        duration.minutes.setValue(90)
        self.assertEqual(duration.value(),5400)
        self.assertEqual(duration.seconds.maximum(),0)
        duration.minutes.setValue(0)
        self.assertEqual(duration.value(),1)
        duration.setValue(125)
        duration.minutes.lineEdit().setText('4')
        duration.seconds.lineEdit().setText('23')
        self.assertEqual(duration.value(),263)
        for value in (1,59,60,125,899,900,3600,5399,5400):
            duration.setValue(value)
            self.assertEqual(duration.value(),value)
        duration.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_reward_selection_count_and_empty_save_guard(self):
        dialog=RewardDialog([dict(id='a',title='Музыка')],{})
        check,duration=dialog.controls['a']
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.Save).isEnabled())
        self.assertFalse(duration.isEnabled())
        check.setChecked(True)
        self.assertTrue(dialog.buttons.button(QDialogButtonBox.Save).isEnabled())
        self.assertTrue(duration.isEnabled())
        self.assertIn('01',dialog.count_label.text())
        check.setChecked(False)
        self.assertEqual(dialog.selection(),{})
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)

    def test_second_precision_filter_and_label(self):
        self.assertEqual(limit_text(157),'2 мин 37 сек')
        self.assertIsNone(metadata_filter({'duration':157},max_seconds=157))
        self.assertIsNotNone(metadata_filter({'duration':158},max_seconds=157))

    def test_one_eventsub_subscription_per_reward_same_session(self):
        client = TwitchClient('client', Mock())
        client.user = {'user_id':'streamer'}
        client.api = Mock()
        client.subscribe('session', ['2','3','4','2'])
        calls = client.api.call_args_list
        self.assertEqual(len(calls), 4)
        self.assertEqual([c.kwargs['json']['condition']['reward_id'] for c in calls[:3]], ['2','3','4'])
        self.assertEqual(calls[3].kwargs['json']['type'],'channel.channel_points_custom_reward_redemption.update')
        self.assertEqual(calls[3].kwargs['json']['condition'],{'broadcaster_user_id':'streamer'})
        self.assertTrue(all(c.kwargs['json']['transport']['session_id']=='session' for c in calls))

    def test_limit_boundary_is_inclusive_unknown_duration_is_rejected(self):
        for limit in (120,180,240):
            self.assertIsNone(metadata_filter({'duration':limit},max_seconds=limit))
            self.assertIsNotNone(metadata_filter({'duration':limit+1},max_seconds=limit))
            self.assertIsNotNone(metadata_filter({},max_seconds=limit))
            self.assertIsNone(metadata_filter({},max_seconds=limit,incomplete=True))

    def test_request_limit_survives_restart_history_and_requeue(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'queue.db'
            store=QueueStore(path)
            ident=store.add('https://youtu.be/test',redemption='r',max_duration=120,reward_title='До 2 мин')
            manual=store.add('https://youtu.be/manual')
            store.update(ident,status='Пропущено')
            store.db.close()
            store=QueueStore(path)
            try:
                self.assertEqual(store.get(ident)['max_duration'],120)
                self.assertEqual(store.get(ident)['reward_title'],'До 2 мин')
                self.assertEqual(store.get(manual)['max_duration'],5400)
                store.requeue(ident)
                self.assertEqual(store.get(ident)['max_duration'],120)
            finally:store.db.close()
