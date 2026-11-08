import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import QueueStore, telegram_request
from telegram_support import TelegramInbox, TelegramClient, TelegramError, MAX_BYTES
import requests


def message(user=123, ident=1, **overrides):
    result = dict(message_id=ident, date=int(time.time()), chat={'id': -1001, 'type': 'supergroup'},
                  **{'from': {'id': user, 'username': 'tg_user'}},
                  audio=dict(file_id='file_1', file_name='song.mp3', mime_type='audio/mpeg',
                             file_size=1000, duration=180, title='Песня'))
    result.update(overrides)
    return result


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = QueueStore(Path(self.folder.name) / 'db.sqlite')
        self.inbox = TelegramInbox(self.store)
        self.client = TelegramClient('12345:SECRET', '-1001', self.inbox, self.folder.name, Mock())
        self.client.bot_id = '12345'

    def tearDown(self):
        self.store.db.close()
        self.folder.cleanup()

    def file(self, user=123, ident=1):
        return self.inbox.remember('12345', '-1001', message(user, ident))

    def order(self, text='в тг', viewer='viewer', viewer_id='42'):
        return self.store.add(text, viewer, viewer_id=viewer_id)

    def test_marker_variants_and_url_priority(self):
        for text in ('в тг', 'В ТГ!', 'в телегу', 'в телеге', 'трек в telegram', 'отправил в телеграмме'):
            self.assertEqual(telegram_request(text), '', text)
        self.assertEqual(telegram_request('в тг tt-a1b2c3d4'), 'TT-A1B2C3D4')
        self.assertIsNone(telegram_request('https://youtu.be/test в тг'))
        self.assertIsNone(telegram_request('обычный текст'))
        with self.assertRaises(ValueError): telegram_request('в тг TT-NOPE')
        with self.assertRaises(ValueError): telegram_request('в тг TT-AABBCCDD TT-11223344')

    def test_add_waiting_order_duplicate_and_profile(self):
        ident = self.store.add('в тг', 'nick', 'redemption', viewer_id='42')
        self.assertEqual(self.store.get(ident)['status'], 'Ожидает Telegram')
        self.assertIsNone(self.store.add('в тг', 'nick', 'redemption', viewer_id='42'))
        self.store.rate(ident, 8)
        self.assertEqual(self.store.profiles()[0]['average'], 8)

    def test_deduplicates_messages_and_retains_offset(self):
        a, b = self.file(), self.file()
        self.assertEqual(a['code'], b['code'])
        self.assertEqual(len(self.inbox.files()), 1)
        self.inbox.offset('12345', 567)
        self.assertEqual(TelegramInbox(self.store).offset('12345'), 567)

    def test_exact_code_and_one_file_per_order(self):
        file = self.file()
        ident = self.order('в тг ' + file['code'])
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 1)
        self.assertEqual(self.store.get(ident)['telegram_code'], file['code'])
        self.assertEqual(self.inbox.get(file['code'])['used_by'], ident)
        other = self.order('в тг ' + file['code'], viewer='other', viewer_id='99')
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 0)
        self.assertEqual(self.store.get(other)['status'], 'Ожидает Telegram')

    def test_order_can_arrive_before_file(self):
        ident = self.order('в тг TT-AABBCCDD')
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 0)
        with patch('telegram_support.uuid.uuid4', return_value=SimpleNamespace(hex='aabbccdd00000000')):
            self.file()
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 1)
        self.assertEqual(self.store.get(ident)['status'], 'Ожидает загрузки')

    def test_bare_marker_needs_binding_then_accepts_unique_file(self):
        first = self.file()
        ident = self.order()
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 0)
        self.inbox.assign(ident, first['code'], '-1001', '12345')
        second = self.file(ident=2)
        next_order = self.order()
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 1)
        self.assertEqual(self.store.get(next_order)['telegram_code'], second['code'])

    def test_ambiguity_wrong_chat_and_wrong_bot_never_claim(self):
        a = self.file()
        self.inbox.assign(self.order(), a['code'], '-1001', '12345')
        self.file(ident=2)
        self.file(ident=3)
        ident = self.order()
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 0)
        with self.assertRaises(ValueError): self.inbox.assign(ident, a['code'], '-1002', '12345')
        with self.assertRaises(ValueError): self.inbox.assign(ident, a['code'], '-1001', 'otherbot')
        self.assertEqual(self.store.get(ident)['status'], 'Ожидает Telegram')

    def test_two_orders_for_same_viewer_stay_pending_without_code(self):
        a = self.file()
        self.inbox.assign(self.order(), a['code'], '-1001', '12345')
        self.file(ident=2)
        one, two = self.order(), self.order()
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 0)
        self.assertTrue(all(self.store.get(i)['status'] == 'Ожидает Telegram' for i in (one, two)))

    def test_rejects_large_long_and_old_files(self):
        for ident, key, value in ((1, 'file_size', MAX_BYTES + 1), (2, 'duration', 901)):
            data = message(ident=ident)
            data['audio'][key] = value
            file = self.inbox.remember('12345', '-1001', data)
            self.assertEqual(file['status'], 'Отклонено')
            with self.assertRaises(ValueError): self.inbox.assign(self.order(), file['code'], '-1001', '12345')
        self.assertIsNone(self.inbox.remember('12345', '-1001', message(ident=3, date=time.time() - 90000)))

    def test_document_mp3_supported_other_documents_ignored(self):
        data = message()
        data['document'] = data.pop('audio')
        self.assertIsNotNone(self.inbox.remember('12345', '-1001', data))
        data['message_id'] = 2
        data['document'].update(file_name='photo.png', mime_type='image/png')
        self.assertIsNone(self.inbox.remember('12345', '-1001', data))

    def test_binding_conflict_and_unbind(self):
        a, b = self.file(), self.file(user=456, ident=2)
        self.inbox.assign(self.order(), a['code'], '-1001', '12345')
        order = self.order('в тг ' + b['code'])
        with self.assertRaises(ValueError): self.inbox.assign(order, b['code'], '-1001', '12345')
        self.inbox.unbind('twitch:42')
        self.inbox.assign(order, b['code'], '-1001', '12345')

    def response(self, chunks):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.status_code = 200
        response.iter_content.return_value = iter(chunks)
        return response

    def download_setup(self):
        file = self.file()
        ident = self.order('в тг ' + file['code'])
        self.inbox.match_pending('-1001', '12345')
        self.client.api = Mock(return_value=dict(file_path='music/file.mp3', file_size=5000))
        return file, ident

    def test_download_validates_content_and_finishes_queue(self):
        file, ident = self.download_setup()
        with patch('telegram_support.requests.get', return_value=self.response([b'mp3-bytes'])), \
             patch('telegram_support.MP3', return_value=SimpleNamespace(info=SimpleNamespace(length=120))):
            self.client.process_downloads()
        row = self.store.get(ident)
        self.assertEqual(row['status'], 'Готово')
        self.assertEqual(Path(row['path']).read_bytes(), b'mp3-bytes')
        self.assertEqual(self.inbox.get(file['code'])['status'], 'Готово')

    def test_corrupt_content_and_stream_limit_leave_no_partial(self):
        file, ident = self.download_setup()
        with patch('telegram_support.requests.get', return_value=self.response([b'corrupt'])), \
             patch('telegram_support.MP3', side_effect=ValueError('invalid content')):
            self.client.process_downloads()
        self.assertEqual(self.store.get(ident)['status'], 'Ошибка Telegram')
        self.assertFalse(list(Path(self.folder.name).glob('*.part')))
        self.store.update(ident, status='Ожидает загрузки')
        with patch('telegram_support.MAX_BYTES', 3), \
             patch('telegram_support.requests.get', return_value=self.response([b'over-limit'])):
            self.client.process_downloads()
        self.assertEqual(self.store.get(ident)['status'], 'Ошибка Telegram')
        self.assertFalse(list(Path(self.folder.name).glob('*.mp3')))

    def test_skip_during_stream_does_not_revive_queue(self):
        file, ident = self.download_setup()
        def chunks():
            self.store.update(ident, status='Пропущено')
            yield b'data'
        with patch('telegram_support.requests.get', return_value=self.response(chunks())):
            self.client.process_downloads()
        self.assertEqual(self.store.get(ident)['status'], 'Пропущено')
        self.assertEqual(self.inbox.get(file['code'])['status'], 'Пропущено')
        self.assertFalse(list(Path(self.folder.name).glob('*.part')))

    def test_stop_during_stream_is_resumable(self):
        _, ident = self.download_setup()
        def chunks():
            self.client.stop.set()
            yield b'data'
        with patch('telegram_support.requests.get', return_value=self.response(chunks())):
            self.client.process_downloads()
        self.assertEqual(self.store.get(ident)['status'], 'Ожидает загрузки')

    def test_network_exception_hides_bot_token(self):
        with patch('telegram_support.requests.post', side_effect=requests.ConnectionError('https://api.telegram.org/bot12345:SECRET/getMe')):
            with self.assertRaises(TelegramError) as error:
                self.client.api('getMe')
        self.assertNotIn('SECRET', str(error.exception))

    def test_bot_start_retries_network_timeout_and_reports_non_json_response(self):
        response = Mock(status_code=502)
        response.json.side_effect = ValueError('Not JSON')
        with patch('telegram_support.requests.post', side_effect=[requests.Timeout(), response]) as post:
            with self.assertRaisesRegex(TelegramError, 'HTTP 502'):
                self.client.api('getMe')
            self.assertEqual(post.call_count, 2)

    def test_reply_only_once_and_failure_retains_file(self):
        self.client.api = Mock(return_value={})
        self.client.handle_message(message())
        self.client.handle_message(message())
        self.assertEqual(self.client.api.call_count, 1)
        self.assertEqual(self.client.api.call_args.args[0], 'sendMessage')
        self.client.api.side_effect = TelegramError('no permission')
        self.client.handle_message(message(ident=2))
        self.assertEqual(len(self.inbox.files()), 2)

    def test_file_is_not_reassigned_after_restart(self):
        file, ident = self.download_setup()
        restarted = TelegramInbox(self.store)
        self.assertEqual(restarted.get(file['code'])['used_by'], ident)
        self.assertEqual(restarted.match_pending('-1001', '12345'), 0)

    def test_poll_reads_only_configured_group_and_persists_offsets(self):
        calls = []
        def api(method, **params):
            calls.append((method, params))
            if method == 'getMe': return dict(id=12345, can_read_all_group_messages=True)
            if method == 'getWebhookInfo': return dict(url='')
            if method == 'getChat': return dict(id=-1001, type='supergroup', title='Music Chat')
            if method == 'getChatMember': return dict(status='administrator')
            if method == 'sendMessage': return {}
            if method == 'getUpdates':
                if params['offset']:
                    self.client.stop.set()
                    return []
                other = message(ident=2)
                other['chat']['id'] = -999
                return [dict(update_id=10, message=other), dict(update_id=11, message=message())]
            self.fail(method)
        self.client.api = api
        self.client.run()
        self.assertEqual(len(self.inbox.files()), 1)
        self.assertEqual(self.inbox.offset('12345'), 12)
        reply = next(params for method, params in calls if method == 'sendMessage')
        self.assertEqual(reply['chat_id'], '-1001')

    def test_legacy_manual_viewer_link_migrates_to_twitch_id(self):
        file = self.file()
        ident = self.store.add('в тг', 'oldnick')
        self.inbox.assign(ident, file['code'], '-1001', '12345')
        self.store.add('https://youtu.be/test', 'oldnick', viewer_id='42')
        self.file(ident=2)
        current = self.order(viewer='newnick')
        self.assertEqual(self.inbox.match_pending('-1001', '12345'), 1)
        self.assertEqual(self.store.get(current)['status'], 'Ожидает загрузки')


if __name__ == '__main__': unittest.main()
