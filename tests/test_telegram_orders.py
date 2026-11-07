import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core import QueueStore
from telegram_orders import TelegramOrders
from telegram_support import TelegramInbox


class TelegramOrderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = QueueStore(Path(directory.name) / 'queue.sqlite3')
        self.addCleanup(self.store.db.close)
        self.bot = TelegramOrders('12345:test', 'client', TelegramInbox(self.store),
                                  Path(directory.name) / 'tracks', Mock(),
                                  lambda: [('short', 60, 'До минуты'), ('long', 180, 'До трёх минут')])
        self.bot.send = Mock()
        self.bot.api = Mock()
        self.chat = {'id': 99, 'type': 'private'}
        self.sender = {'id': 99}

    def message(self, **fields):
        self.bot.handle_update({'message': {
            'chat': self.chat, 'from': self.sender, 'message_id': 5, **fields}})

    def callback(self, action):
        self.bot.handle_update({'callback_query': {
            'message': {'chat': self.chat}, 'from': self.sender, 'id': 'callback', 'data': action}})

    def authorize(self, twitch_id='42'):
        self.bot.twitch_json = Mock(side_effect=[
            (True, {'verification_uri': 'https://www.twitch.tv/activate?device-code=TEST',
                    'user_code': 'TEST', 'device_code': 'hidden', 'expires_in': 300, 'interval': 5}),
            (True, {'access_token': 'not-stored'}),
            (True, {'client_id': 'client', 'login': 'viewer', 'user_id': twitch_id}),
        ])
        self.message(text='/start')
        self.assertIsNone(self.bot.account('99'))
        self.bot.logins['99']['next_poll'] = 0
        self.callback('login')
        self.assertEqual(self.bot.account('99')['twitch'], twitch_id)

    def upload(self):
        self.bot.api.return_value = {'file_path': 'music/file.mp3'}
        fixture = (Path(__file__).parent / 'fixtures' / 'silence-6s.mp3').read_bytes()
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [fixture]
        with patch('telegram_orders.requests.get', return_value=response):
            self.message(audio={'file_id': 'file', 'file_name': 'Test.mp3', 'duration': 999,
                                'file_size': len(fixture)})
        return self.bot.pending('99')

    def reward(self, tier='short', user='42', ident='reward'):
        return {'id': ident, 'reward': {'id': tier}, 'user_id': user, 'user_name': 'renamed'}

    def test_registration_upload_lyrics_reward_and_live_profile(self):
        self.authorize()
        order = self.upload()
        self.assertEqual(self.store.rows(), [])
        self.assertLess(order['duration'], 60)
        self.assertIn('Текста нет', repr(self.bot.send.call_args))
        with self.assertRaisesRegex(ValueError, 'отправь текст'):
            self.bot.accept_reward(self.reward(), order['code'])
        self.message(message_id=6, text='Текст песни\nВторая строка')
        self.assertIn('До минуты', repr(self.bot.send.call_args))
        with self.assertRaisesRegex(ValueError, 'Twitch-аккаунта'):
            self.bot.accept_reward(self.reward(user='43'), order['code'])
        with self.assertRaisesRegex(ValueError, 'До минуты'):
            self.bot.accept_reward(self.reward(tier='long'), order['code'])
        self.assertEqual(self.store.rows(), [])
        ident = self.bot.accept_reward(self.reward(), order['code'])
        row = self.store.get(ident)
        self.assertEqual(row['viewer_key'], 'twitch:42')
        self.assertEqual(row['status'], 'Готово')
        self.assertEqual(row['submitted_lyrics'], 'Текст песни\nВторая строка')
        self.assertTrue(Path(row['path']).is_file())
        self.assertIsNone(self.bot.accept_reward(self.reward(ident='second'), order['code']))
        self.store.rate(ident, 10)
        self.message(text='/profile')
        self.assertIn('renamed', self.bot.send.call_args.args[1])
        self.assertIn('Заявок: 1', self.bot.send.call_args.args[1])
        self.assertIn('66.00/66', self.bot.send.call_args.args[1])

    def test_no_lyrics_and_cancelled_reward_cannot_insert_or_consume_draft(self):
        self.authorize()
        order = self.upload()
        self.callback('no_lyrics:TT-AAAAAAAA')
        self.assertIsNone(self.bot.pending('99')['lyrics'])
        self.callback('no_lyrics:' + order['code'])
        self.assertEqual(self.bot.pending('99')['lyrics'], '')
        self.store.cancel_redemption('cancelled')
        self.assertIsNone(self.bot.accept_reward(self.reward(ident='cancelled'), order['code']))
        self.assertEqual(self.store.rows(), [])
        ident = self.bot.accept_reward(self.reward(), order['code'])
        self.assertEqual(self.store.get(ident)['submitted_lyrics'], '')

    def test_unregistered_upload_starts_login_and_group_message_is_ignored(self):
        self.bot.begin_login = Mock()
        self.message(audio={'file_id': 'file', 'file_name': 'a.mp3'})
        self.bot.begin_login.assert_called_once_with('99')
        self.bot.api.assert_not_called()
        self.chat['type'] = 'group'
        self.message(text='/start')
        self.assertEqual(self.bot.begin_login.call_count, 1)

    def test_unlink_cancels_pending_mp3_and_preserves_ratings_by_twitch_id(self):
        self.authorize()
        existing = self.store.add('https://youtu.be/previous', 'viewer', viewer_id='42')
        self.store.rate(existing, 9)
        order = self.upload()
        self.callback('no_lyrics:' + order['code'])
        self.callback('unlink')
        nonce = self.bot.unlink_requests['99'][0]
        self.assertIsNotNone(self.bot.account('99'))
        self.callback('confirm_unlink:wrong')
        self.assertIsNotNone(self.bot.account('99'))
        self.callback('confirm_unlink:' + nonce)
        self.assertIsNone(self.bot.account('99'))
        self.assertFalse((self.bot.folder / (order['id'] + '.mp3')).exists())
        with self.assertRaisesRegex(ValueError, 'отменён'):
            self.bot.accept_reward(self.reward(), order['code'])
        self.authorize('43')
        self.callback('confirm_unlink:' + nonce)
        self.assertEqual(self.bot.account('99')['twitch'], '43')
        self.assertEqual(self.store.get(existing)['rating'], 9)
        self.callback('profile')
        self.assertIn('Заявок: 0', self.bot.send.call_args.args[1])

    def test_appearance_registers_commands_and_uploads_avatar_once(self):
        with patch('telegram_orders.requests.post', return_value=Mock(ok=True)) as post:
            post.return_value.json.return_value = {'ok': True}
            self.bot.apply_appearance()
            self.bot.apply_appearance()
        post.assert_called_once()
        self.assertTrue(post.call_args.args[0].endswith('/setMyProfilePhoto'))
        self.assertTrue(post.call_args.kwargs['files']['avatar'][1].startswith(b'\xff\xd8'))
        commands = self.bot.api.call_args_list[0].kwargs['commands']
        self.assertEqual([row['command'] for row in commands],
                         ['start', 'profile', 'pending', 'cancel', 'unlink', 'help'])
