"""Telegram group inbox, deterministic matching, and Bot API transport.

Only a configured group is read. MP3 bytes are fetched after a Twitch order claims
one inbox message. Tokens never enter SQLite, settings, or exception text.
"""
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote
import requests
from mutagen.mp3 import MP3

MAX_BYTES = 20 * 1024 * 1024
MAX_SECONDS = 15 * 60
MAX_AGE = 24 * 3600
AUTO_WINDOW = 30 * 60


class TelegramError(RuntimeError):
    pass


def extract_mp3(message):
    media = message.get('audio') or message.get('document')
    sender = message.get('from', {})
    if not media or not sender.get('id') or sender.get('is_bot') or message.get('sender_chat'):
        return None
    name = media.get('file_name', '')
    if Path(name).suffix.lower() != '.mp3' and media.get('mime_type') not in ('audio/mpeg', 'audio/mp3'):
        return None
    title = ' — '.join(str(media.get(k, '')).strip() for k in ('performer', 'title') if media.get(k))
    return dict(file_id=media['file_id'], file_size=media.get('file_size') or 0,
                duration=media.get('duration') or 0, title=(title or name or 'MP3')[:500],
                user_id=str(sender['id']), nickname=sender.get('username') or sender.get('first_name') or str(sender['id']),
                message_id=message['message_id'], date=message.get('date') or time.time())


