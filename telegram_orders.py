"""Private MP3 bot. Telegram drafts become queue entries only through Twitch."""
import json
import math
import re
import sqlite3
import time
import uuid
from html import escape
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import quote, urlparse

import requests
from mutagen.mp3 import MP3
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImageReader

from limits import MAX_TRACK_SECONDS
from telegram_support import MAX_AGE, MAX_BYTES, TelegramClient, TelegramError, extract_mp3
from twitch import OAUTH


COMMANDS = (
    ('start', 'Главное меню и вход через Twitch'),
    ('profile', 'Профиль, заявки и средняя оценка'),
    ('pending', 'Трек в ожидании и код награды'),
    ('cancel', 'Отменить подготовку MP3'),
    ('unlink', 'Отвязать или сменить Twitch'),
    ('help', 'Все команды и инструкция'),
    ('streamer', 'Статус стримера и приём MP3'),
)


def eligible_rewards(rewards, duration):
    suitable = [(limit, reward_id, title) for reward_id, limit, title in rewards if duration <= limit]
    if not suitable:
        return []
    minimum = min(limit for limit, _, _ in suitable)
    return [reward for reward in suitable if reward[0] == minimum]


class TelegramOrders(TelegramClient):
    def __init__(self, token, client_id, inbox, folder, emit, rewards, streamer_id=lambda: None):
        super().__init__(token, '', inbox, folder, emit)
        if not re.fullmatch(r'[a-zA-Z0-9]+', client_id):
            raise TelegramError('Укажи Client ID публичного приложения Twitch.')
        self.client_id = client_id
        self.store = inbox.store
        self.rewards = rewards
        self.streamer_id = streamer_id
        self.bot_id = self.token.split(':', 1)[0]
        self.logins = {}
        self.unlink_requests = {}
        self.notices = Queue()
        with self.store.lock:
            self.store.db.executescript("""
                CREATE TABLE IF NOT EXISTS tg_bot_appearance (
                    bot TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS tg_viewer_accounts (
                    bot TEXT, telegram TEXT, twitch TEXT, login TEXT,
                    PRIMARY KEY (bot, telegram), UNIQUE (bot, twitch)
                );
                CREATE TABLE IF NOT EXISTS tg_streamer_delivery (
                    id TEXT PRIMARY KEY, bot TEXT, file_id TEXT, method TEXT,
                    sender TEXT, title TEXT, created REAL, delivered INTEGER DEFAULT 0,
                    retry_after REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS tg_mp3_orders (
                    id TEXT PRIMARY KEY, code TEXT UNIQUE, bot TEXT, telegram TEXT,
                    twitch TEXT, message INTEGER, title TEXT, artist TEXT, track TEXT,
                    duration REAL, lyrics TEXT, created REAL, state TEXT,
                    UNIQUE (bot, telegram, message)
                );
            """)

    def send(self, chat, text, buttons=None, *, html=False):
        params = {'chat_id': chat, 'text': text}
        if html:
            params['parse_mode'] = 'HTML'
        if buttons:
            params['reply_markup'] = {'inline_keyboard': buttons}
        try:
            return self.api('sendMessage', **params)
        except TelegramError:
            # A blocked chat must not hold up updates from other viewers.
            self.emit('Не удалось отправить ответ в Telegram. Зритель может повторить команду.')

    def run(self):
        offset = self.inbox.offset(self.bot_id)
        while not self.stop.is_set():
            try:
                me = self.api('getMe')
                if self.api('getWebhookInfo').get('url'):
                    self.emit('Бот настроен на webhook. Используй отдельного бота для TwitchTune.')
                    return
                status = 'Бот подключён: @' + me['username']
                try:
                    self.apply_appearance()
                except TelegramError:
                    status += ' · оформление не применилось, повторится при переподключении'
                self.emit(status)
                while not self.stop.is_set():
                    self.expire_orders()
                    self.deliver_streamer_files()
                    while not self.notices.empty():
                        try:
                            chat, text = self.notices.get_nowait()
                        except Empty:
                            break
                        self.send(chat, text)
                    updates = self.api('getUpdates', offset=offset, timeout=10,
                                       allowed_updates=['message', 'callback_query'])
                    for update in updates:
                        if self.stop.is_set():
                            return
                        self.handle_update(update)
                        offset = update['update_id'] + 1
                        self.inbox.offset(self.bot_id, offset)
            except TelegramError as exc:
                self.emit(str(exc))
                self.stop.wait(8)
            except (OSError, sqlite3.Error):
                self.emit('Не удалось сохранить данные Telegram. Проверь доступ к папке TwitchTune.')
                self.stop.wait(8)

    def apply_appearance(self):
        with self.store.lock:
            if self.store.db.execute('SELECT 1 FROM tg_bot_appearance WHERE bot=?', (self.bot_id + ':streamer',)).fetchone():
                return
        self.api('setMyCommands', commands=[
            {'command': name, 'description': description} for name, description in COMMANDS
        ])
        with self.store.lock:
            previously_branded = self.store.db.execute(
                'SELECT 1 FROM tg_bot_appearance WHERE bot=?', (self.bot_id,)).fetchone()
        if not previously_branded:
            self.upload_appearance()
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO tg_bot_appearance VALUES (?)', (self.bot_id + ':streamer',))

    def upload_appearance(self):
        self.api('setMyDescription', description=(
            '🎧 TwitchTune · музыкальные заявки\n\nВойди через Twitch, пришли MP3 и текст. '
            'Бот подскажет награду и выдаст код для заказа. Оценки и статистика доступны в профиле.'
        ))
        self.api('setMyShortDescription', short_description='🎧 MP3 для Twitch · заявки, тексты и оценки TwitchTune')
        icon = QImageReader(str(Path(__file__).resolve().parent / 'assets' / 'twitchtune.ico'))
        icon.jumpToImage(icon.imageCount() - 1)
        avatar = icon.read()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if avatar.isNull() or not avatar.scaled(512, 512, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(buffer, 'JPEG', 94):
            raise TelegramError('Не удалось подготовить аватар TwitchTune.')
        try:
            response = requests.post(
                'https://api.telegram.org/bot' + self.token + '/setMyProfilePhoto',
                data={'photo': json.dumps({'type': 'static', 'photo': 'attach://avatar'})},
                files={'avatar': ('twitchtune.jpg', bytes(buffer.data()), 'image/jpeg')},
                timeout=(5, 15), allow_redirects=False,
            )
            uploaded = response.ok and response.json().get('ok')
        except (requests.RequestException, ValueError):
            uploaded = False
        if not uploaded:
            raise TelegramError('Telegram не принял аватар TwitchTune.')

    def send_menu(self, chat):
        account = self.account(chat)
        if not account:
            return self.begin_login(chat)
        self.send(chat, f'<b>🎧 TWITCHTUNE</b>\n\nTwitch подключён · <b>{escape(account["login"])}</b>\n'
                  'Пришли MP3, чтобы подготовить заявку. Файл получит стример.', [
                      [{'text': '👤 Мой профиль', 'callback_data': 'profile'},
                       {'text': '🎵 Моя заявка', 'callback_data': 'pending'}],
                      [{'text': '📖 Команды и помощь', 'callback_data': 'help'}],
                  ], html=True)

    def send_help(self, chat):
        commands = '\n'.join(f'/{name} — {description}' for name, description in COMMANDS)
        self.send(chat, '<b>📖 Команды TwitchTune</b>\n\n' + commands +
                  '\n\n<b>Как заказать трек</b>\nВойди через Twitch → пришли MP3 → добавь текст '
                  'или выбери «Текста нет» → вставь код в указанную награду Twitch.',
                  [[{'text': '↩ Главное меню', 'callback_data': 'menu'}]], html=True)

    def ask_unlink(self, chat):
        account = self.account(chat)
        if not account:
            return self.begin_login(chat)
        now = time.monotonic()
        self.unlink_requests = {key: value for key, value in self.unlink_requests.items() if value[2] > now}
        nonce = uuid.uuid4().hex
        self.unlink_requests[chat] = (nonce, account['twitch'], now + 300)
        self.send(chat, f'<b>Сменить аккаунт Twitch?</b>\n\nСейчас привязан <b>{escape(account["login"])}</b>. '
                  'Текущая подготовка MP3 и её код будут отменены. Оценки и история принадлежат прежнему Twitch ID.', [
                      [{'text': 'Отвязать Twitch', 'callback_data': 'confirm_unlink:' + nonce}],
                      [{'text': '↩ Назад в профиль', 'callback_data': 'profile'}],
                  ], html=True)

    def unlink(self, chat, nonce):
        confirmation = self.unlink_requests.get(chat)
        if not confirmation or confirmation[0] != nonce or confirmation[2] <= time.monotonic():
            raise ValueError('Подтверждение устарело. Открой профиль и нажми «Сменить Twitch» снова.')
        self.unlink_requests.pop(chat, None)
        with self.store.lock, self.store.db:
            account = self.account(chat)
            if not account or account['twitch'] != confirmation[1]:
                raise ValueError('Привязка уже изменилась. Открой главное меню.')
            order = self.pending(chat)
            if order:
                self.store.db.execute("UPDATE tg_mp3_orders SET state='cancelled', lyrics=NULL WHERE id=?",
                                      (order['id'],))
            self.store.db.execute('DELETE FROM tg_viewer_accounts WHERE bot=? AND telegram=?', (self.bot_id, chat))
        if order:
            try:
                (self.folder / (order['id'] + '.mp3')).unlink(missing_ok=True)
            except OSError:
                self.emit('Привязка снята. Не удалось убрать временный MP3.')
        self.logins.pop(chat, None)
        self.send(chat, '<b>Twitch отвязан.</b>\nЧтобы снова отправлять треки, привяжи нужный аккаунт.', [
            [{'text': 'Войти через Twitch', 'callback_data': 'menu'}],
        ], html=True)

    def is_streamer(self, account):
        owner = self.streamer_id()
        return bool(owner and account and account['twitch'] == str(owner))

    def send_streamer_status(self, chat, account):
        if self.is_streamer(account):
            self.send(chat, '🎙 Статус: стример. Сюда будут приходить MP3, которые зрители отправляют боту.')
        else:
            self.send(chat, 'Статус стримера доступен Twitch-аккаунту канала, подключённого в TwitchTune.')

    def deliver_streamer_files(self):
        owner = self.streamer_id()
        if not owner:
            return
        with self.store.lock:
            recipient = self.store.db.execute(
                'SELECT telegram FROM tg_viewer_accounts WHERE bot=? AND twitch=?',
                (self.bot_id, str(owner))).fetchone()
            if not recipient:
                return
            files = self.store.db.execute(
                'SELECT * FROM tg_streamer_delivery WHERE bot=? AND delivered=0 AND retry_after<=? ORDER BY created LIMIT 10',
                (self.bot_id, time.time())).fetchall()
        for file in files:
            if self.stop.is_set() or str(self.streamer_id()) != str(owner):
                return
            kind = file['method']
            caption = f"🎵 {file['title']}\nОт Twitch: {file['sender']}"
            try:
                self.api('sendAudio' if kind == 'audio' else 'sendDocument',
                         chat_id=recipient['telegram'], caption=caption, **{kind: file['file_id']})
            except TelegramError:
                with self.store.lock, self.store.db:
                    self.store.db.execute('UPDATE tg_streamer_delivery SET retry_after=? WHERE id=?',
                                          (time.time() + 60, file['id']))
                return
            with self.store.lock, self.store.db:
                self.store.db.execute('UPDATE tg_streamer_delivery SET delivered=1 WHERE id=?', (file['id'],))

    def account(self, chat):
        with self.store.lock:
            return self.store.db.execute(
                'SELECT * FROM tg_viewer_accounts WHERE bot=? AND telegram=?', (self.bot_id, chat)
            ).fetchone()

    def twitch_json(self, endpoint, *, data=None, token=None):
        try:
            if data is not None:
                response = requests.post(OAUTH + endpoint, data=data, timeout=12, allow_redirects=False)
            else:
                response = requests.get(OAUTH + endpoint, headers={'Authorization': 'OAuth ' + token},
                                        timeout=12, allow_redirects=False)
            body = response.json()
        except (requests.RequestException, ValueError):
            raise ValueError('Twitch недоступен. Попробуй чуть позже.') from None
        if not isinstance(body, dict):
            raise ValueError('Twitch вернул неожиданный ответ.')
        return response.ok, body

    def begin_login(self, chat):
        now = time.monotonic()
        self.logins = {key: value for key, value in self.logins.items() if value['expires'] > now}
        pending = self.logins.get(chat)
        if pending is None:
            if len(self.logins) >= 100:
                raise ValueError('Сейчас много регистраций. Попробуй чуть позже.')
            ok, body = self.twitch_json('device', data={'client_id': self.client_id, 'scopes': ''})
            url = body.get('verification_uri', '')
            parsed = urlparse(url)
            if (not ok or parsed.scheme != 'https' or parsed.netloc not in ('www.twitch.tv', 'twitch.tv')
                    or parsed.path != '/activate' or not body.get('device_code') or not body.get('user_code')):
                raise ValueError('Не удалось начать вход. Попроси стримера проверить публичный Twitch Client ID.')
            interval = max(5, int(body.get('interval') or 5))
            pending = dict(device=body['device_code'], url=url, code=body['user_code'],
                           expires=now + min(1800, int(body.get('expires_in') or 1800)),
                           next_poll=now + interval, interval=interval)
            self.logins[chat] = pending
        self.send(chat, '<b>🎧 TWITCHTUNE</b>\n<i>Музыкальные заявки на стрим</i>\n\n'
                  'Войди в свой Twitch и подтверди код <code>' + escape(pending['code']) +
                  '</code>. После этого вернись сюда и нажми «Проверить вход».', [
                      [{'text': 'Войти через Twitch', 'url': pending['url']}],
                      [{'text': '✓ Проверить вход', 'callback_data': 'login'}],
                      [{'text': '📖 Команды и помощь', 'callback_data': 'help'}],
                  ], html=True)

    def finish_login(self, chat):
        if self.account(chat):
            return self.send_profile(chat)
        pending = self.logins.get(chat)
        now = time.monotonic()
        if not pending or pending['expires'] <= now:
            self.logins.pop(chat, None)
            raise ValueError('Код входа истёк. Начни заново командой /start.')
        if now < pending['next_poll']:
            raise ValueError('Подожди несколько секунд и снова нажми «Проверить вход».')
        pending['next_poll'] = now + pending['interval']
        ok, body = self.twitch_json('token', data={
            'client_id': self.client_id, 'device_code': pending['device'],
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
        })
        if not ok:
            error = body.get('message') or body.get('error')
            if error == 'slow_down':
                pending['interval'] += 5
                pending['next_poll'] = now + pending['interval']
            if error in ('authorization_pending', 'slow_down'):
                raise ValueError('Дождись подтверждения на Twitch и снова нажми «Проверить вход».')
            self.logins.pop(chat, None)
            raise ValueError('Вход не завершён. Запроси новый код через /start.')
        self.logins.pop(chat, None)
        token = body.get('access_token')
        if not token:
            raise ValueError('Twitch не подтвердил аккаунт. Начни заново через /start.')
        valid, identity = self.twitch_json('validate', token=token)
        twitch_id, login = str(identity.get('user_id') or ''), identity.get('login') or ''
        if (not valid or identity.get('client_id') != self.client_id
                or not twitch_id.isdecimal() or not re.fullmatch(r'[a-zA-Z0-9_]{1,25}', login)):
            raise ValueError('Twitch не подтвердил аккаунт. Начни заново через /start.')
        try:
            with self.store.lock, self.store.db:
                self.store.db.execute('INSERT INTO tg_viewer_accounts VALUES (?, ?, ?, ?)',
                                      (self.bot_id, chat, twitch_id, login))
        except sqlite3.IntegrityError:
            raise ValueError('Этот аккаунт Twitch уже привязан к другому Telegram.') from None
        self.send_menu(chat)

    def send_profile(self, chat):
        account = self.account(chat)
        if not account:
            return self.begin_login(chat)
        key = 'twitch:' + account['twitch']
        profile = next((p for p in self.store.profiles() if p['viewer_key'] == key), None)
        name = profile['nickname'] if profile else account['login']
        total = profile['total'] if profile else 0
        rated = profile['rated'] if profile else 0
        average = profile['average66'] if profile else None
        score = f'{average:.2f}/66' if average is not None else 'пока нет оценок'
        filled = max(0, min(10, round(average / 66 * 10))) if average is not None else 0
        bar = '▰' * filled + '▱' * (10 - filled)
        self.send(chat, f'<b>👤 Профиль TwitchTune</b>\n'
                  f'Twitch · <b>{escape(name)}</b>\n\n'
                  f'🎵 Заявок: {total}\n⭐ Оценено: {rated}\n'
                  f'<b>Средняя оценка</b> · {score}\n<code>{bar}</code>', [
                      [{'text': '↻ Обновить', 'callback_data': 'profile'},
                       {'text': '🎵 Моя заявка', 'callback_data': 'pending'}],
                      [{'text': '🔗 Сменить Twitch', 'callback_data': 'unlink'}],
                      [{'text': '↩ Главное меню', 'callback_data': 'menu'}],
                  ], html=True)

    def pending(self, chat):
        with self.store.lock:
            return self.store.db.execute(
                """SELECT * FROM tg_mp3_orders WHERE bot=? AND telegram=?
                   AND state='pending' AND created>?""", (self.bot_id, chat, time.time() - MAX_AGE)
            ).fetchone()

    def cancel(self, chat):
        with self.store.lock, self.store.db:
            order = self.pending(chat)
            if not order:
                return False
            (self.folder / (order['id'] + '.mp3')).unlink(missing_ok=True)
            self.store.db.execute("UPDATE tg_mp3_orders SET state='cancelled', lyrics=NULL WHERE id=?", (order['id'],))
        return True

    def expire_orders(self):
        with self.store.lock, self.store.db:
            cutoff = time.time() - MAX_AGE
            expired = self.store.db.execute(
                "SELECT id FROM tg_mp3_orders WHERE state='pending' AND created<=?",
                (cutoff,)).fetchall()
            for row in expired:
                (self.folder / (row['id'] + '.mp3')).unlink(missing_ok=True)
            self.store.db.execute('DELETE FROM tg_mp3_orders WHERE created<=?', (cutoff,))
            self.store.db.execute('DELETE FROM tg_streamer_delivery WHERE created<=?', (cutoff,))

    def stage(self, chat, account, media):
        with self.store.lock:
            replay = self.store.db.execute(
                'SELECT state FROM tg_mp3_orders WHERE bot=? AND telegram=? AND message=?',
                (self.bot_id, chat, media['message_id'])).fetchone()
            capacity = self.store.db.execute(
                "SELECT COUNT(*) FROM tg_mp3_orders WHERE state='pending'").fetchone()[0]
        if replay:
            return self.send_pending(chat) if replay['state'] == 'pending' else None
        if self.pending(chat):
            raise ValueError('У тебя уже есть подготовка MP3. Продолжи через /pending или отмени через /cancel.')
        if capacity >= 100:
            raise ValueError('Слишком много MP3 ожидают награды. Попробуй позже.')
        if media['file_size'] > MAX_BYTES:
            raise ValueError('Бот принимает MP3 размером до 20 МБ.')
        ident = uuid.uuid4().hex
        code = 'TT-' + ident[:8].upper()
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / (ident + '.mp3')
        partial = target.with_suffix('.tg-part')
        saved = False
        try:
            try:
                info = self.api('getFile', file_id=media['file_id'])
            except TelegramError:
                raise ValueError('Telegram не смог скачать файл. Проверь размер и пришли MP3 снова.') from None
            path = info.get('file_path', '')
            if ((info.get('file_size') or 0) > MAX_BYTES or not path or path.startswith('/')
                    or '..' in path.split('/')):
                raise ValueError('Telegram не смог выдать MP3 размером до 20 МБ.')
            url = 'https://api.telegram.org/file/bot' + self.token + '/' + quote(path, safe='/')
            try:
                with requests.get(url, stream=True, timeout=(5, 12), allow_redirects=False) as response:
                    if response.status_code != 200:
                        raise ValueError('Telegram не отдал MP3. Пришли файл ещё раз.')
                    size = 0
                    with partial.open('wb') as output:
                        for chunk in response.iter_content(65536):
                            size += len(chunk)
                            if self.stop.is_set() or size > MAX_BYTES:
                                raise ValueError('Загрузка отменена или MP3 больше 20 МБ.')
                            output.write(chunk)
            except requests.RequestException:
                raise ValueError('Загрузка MP3 прервалась. Пришли файл ещё раз.') from None
            try:
                audio = MP3(partial)
                duration = float(audio.info.length)
            except Exception:
                raise ValueError('Файл не удалось прочитать как MP3.') from None
            if not math.isfinite(duration) or not 0 < duration <= MAX_TRACK_SECONDS:
                raise ValueError('Допустимая длительность MP3 — до 90 минут.')
            artist = str(audio.tags.get('TPE1', ''))[:300] if audio.tags else ''
            track = str(audio.tags.get('TIT2', ''))[:300] if audio.tags else ''
            partial.replace(target)
            with self.store.lock, self.store.db:
                self.store.db.execute(
                    """INSERT INTO tg_mp3_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'pending')""",
                    (ident, code, self.bot_id, chat, account['twitch'], media['message_id'],
                     media['title'], artist, track, duration, time.time()))
                self.store.db.execute(
                    'INSERT INTO tg_streamer_delivery (id, bot, file_id, method, sender, title, created) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (ident, self.bot_id, media['file_id'], media.get('kind', 'audio'),
                     account['login'], media['title'], time.time()))
            saved = True
        finally:
            partial.unlink(missing_ok=True)
            if not saved:
                target.unlink(missing_ok=True)
        self.send_pending(chat)

    def send_pending(self, chat):
        order = self.pending(chat)
        menu = [{'text': '↩ Главное меню', 'callback_data': 'menu'}]
        if not order:
            return self.send(chat, '<b>🎵 Моя заявка</b>\n\nНет MP3 в ожидании. Пришли новый трек.',
                             [menu], html=True)
        code = order['code']
        seconds = math.ceil(order['duration'])
        header = f'<b>🎵 {escape(order["title"])}</b>\n⏱ {seconds // 60}:{seconds % 60:02}\n\n'
        if order['lyrics'] is None:
            return self.send(chat, header + '<b>Шаг 2 · Текст песни</b>\n'
                             'Пришли текст одним сообщением или нажми кнопку.', [
                                 [{'text': 'Текста нет', 'callback_data': 'no_lyrics:' + code}], menu,
                             ], html=True)
        rewards = eligible_rewards(self.rewards(), order['duration'])
        if not rewards:
            return self.send(chat, header + 'Сейчас нет подходящей награды. '
                             'Уточни у стримера и проверь ещё раз.', [
                                 [{'text': '↻ Проверить награды', 'callback_data': 'pending'}], menu,
                             ], html=True)
        names = ' / '.join(title for _, _, title in rewards)
        self.send(chat, header + f'<b>Шаг 3 · Награда Twitch</b>\nВыбери «{escape(names)}» '
                  f'и вставь в её поле:\n\n<code>в тг {code}</code>\n\n'
                  'Код действует 24 часа с загрузки MP3.', [menu], html=True)

    def save_lyrics(self, chat, order, lyrics):
        with self.store.lock, self.store.db:
            self.store.db.execute(
                "UPDATE tg_mp3_orders SET lyrics=? WHERE id=? AND state='pending' AND lyrics IS NULL",
                (lyrics[:4096], order['id']))
        self.send_pending(chat)

    def handle_update(self, update):
        callback = update.get('callback_query')
        message = callback.get('message', {}) if callback else update.get('message', {})
        sender = callback.get('from', {}) if callback else message.get('from', {})
        info = message.get('chat', {})
        chat = str(info.get('id', ''))
        if info.get('type') != 'private' or not chat or chat != str(sender.get('id')) or sender.get('is_bot'):
            return
        try:
            if callback:
                try:
                    self.api('answerCallbackQuery', callback_query_id=callback['id'])
                except TelegramError:
                    pass
                action = callback.get('data', '')
                if action == 'login':
                    return self.finish_login(chat)
                if action == 'profile':
                    self.unlink_requests.pop(chat, None)
                    return self.send_profile(chat)
                if action == 'menu':
                    return self.send_menu(chat)
                if action == 'help':
                    return self.send_help(chat)
                if action == 'unlink':
                    return self.ask_unlink(chat)
                if action.startswith('confirm_unlink:'):
                    return self.unlink(chat, action.split(':', 1)[1])
                if action == 'pending' and self.account(chat):
                    return self.send_pending(chat)
                order = self.pending(chat)
                if order and order['lyrics'] is None and action == 'no_lyrics:' + order['code']:
                    self.save_lyrics(chat, order, '')
                return
            text = message.get('text', '').strip()
            command = text.split(maxsplit=1)[0].lower() if text.startswith('/') else ''
            if command == '/help':
                return self.send_help(chat)
            account = self.account(chat)
            if not account:
                return self.begin_login(chat)
            if command == '/start':
                return self.send_menu(chat)
            if command == '/profile':
                return self.send_profile(chat)
            if command == '/streamer':
                return self.send_streamer_status(chat, account)
            if command == '/unlink':
                return self.ask_unlink(chat)
            if command == '/pending':
                return self.send_pending(chat)
            if command == '/cancel':
                return self.send(chat, 'Подготовка MP3 отменена.' if self.cancel(chat) else 'Нет MP3 в ожидании.')
            media = extract_mp3(message)
            if media:
                if media['date'] <= time.time() - MAX_AGE:
                    return
                return self.stage(chat, account, dict(media, kind='audio' if message.get('audio') else 'document'))
            order = self.pending(chat)
            if text and not command and order and order['lyrics'] is None:
                return self.save_lyrics(chat, order, text)
            self.send_menu(chat)
        except ValueError as exc:
            self.send(chat, str(exc))

    def accept_reward(self, event, code):
        redemption = event.get('id')
        twitch_id = str(event.get('user_id') or '')
        reward_id = event.get('reward', {}).get('id')
        if not redemption or not twitch_id:
            raise ValueError('В награде отсутствует ID Twitch или заявки.')
        with self.store.lock, self.store.db:
            order = self.store.db.execute(
                'SELECT * FROM tg_mp3_orders WHERE bot=? AND code=? AND twitch=?',
                (self.bot_id, code, twitch_id)).fetchone()
            if order is None:
                raise ValueError('Код MP3 не найден у этого Twitch-аккаунта.')
            if order['state'] == 'accepted' or self.store.get_redemption(redemption):
                return None
            try:
                if order['state'] != 'pending' or order['created'] <= time.time() - MAX_AGE:
                    raise ValueError('Код MP3 истёк или был отменён.')
                if order['lyrics'] is None:
                    raise ValueError('Сначала отправь текст или нажми «Текста нет» в боте.')
                rewards = eligible_rewards(self.rewards(), order['duration'])
                selected = next((reward for reward in rewards if reward[1] == reward_id), None)
                if not selected:
                    titles = ' / '.join(title for _, _, title in rewards)
                    raise ValueError('Нужна награда «' + titles + '».' if titles else 'Нет подходящей награды.')
                path = self.folder / (order['id'] + '.mp3')
                if not path.is_file():
                    raise ValueError('MP3 отсутствует. Отмени подготовку и пришли файл заново.')
                ident = self.store.add_telegram_mp3(
                    dict(order) | {'path': str(path)}, event.get('user_name') or twitch_id,
                    redemption, selected[0], selected[2])
            except ValueError as exc:
                self.notices.put((order['telegram'], 'Заявка не добавлена. ' + str(exc)))
                raise
        if ident:
            self.notices.put((order['telegram'], 'Награда подтверждена. MP3 добавлен в очередь TwitchTune.'))
        return ident