class TelegramInbox:
    def __init__(self, store):
        self.store = store
        with store.lock:
            store.db.executescript('''
                CREATE TABLE IF NOT EXISTS telegram_files (
                    code TEXT PRIMARY KEY, bot_id TEXT, chat_id TEXT, message_id INTEGER,
                    user_id TEXT, nickname TEXT, file_id TEXT, title TEXT, file_size INTEGER,
                    duration REAL, received_at REAL, used_by TEXT UNIQUE,
                    status TEXT DEFAULT 'Ожидает заказа', error TEXT DEFAULT '', announced INTEGER DEFAULT 0,
                    UNIQUE(bot_id,chat_id,message_id));
                CREATE TABLE IF NOT EXISTS telegram_links (
                    viewer_key TEXT PRIMARY KEY, chat_id TEXT, user_id TEXT,
                    UNIQUE(chat_id,user_id));
                CREATE TABLE IF NOT EXISTS telegram_state (bot_id TEXT PRIMARY KEY, offset INTEGER);
            ''')
            store.db.execute("UPDATE telegram_files SET status='Ожидает загрузки' WHERE status='Загрузка'")
            store.db.commit()

    def remember(self, bot_id, chat_id, message):
        data = extract_mp3(message)
        if not data or data['date'] < time.time() - MAX_AGE:
            return None
        with self.store.lock:
            existing = self.store.db.execute('SELECT * FROM telegram_files WHERE bot_id=? AND chat_id=? AND message_id=?',
                                            (str(bot_id), str(chat_id), data['message_id'])).fetchone()
            if existing:
                return dict(existing)
            error = ''
            if data['file_size'] > MAX_BYTES:
                error = 'MP3 больше 20 МБ — лимит загрузки через Telegram-бота.'
            elif data['duration'] > MAX_SECONDS:
                error = 'Трек длиннее 15 минут.'
            code = 'TT-' + uuid.uuid4().hex[:8].upper()
            while self.get(code):
                code = 'TT-' + uuid.uuid4().hex[:8].upper()
            self.store.db.execute('''INSERT INTO telegram_files
                (code,bot_id,chat_id,message_id,user_id,nickname,file_id,title,file_size,duration,received_at,status,error)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (code, str(bot_id), str(chat_id), data['message_id'], data['user_id'], data['nickname'],
                 data['file_id'], data['title'], data['file_size'], data['duration'], data['date'],
                 'Отклонено' if error else 'Ожидает заказа', error))
            self.store.db.commit()
            return self.get(code)

    def get(self, code):
        with self.store.lock:
            row = self.store.db.execute('SELECT * FROM telegram_files WHERE code=?', (code,)).fetchone()
            return dict(row) if row else None

    def files(self, chat_id=None, bot_id=None):
        with self.store.lock:
            sql, params = 'SELECT * FROM telegram_files WHERE received_at>=?', [time.time() - MAX_AGE]
            if chat_id is not None:
                sql += ' AND chat_id=?'
                params.append(str(chat_id))
            if bot_id is not None:
                sql += ' AND bot_id=?'
                params.append(str(bot_id))
            return [dict(r) for r in self.store.db.execute(sql + ' ORDER BY received_at DESC LIMIT 300', params)]

    def set_file(self, code, **values):
        if not values or not set(values) <= {'status', 'error', 'announced'}:
            raise ValueError('Invalid Telegram update')
        with self.store.lock:
            self.store.db.execute('UPDATE telegram_files SET ' + ','.join(k+'=?' for k in values) + ' WHERE code=?',
                                  [*values.values(), code])
            self.store.db.commit()

    def assign(self, ident, code, chat_id, bot_id):
        with self.store.lock:
            row, file = self.store.get(ident), self.get(code)
            if not row or row['source'] != 'Telegram' or row['status'] != 'Ожидает Telegram':
                raise ValueError('Выбери заявку со статусом «Ожидает Telegram».')
            if not file or file['chat_id'] != str(chat_id) or file['bot_id'] != str(bot_id):
                raise ValueError('Файл не найден в подключённом чате этого бота.')
            if file['used_by']:
                raise ValueError('Этот MP3 уже назначен другой заявке.')
            if file['status'] != 'Ожидает заказа' or file['received_at'] < time.time() - MAX_AGE:
                raise ValueError(file['error'] or 'Файл устарел или недоступен.')
            link = self.store.db.execute('SELECT * FROM telegram_links WHERE viewer_key=?', (row['viewer_key'],)).fetchone()
            owner = self.store.db.execute('SELECT * FROM telegram_links WHERE chat_id=? AND user_id=?',
                                          (str(chat_id), file['user_id'])).fetchone()
            if (link and (link['chat_id'], link['user_id']) != (str(chat_id), file['user_id'])) or (owner and owner['viewer_key'] != row['viewer_key']):
                raise ValueError('Аккаунты уже связаны иначе. Сбрось прежнюю привязку нужного зрителя в очереди.')
            # One lock/transaction makes claiming single-use even during UI/poller races.
            with self.store.db:
                self.store.db.execute('INSERT OR IGNORE INTO telegram_links VALUES(?,?,?)',
                                      (row['viewer_key'], str(chat_id), file['user_id']))
                self.store.db.execute("UPDATE telegram_files SET used_by=?, status='Ожидает загрузки' WHERE code=?", (ident, code))
                self.store.db.execute("UPDATE queue SET telegram_code=?, title=?, status='Ожидает загрузки', error='' WHERE id=?",
                                      (code, file['title'], ident))

    def unbind(self, viewer_key):
        with self.store.lock:
            self.store.db.execute('DELETE FROM telegram_links WHERE viewer_key=?', (viewer_key,))
            self.store.db.commit()

    def match_pending(self, chat_id, bot_id):
        matched = 0
        with self.store.lock:
            pending = [r for r in self.store.rows() if r['source'] == 'Telegram' and r['status'] == 'Ожидает Telegram']
            files = self.files(chat_id, bot_id)
            for row in pending:
                code = row['telegram_code']
                if not code:
                    link = self.store.db.execute('SELECT * FROM telegram_links WHERE viewer_key=? AND chat_id=?',
                                                  (row['viewer_key'], str(chat_id))).fetchone()
                    if not link or row['created_at'] < time.time() - AUTO_WINDOW:
                        continue
                    same_viewer = [r for r in pending if r['viewer_key'] == row['viewer_key']]
                    candidates = [f for f in files if f['user_id'] == link['user_id'] and not f['used_by']
                                  and f['status'] == 'Ожидает заказа' and f['received_at'] >= time.time() - AUTO_WINDOW]
                    if len(same_viewer) != 1 or len(candidates) != 1:
                        continue
                    code = candidates[0]['code']
                try:
                    self.assign(row['id'], code, chat_id, bot_id)
                    matched += 1
                    for f in files:
                        if f['code'] == code:
                            f['used_by'] = row['id']
                except ValueError as exc:
                    self.store.update(row['id'], error=str(exc))
        return matched

    def offset(self, bot_id, value=None):
        with self.store.lock:
            if value is not None:
                self.store.db.execute('INSERT INTO telegram_state VALUES(?,?) ON CONFLICT(bot_id) DO UPDATE SET offset=excluded.offset',
                                      (str(bot_id), value))
                self.store.db.commit()
            row = self.store.db.execute('SELECT offset FROM telegram_state WHERE bot_id=?', (str(bot_id),)).fetchone()
            return row['offset'] if row else 0


class TelegramClient:
    def __init__(self, token, chat_id, inbox, folder, emit, announce=True):
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token.strip()):
            raise TelegramError('Неверный формат токена Telegram-бота.')
        self.token = token.strip()
        self.chat_id = str(chat_id).strip()
        self.inbox, self.folder, self.emit = inbox, Path(folder), emit
        self.announce = announce
        self.bot_id = None
        self.stop = threading.Event()
        self.thread = None

    def api(self, method, **params):
        try:
            response = requests.post('https://api.telegram.org/bot' + self.token + '/' + method,
                                     json=params, timeout=(10, 25), allow_redirects=False)
            data = response.json()
        except (requests.RequestException, ValueError):
            raise TelegramError('Нет связи с Telegram. Проверь подключение.') from None
        if not response.ok or not data.get('ok'):
            # Never expose request URLs (they contain the token).
            detail = str(data.get('description', 'Ошибка API')).replace(self.token, '[скрыто]')[:200]
            if response.status_code == 429:
                raise TelegramError('Telegram: слишком частые запросы. Попробуй позже.')
            raise TelegramError('Telegram: ' + detail)
        return data.get('result')

    def discover_chats(self):
        self.api('getMe')
        if self.api('getWebhookInfo').get('url'):
            raise TelegramError('У бота установлен webhook. Используй отдельного бота для TwitchTune.')
        updates = self.api('getUpdates', timeout=0, allowed_updates=['message'])
        chats = {}
        for update in updates:
            chat = update.get('message', {}).get('chat', {})
            if chat.get('type') in ('group', 'supergroup'):
                chats[str(chat['id'])] = chat.get('title', str(chat['id']))
        return chats

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()

    def run(self):
        try:
            me = self.api('getMe')
            self.bot_id = str(me['id'])
            if self.api('getWebhookInfo').get('url'):
                raise TelegramError('У бота установлен webhook. Используй отдельного бота для TwitchTune.')
            chat = self.api('getChat', chat_id=self.chat_id)
            if chat.get('type') not in ('group', 'supergroup'):
                raise TelegramError('Нужен Telegram-чат: группа или супергруппа.')
            self.chat_id = str(chat['id'])
            member = self.api('getChatMember', chat_id=self.chat_id, user_id=me['id'])
            if member.get('status') not in ('administrator', 'creator') and not me.get('can_read_all_group_messages'):
                raise TelegramError('Сделай бота администратором чата или отключи Group Privacy через BotFather и добавь его заново.')
            self.emit('telegram_status', 'Подключён чат: ' + chat.get('title', self.chat_id))
            delay = 1
            while not self.stop.is_set():
                try:
                    self.inbox.match_pending(self.chat_id, self.bot_id)
                    self.process_downloads()
                    self.emit('telegram_refresh', None)
                    updates = self.api('getUpdates', offset=self.inbox.offset(self.bot_id), timeout=10, allowed_updates=['message'])
                    self.emit('telegram_status', 'Подключён чат: ' + chat.get('title', self.chat_id))
                    for update in updates:
                        if self.stop.is_set():
                            break
                        message = update.get('message', {})
                        if str(message.get('chat', {}).get('id')) == self.chat_id:
                            self.handle_message(message)
                        self.inbox.offset(self.bot_id, update['update_id'] + 1)
                    delay = 1
                except TelegramError as exc:
                    self.emit('telegram_status', str(exc))
                    if self.stop.wait(delay):
                        break
                    delay = min(delay * 2, 30)
        except TelegramError as exc:
            self.emit('telegram_status', str(exc))
        except Exception:
            self.emit('telegram_status', 'Ошибка Telegram. Переподключи бота.')

    def handle_message(self, message):
        file = self.inbox.remember(self.bot_id, self.chat_id, message)
        if not file or file['announced'] or not self.announce:
            return
        text = file['error'] if file['error'] else ('MP3 принят. В заказе на Twitch напиши: в тг ' + file['code'])
        try:
            params = dict(chat_id=self.chat_id, text=text,
                          reply_parameters={'message_id': file['message_id'], 'allow_sending_without_reply': True})
            if message.get('message_thread_id'):
                params['message_thread_id'] = message['message_thread_id']
            self.api('sendMessage', **params)
            self.inbox.set_file(file['code'], announced=1)
        except TelegramError:
            self.emit('telegram_status', 'MP3 получен, но бот не смог ответить кодом. Код есть во вкладке Telegram.')

    def process_downloads(self):
        # Active orders must not disappear behind the inbox's 300-message UI limit.
        for row in self.inbox.store.rows():
            if self.stop.is_set():
                return
            if row['source'] != 'Telegram' or row['status'] not in ('Ожидает загрузки', 'Пропущено'):
                continue
            file = self.inbox.get(row['telegram_code'])
            if not file or file['used_by'] != row['id'] or file['chat_id'] != self.chat_id or file['bot_id'] != self.bot_id:
                continue
            if row and row['status'] == 'Пропущено' and file['status'] != 'Пропущено':
                self.inbox.set_file(file['code'], status='Пропущено', error='')
            if row and row['status'] == 'Ожидает загрузки':
                try:
                    self.download(file, row)
                except Exception as exc:
                    current = self.inbox.store.get(row['id'])
                    if current and current['status'] == 'Пропущено':
                        self.inbox.set_file(file['code'], status='Пропущено', error='')
                        continue
                    if self.stop.is_set():
                        self.inbox.store.update_download(row['id'], status='Ожидает загрузки', error='')
                        self.inbox.set_file(file['code'], status='Ожидает загрузки', error='')
                        return
                    error = str(exc) if isinstance(exc, TelegramError) else 'Не удалось проверить MP3. Попробуй другой файл.'
                    self.inbox.set_file(file['code'], status='Ошибка', error=error)
                    self.inbox.store.update_download(row['id'], status='Ошибка Telegram', error=error)
                self.emit('telegram_refresh', None)

    def download(self, file, row):
        if not self.inbox.store.update_download(row['id'], status='Загрузка', error=''):
            return
        self.inbox.set_file(file['code'], status='Загрузка', error='')
        self.emit('telegram_refresh', None)
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / (file['code'] + '.mp3')
        partial = target.with_suffix('.part')
        try:
            meta = self.api('getFile', file_id=file['file_id'])
            if (meta.get('file_size') or 0) > MAX_BYTES:
                raise TelegramError('MP3 больше 20 МБ.')
            path = meta.get('file_path', '')
            if not path or path.startswith('/') or '..' in path.split('/'):
                raise TelegramError('Telegram не вернул допустимый путь файла.')
            url = 'https://api.telegram.org/file/bot' + self.token + '/' + quote(path, safe='/')
            size = 0
            try:
                with requests.get(url, stream=True, timeout=(10, 20), allow_redirects=False) as response:
                    if response.status_code != 200:
                        raise TelegramError('Не удалось скачать MP3 из Telegram.')
                    with partial.open('wb') as output:
                        for chunk in response.iter_content(64 * 1024):
                            current = self.inbox.store.get(row['id'])
                            if self.stop.is_set() or not current or current['status'] == 'Пропущено':
                                raise TelegramError('Загрузка отменена.')
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise TelegramError('MP3 больше 20 МБ.')
                            output.write(chunk)
            except requests.RequestException:
                raise TelegramError('Соединение прервано при загрузке MP3.') from None
            duration = MP3(partial).info.length
            if not 0 < duration <= MAX_SECONDS:
                raise TelegramError('MP3 пустой или длиннее 15 минут.')
            current = self.inbox.store.get(row['id'])
            if self.stop.is_set():
                self.inbox.store.update_download(row['id'], status='Ожидает загрузки')
                self.inbox.set_file(file['code'], status='Ожидает загрузки')
                return
            if current['status'] == 'Пропущено':
                self.inbox.set_file(file['code'], status='Пропущено')
                return
            partial.replace(target)
            if self.inbox.store.update_download(row['id'], status='Готово', path=str(target), error=''):
                self.inbox.set_file(file['code'], status='Готово')
            else:
                self.inbox.set_file(file['code'], status='Пропущено')
        finally:
            partial.unlink(missing_ok=True)
