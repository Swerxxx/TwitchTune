"""Persistent queue and URL validation; usable without GUI dependencies."""
import json
from limits import MAX_TRACK_SECONDS
import hashlib
from ratings import calculate, CRITERIA
import re
import sqlite3
import threading
import uuid
import time
from pathlib import Path
from urllib.parse import urlparse

DOMAINS = {
    'YouTube': ('youtube.com', 'youtu.be', 'music.youtube.com'),
    'SoundCloud': ('soundcloud.com', 'on.soundcloud.com', 'm.soundcloud.com'),
    'Яндекс Музыка': ('music.yandex.ru', 'music.yandex.com', 'music.yandex.kz', 'music.yandex.by', 'music.yandex.uz'),
    'Spotify': ('open.spotify.com',),
    'VK': ('vk.com', 'vk.ru', 'vkvideo.ru', 'm.vk.com', 'm.vk.ru'),
}

def telegram_delivery_note(text):
    """A viewer says the file is in Telegram, independent of any accompanying URL."""
    text = re.sub(r'https?://\S+', '', text, flags=re.I)
    text = re.sub(r'\bт\s*\.\s*г\.?', 'тг', text, flags=re.I)
    return bool(re.search(r'\bв\s+(?:тг|tg|telegram|телег[еу]|телеграмм?(?:е|у)?)\b', text, re.I))


def telegram_request(text):
    """None means an ordinary URL request; empty string means TG without a code."""
    if re.search(r'https?://', text, re.I):
        return None
    if not re.search(r'\bв\s+(?:тг|телег[еу]|телеграмм?е?|telegram|tg)\b', text, re.I):
        return None
    codes = re.findall(r'\bTT-[A-F0-9]{8}\b', text, re.I)
    if len(codes) > 1:
        raise ValueError('Укажи код только одного MP3.')
    if re.search(r'\bTT-', text, re.I) and not codes:
        raise ValueError('Неверный код Telegram: пример TT-A1B2C3D4.')
    return codes[0].upper() if codes else ''

def parse_link(text):
    links = re.findall(r'https?://[^\s<>"\x00-\x1f]+', text)
    if len(links) != 1:
        raise ValueError('Нужна одна ссылка на один трек, без плейлиста.')
    url = links[0].rstrip('.,!;)')
    p = urlparse(url)
    if p.username or p.password or p.port not in (None, 443, 80):
        raise ValueError('Недопустимый адрес ссылки.')
    host = (p.hostname or '').lower().removeprefix('www.')
    for source, hosts in DOMAINS.items():
        if host in hosts:
            if source == 'Spotify' and not re.fullmatch(r'/(?:intl-[a-z]+/)?track/[A-Za-z0-9]+/?', p.path):
                raise ValueError('Spotify: нужна ссылка open.spotify.com/track/…, не альбом или плейлист.')
            if source == 'YouTube' and p.path in ('/playlist', '/channel', '/feed'):
                raise ValueError('Нужна ссылка на отдельное видео.')
            if source == 'Яндекс Музыка' and not re.fullmatch(r'/(?:album/\d+/)?track/\d+/?', p.path):
                raise ValueError('Яндекс Музыка: нужна ссылка на один трек, не альбом или плейлист.')
            return url, source
    raise ValueError('Поддерживаются ссылки YouTube, SoundCloud, Яндекс Музыки, Spotify и VK.')

class QueueStore:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS queue (
            id TEXT PRIMARY KEY, redemption TEXT UNIQUE, url TEXT, source TEXT,
            viewer TEXT, title TEXT, status TEXT, path TEXT DEFAULT '',
            error TEXT DEFAULT '', position INTEGER, replacement TEXT DEFAULT '')''')
        # Additive migration: existing queues and downloaded files stay intact.
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(queue)')}
        for name, definition in {'viewer_key': "TEXT DEFAULT ''", 'rating': 'INTEGER',
                                 'completion_status': "TEXT DEFAULT 'Прослушано'",
                                 'in_queue': 'INTEGER DEFAULT 1', 'album_id': "TEXT DEFAULT ''",
                                 'album_folder': "TEXT DEFAULT ''", 'album_position': 'INTEGER DEFAULT 0',
                                 'rating_scale': 'INTEGER DEFAULT 10', 'rating_details': "TEXT DEFAULT ''",
                                 'comment': "TEXT DEFAULT ''", 'telegram_code': "TEXT DEFAULT ''",
                                 'created_at': 'REAL DEFAULT 0', 'finished_at': 'REAL DEFAULT 0',
                                 'artist': "TEXT DEFAULT ''", 'track': "TEXT DEFAULT ''",
                                 'duration': 'REAL DEFAULT 0', 'max_duration': f'INTEGER DEFAULT {MAX_TRACK_SECONDS}',
                                 'reward_title': "TEXT DEFAULT ''", 'cover_url': "TEXT DEFAULT ''",
                                 'cover_path': "TEXT DEFAULT ''", 'description_lyrics': "TEXT DEFAULT ''",
                                 'submitted_lyrics': 'TEXT', 'description_checked': 'INTEGER DEFAULT 0', 'soundcloud_description': "TEXT DEFAULT ''", 'vip_priority': 'INTEGER DEFAULT 0',
                                 'donation_priority': 'INTEGER DEFAULT 0', 'donation_provider': "TEXT DEFAULT ''",
                                 'donation_amount': "TEXT DEFAULT ''", 'donation_currency': "TEXT DEFAULT ''",
                                 'donation_time': 'REAL DEFAULT 0'}.items():
            if name not in columns:
                self.db.execute(f'ALTER TABLE queue ADD COLUMN {name} {definition}')
        self.db.execute('''CREATE TABLE IF NOT EXISTS viewers (
            viewer_key TEXT PRIMARY KEY, nickname TEXT NOT NULL)''')
        viewer_columns = {r['name'] for r in self.db.execute('PRAGMA table_info(viewers)')}
        for name in ('vip','vip_used'):
            if name not in viewer_columns:
                self.db.execute(f'ALTER TABLE viewers ADD COLUMN {name} INTEGER DEFAULT 0')
        self.db.execute('''CREATE TABLE IF NOT EXISTS albums (id TEXT PRIMARY KEY, url TEXT,
            title TEXT, artist TEXT, year TEXT, folder TEXT, source TEXT)''')
        self.db.execute("""CREATE TABLE IF NOT EXISTS donation_inbox (
            event_key TEXT PRIMARY KEY, payload TEXT, status TEXT DEFAULT 'pending',
            queue_id TEXT DEFAULT '', retry_after REAL DEFAULT 0, error TEXT DEFAULT '')""")
        self.db.execute('CREATE TABLE IF NOT EXISTS deleted_redemptions (redemption TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE IF NOT EXISTS lyrics_cache (cache_key TEXT PRIMARY KEY,payload TEXT,updated REAL)')
        for r in self.db.execute("SELECT id, viewer FROM queue WHERE viewer_key='' OR viewer_key IS NULL").fetchall():
            key = 'name:' + r['viewer'].strip().casefold()
            self.db.execute('UPDATE queue SET viewer_key=? WHERE id=?', (key, r['id']))
            self.db.execute('INSERT OR IGNORE INTO viewers(viewer_key,nickname) VALUES (?,?)', (key, r['viewer']))
        self.db.execute("UPDATE queue SET status='Ожидает загрузки' WHERE status IN ('Загрузка','Поиск записи','Поиск замены')")
        self.db.execute("UPDATE queue SET status='Готово' WHERE status='Играет'")
        if self.db.execute("SELECT 1 FROM queue WHERE in_queue=1 AND donation_priority=1 AND status NOT IN ('Прослушано','Пропущено')").fetchone():
            self._order_priority()
        # Lift only the old ordinary/donation default. Explicit Twitch limits
        # and children of a Twitch album retain their original duration.
        self.db.execute("""UPDATE queue SET max_duration=? WHERE max_duration=900
            AND source!='Telegram' AND COALESCE(reward_title,'')=''
            AND (COALESCE(redemption,'')='' OR redemption LIKE 'donation:%')
            AND NOT EXISTS (SELECT 1 FROM queue parent
                WHERE parent.source='Альбом' AND parent.album_id=queue.album_id
                AND parent.id!=queue.id
                AND (COALESCE(parent.reward_title,'')!='' OR
                     (COALESCE(parent.redemption,'')!='' AND parent.redemption NOT LIKE 'donation:%')))""",(MAX_TRACK_SECONDS,))
        self.db.commit()

    def add(self, text, viewer='Вручную', redemption=None, viewer_id=None, max_duration=MAX_TRACK_SECONDS, reward_title='', donation=None):
        if type(max_duration) is not int or not 1 <= max_duration <= MAX_TRACK_SECONDS:
            raise ValueError('Лимит длительности должен быть от 1 секунды до 90 минут.')
        tg_code = telegram_request(text)
        url, source = ('', 'Telegram') if tg_code is not None else parse_link(text)
        return self._add(url, source, viewer, redemption, viewer_id, max_duration, reward_title, tg_code, donation=donation)

    def add_local(self, metadata, viewer='Вручную'):
        if not re.fullmatch(r'[a-f0-9]{32}', metadata.get('id', '')):
            raise ValueError('Неверный идентификатор импортированного MP3.')
        return self._add('', 'MP3', viewer, local=metadata)

    def add_telegram_mp3(self, order, viewer, redemption, max_duration, reward_title):
        return self._add('', 'Telegram', viewer, redemption, order['twitch'],
                         max_duration, reward_title, order['code'], local=order)

    def _add(self, url, source, viewer='Вручную', redemption=None, viewer_id=None,
             max_duration=MAX_TRACK_SECONDS, reward_title='', tg_code=None, local=None, donation=None):
        if type(max_duration) is not int or not 1 <= max_duration <= MAX_TRACK_SECONDS:
            raise ValueError('Лимит длительности должен быть от 1 секунды до 90 минут.')
        with self.lock:
            if redemption and (self.db.execute('SELECT 1 FROM queue WHERE redemption=?', (redemption,)).fetchone()
                               or self.db.execute('SELECT 1 FROM deleted_redemptions WHERE redemption=?', (redemption,)).fetchone()):
                return None
            n = self.db.execute("SELECT count(*) FROM queue WHERE in_queue=1 AND status NOT IN ('Прослушано','Пропущено')").fetchone()[0]
            if n >= 100:
                raise ValueError('Очередь заполнена: максимум 100 активных заявок.')
            viewer = viewer.strip() or 'Вручную'
            legacy_key = 'name:' + viewer.casefold()
            key = 'twitch:' + str(viewer_id) if viewer_id else legacy_key
            if donation:
                identity=viewer.casefold() if viewer.casefold() not in ('аноним','anonymous') else donation['event_key']
                key='donor:'+donation['provider']+':'+donation['account']+':'+hashlib.sha256(identity.encode()).hexdigest()[:24]
            if not viewer_id and not donation:
                matches = [r['viewer_key'] for r in self.db.execute('SELECT * FROM viewers')
                           if r['nickname'].casefold() == viewer.casefold() and r['viewer_key'].startswith('twitch:')]
                if len(matches) == 1:
                    key = matches[0]
            if viewer_id:
                legacy = self.db.execute('SELECT * FROM viewers WHERE viewer_key=?',(legacy_key,)).fetchone()
                target = self.db.execute('SELECT * FROM viewers WHERE viewer_key=?',(key,)).fetchone()
                if legacy and legacy['vip']:
                    used = min(3,legacy['vip_used'] + (target['vip_used'] if target and target['vip'] else 0))
                    self.db.execute('INSERT INTO viewers(viewer_key,nickname,vip,vip_used) VALUES(?,?,1,?) ON CONFLICT(viewer_key) DO UPDATE SET vip=1,vip_used=excluded.vip_used',(key,viewer,used))
                self.db.execute('UPDATE queue SET viewer_key=? WHERE viewer_key=?', (key, legacy_key))
                self.db.execute('DELETE FROM viewers WHERE viewer_key=?', (legacy_key,))
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_links'").fetchone():
                    if not self.db.execute('SELECT 1 FROM telegram_links WHERE viewer_key=?', (key,)).fetchone():
                        self.db.execute('UPDATE telegram_links SET viewer_key=? WHERE viewer_key=?', (key, legacy_key))
                    else:
                        self.db.execute('DELETE FROM telegram_links WHERE viewer_key=?', (legacy_key,))
            self.db.execute('INSERT INTO viewers(viewer_key,nickname) VALUES (?,?) ON CONFLICT(viewer_key) DO UPDATE SET nickname=excluded.nickname', (key, viewer))
            profile = self.db.execute('SELECT vip,vip_used FROM viewers WHERE viewer_key=?',(key,)).fetchone()
            priority = bool(not donation and profile['vip'] and profile['vip_used'] < 3)
            ident = local['id'] if local else uuid.uuid4().hex
            pos = self.db.execute('SELECT COALESCE(MAX(position),0)+1 FROM queue').fetchone()[0]
            title = ('MP3 из Telegram' + (' · ' + tg_code if tg_code else '')) if source == 'Telegram' else url
            status = 'Ожидает Telegram' if source == 'Telegram' else 'Ожидает загрузки'
            self.db.execute('INSERT INTO queue(id,redemption,url,source,viewer,title,status,position,viewer_key,telegram_code,created_at,max_duration,reward_title) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (ident, redemption, url, source, viewer, title, status, pos, key, tg_code or '', time.time(), max_duration, str(reward_title)[:300]))
            if local:
                self.db.execute('UPDATE queue SET title=?, artist=?, track=?, duration=?, path=?, cover_path=?, status=? WHERE id=?',
                    (local['title'], local.get('artist', ''), local.get('track', ''), local['duration'],
                     local['path'], local.get('cover_path', ''), 'Готово', ident))
            if local and source == 'Telegram':
                self.db.execute('UPDATE queue SET submitted_lyrics=? WHERE id=?', (local['lyrics'], ident))
                self.db.execute(
                    "UPDATE tg_mp3_orders SET state='accepted', lyrics=NULL WHERE id=?", (ident,))
            if priority:
                self.db.execute('UPDATE viewers SET vip_used=vip_used+1 WHERE viewer_key=?',(key,))
                self.db.execute('UPDATE queue SET vip_priority=1 WHERE id=?',(ident,))
            if donation:
                self.db.execute('UPDATE queue SET donation_priority=1, donation_provider=?, donation_amount=?, donation_currency=?, donation_time=? WHERE id=?',
                    (donation['provider'],donation['amount'],donation['currency'],donation['created_at'],ident))
            if priority or donation:
                self._order_priority()
            self.db.commit()
            return ident

    def _order_priority(self):
        active=self.rows('active')
        playing=[r for r in active if r['status']=='Играет']
        waiting=[r for r in active if r['status']=='Ждёт оценки']
        upcoming=[r for r in active if r['status'] not in ('Играет','Ждёт оценки')]
        paid=sorted((r for r in upcoming if r['donation_priority']),key=lambda r:(r['donation_time'],r['created_at'],r['position']))
        vip=[r for r in upcoming if r['vip_priority'] and not r['donation_priority']]
        regular=[r for r in upcoming if not r['vip_priority'] and not r['donation_priority']]
        for position,row in zip(sorted(r['position'] for r in active),playing+paid+vip+regular+waiting):
            self.db.execute('UPDATE queue SET position=? WHERE id=?',(position,row['id']))

    def record_donation(self, event):
        with self.lock:
            with self.db:
                return self.db.execute('INSERT OR IGNORE INTO donation_inbox(event_key,payload) VALUES(?,?)',
                    (event['event_key'],json.dumps(event,ensure_ascii=False))).rowcount==1

    def pending_donations(self):
        with self.lock:
            rows=self.db.execute("SELECT payload FROM donation_inbox WHERE status='pending' AND retry_after<=? ORDER BY rowid LIMIT 20",(time.time(),)).fetchall()
            return [json.loads(r['payload']) for r in rows]

    def expand_donation(self,key,events):
        # Receipt and all its funded links commit together. Replays cannot buy
        # another set of songs after a price change, deletion or restart.
        with self.lock:
            with self.db:
                row=self.db.execute('SELECT status FROM donation_inbox WHERE event_key=?',(key,)).fetchone()
                if not row or row['status']!='pending':return False
                for event in events:
                    self.db.execute('INSERT OR IGNORE INTO donation_inbox(event_key,payload) VALUES(?,?)',
                                    (event['event_key'],json.dumps(event,ensure_ascii=False)))
                self.db.execute('UPDATE donation_inbox SET status=?,error=\'\' WHERE event_key=?',
                                ('expanded' if events else 'skipped',key))
                return True

    def donation_pending_count(self):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM donation_inbox WHERE status='pending'").fetchone()[0]

    def donation_added(self, key, ident):
        with self.lock:
            with self.db:
                self.db.execute("UPDATE donation_inbox SET status='added',queue_id=?,error='' WHERE event_key=?",(ident or '',key))

    def donation_retry(self, key, message):
        with self.lock:
            with self.db:
                self.db.execute('UPDATE donation_inbox SET retry_after=?,error=? WHERE event_key=?',(time.time()+60,str(message)[:300],key))

    def rows(self, section=None):
        with self.lock:
            if section == 'active':
                sql = "SELECT * FROM queue WHERE in_queue=1 AND status NOT IN ('Прослушано','Пропущено') ORDER BY position"
            elif section == 'history':
                sql = "SELECT * FROM queue WHERE in_queue=1 AND status IN ('Прослушано','Пропущено') ORDER BY finished_at DESC, position DESC"
            elif section is None:
                sql = 'SELECT * FROM queue WHERE in_queue=1 ORDER BY position'
            elif section == 'all':
                sql = 'SELECT * FROM queue ORDER BY position'
            else:
                raise ValueError('Неизвестный раздел очереди.')
            return [dict(r) for r in self.db.execute(sql)]

    def get(self, ident):
        with self.lock:
            r = self.db.execute('SELECT * FROM queue WHERE id=?', (ident,)).fetchone()
            return dict(r) if r else None

    def get_redemption(self, redemption):
        with self.lock:
            row=self.db.execute('SELECT * FROM queue WHERE redemption=?',(redemption,)).fetchone()
            return dict(row) if row else None

    def lyrics_cache_read(self,key):
        from lyrics import plain_text
        with self.lock:
            row=self.db.execute('SELECT payload FROM lyrics_cache WHERE cache_key=?',(key,)).fetchone()
            if not row:return []
            try:
                records=json.loads(row['payload'])
                return [r for r in records if isinstance(r,dict) and isinstance(r.get('trackName'),str) and plain_text(r)] if isinstance(records,list) else []
            except (TypeError,ValueError):return []

    def lyrics_cache_write(self,key,records):
        from lyrics import plain_text
        saved=[]
        for record in records[:10]:
            text=plain_text(record)
            if not text:continue
            item={k:record[k] for k in ('id','artistName','trackName','albumName','duration','source','sourceUrl') if k in record}
            item.update(plainLyrics=text[:100000],instrumental=False);saved.append(item)
        if not saved:return
        with self.lock:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO lyrics_cache VALUES(?,?,?)',(key,json.dumps(saved,ensure_ascii=False),time.time()))
                self.db.execute('DELETE FROM lyrics_cache WHERE cache_key NOT IN (SELECT cache_key FROM lyrics_cache ORDER BY updated DESC LIMIT 500)')

    def update(self, ident, **values):
        allowed = {'title', 'status', 'path', 'error', 'replacement', 'position', 'artist', 'track', 'duration', 'cover_url', 'cover_path', 'description_lyrics', 'description_checked','soundcloud_description'}
        if not values or not set(values) <= allowed:
            raise ValueError('Invalid queue update')
        with self.lock:
            if 'status' in values:
                old = self.get(ident)
                terminal = values['status'] in ('Прослушано', 'Пропущено')
                if terminal and old and old['status'] != values['status']:
                    values['finished_at'] = time.time()
                elif not terminal:
                    values['finished_at'] = 0
            self.db.execute('UPDATE queue SET '+', '.join(k+'=?' for k in values)+' WHERE id=?', [*values.values(), ident])
            self.db.commit()

    def move(self, ident, delta):
        with self.lock:
            rows = self.rows('active')
            index = next((i for i, r in enumerate(rows) if r['id'] == ident), None)
            if index is None:
                return
            target = index + delta
            if 0 <= target < len(rows):
                a, b = rows[index], rows[target]
                self.db.execute('UPDATE queue SET position=? WHERE id=?', (b['position'], a['id']))
                self.db.execute('UPDATE queue SET position=? WHERE id=?', (a['position'], b['id']))
                self.db.commit()

    def requeue(self, ident):
        with self.lock:
            row = self.get(ident)
            if not row or row['status'] not in ('Прослушано', 'Пропущено'):
                raise ValueError('Выбери трек из истории.')
            if len(self.rows('active')) >= 100:
                raise ValueError('Очередь заполнена: максимум 100 активных заявок.')
            position = self.db.execute('SELECT COALESCE(MAX(position),0)+1 FROM queue').fetchone()[0]
            self.db.execute('UPDATE queue SET vip_priority=0, donation_priority=0 WHERE id=?',(ident,))
            self.update(ident, status='Готово', position=position, error='')

    def delete(self, ident):
        with self.lock:
            row = self.get(ident)
            if not row:
                return None
            with self.db:
                if row['redemption']:
                    self.db.execute('INSERT OR IGNORE INTO deleted_redemptions VALUES(?)', (row['redemption'],))
                if row['album_id'] and row['album_position']==0:
                    self.db.execute('DELETE FROM queue WHERE album_id=? AND album_position>0',(row['album_id'],))
                    self.db.execute('DELETE FROM albums WHERE id=?',(row['album_id'],))
                self.db.execute('DELETE FROM queue WHERE id=?', (ident,))
                tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'telegram_files' in tables:
                    self.db.execute('DELETE FROM telegram_files WHERE used_by=?', (ident,))
                if not self.db.execute('SELECT 1 FROM queue WHERE viewer_key=?', (row['viewer_key'],)).fetchone():
                    self.db.execute('DELETE FROM viewers WHERE viewer_key=? AND vip=0', (row['viewer_key'],))
                    if 'telegram_links' in tables:
                        self.db.execute('DELETE FROM telegram_links WHERE viewer_key=?', (row['viewer_key'],))
            return row

    def cancel_redemption(self, redemption):
        if not isinstance(redemption,str) or not redemption:
            return None
        with self.lock:
            row = self.db.execute('SELECT id FROM queue WHERE redemption=?',(redemption,)).fetchone()
            if row:
                return self.delete(row['id'])
            self.db.execute('INSERT OR IGNORE INTO deleted_redemptions VALUES(?)',(redemption,))
            self.db.commit()
            return None

    def grant_vip(self, nickname):
        nickname = nickname.strip().lstrip('@')
        if not nickname or len(nickname)>100 or any(c in nickname for c in '\r\n'):
            raise ValueError('Укажи ник зрителя.')
        with self.lock:
            matches = self.db.execute('SELECT * FROM viewers WHERE lower(nickname)=lower(?)',(nickname,)).fetchall()
            if len(matches)>1:
                raise ValueError('Есть несколько профилей с таким ником. Выбери нужный профиль в списке.')
            key = matches[0]['viewer_key'] if matches else 'name:'+nickname.casefold()
            self.db.execute('INSERT OR IGNORE INTO viewers(viewer_key,nickname) VALUES(?,?)',(key,nickname))
            self.set_vip(key,True)
            return key

    def set_vip(self, key, enabled, reset=False):
        with self.lock:
            row = self.db.execute('SELECT * FROM viewers WHERE viewer_key=?',(key,)).fetchone()
            if not row:
                raise ValueError('Профиль зрителя не найден.')
            used = 0 if reset or (enabled and not row['vip']) else row['vip_used']
            self.db.execute('UPDATE viewers SET vip=?,vip_used=? WHERE viewer_key=?',(int(bool(enabled)),used,key))
            self.db.commit()

    def rate(self, ident, rating, comment=''):
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 10:
            raise ValueError('Оценка должна быть целым числом от 1 до 10.')
        if len(comment) > 2000:
            raise ValueError('Комментарий: максимум 2000 символов.')
        with self.lock:
            row=self.get(ident)
            if not row:
                raise ValueError('Заявка не найдена.')
            if row['album_position']>0:
                raise ValueError('Оценивается альбом целиком, а не отдельный трек.')
            self.db.execute("UPDATE queue SET rating=?, comment=?, rating_scale=10, rating_details='' WHERE id=?", (rating, comment.strip(), ident))
            self._archive_rated(ident)
            self.db.commit()

    def rate_criteria(self, ident, scores, text_enabled=True, comment=''):
        total = calculate(scores, text_enabled)
        if len(comment) > 2000:
            raise ValueError('Комментарий: максимум 2000 символов.')
        # Store only known criteria; an excluded text score is optional.
        clean = {key: scores.get(key) for key, _ in CRITERIA}
        if not text_enabled and (type(clean['text']) is not int or not 1 <= clean['text'] <= 6):
            clean['text'] = None
        payload = json.dumps({'version': 1, 'text_enabled': text_enabled, 'scores': clean})
        with self.lock:
            row=self.get(ident)
            if not row:
                raise ValueError('Заявка не найдена.')
            if row['album_position']>0:
                raise ValueError('Оценивается альбом целиком, а не отдельный трек.')
            self.db.execute('UPDATE queue SET rating=?, rating_scale=66, rating_details=?, comment=? WHERE id=?',
                            (total, payload, comment.strip(), ident))
            self._archive_rated(ident)
            self.db.commit()
        return total

    def finish(self, ident, status='Прослушано'):
        if status not in ('Прослушано', 'Пропущено'):
            raise ValueError('Неверный статус завершения.')
        with self.lock:
            row = self.get(ident)
            if row:
                self.db.execute('UPDATE queue SET completion_status=? WHERE id=?', (status, ident))
                self.update(ident, status=status if row['rating'] is not None else 'Ждёт оценки')

    def _archive_rated(self, ident):
        self.db.execute("UPDATE queue SET status=completion_status, finished_at=? WHERE id=? AND status='Ждёт оценки'", (time.time(), ident))

    def albums(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT a.*, COUNT(q.id) AS tracks, '
                "SUM(CASE WHEN q.path!='' THEN 1 ELSE 0 END) AS downloaded FROM albums a "
                'LEFT JOIN queue q ON q.album_id=a.id AND q.album_position>0 GROUP BY a.id ORDER BY a.rowid DESC')]

    def album_tracks(self, ident):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM queue WHERE album_id=? AND album_position>0 ORDER BY album_position', (ident,))]

    def add_album(self, metadata, folder, viewer='Вручную', redemption=None, viewer_id=None, max_duration=MAX_TRACK_SECONDS, reward_title='', donation=None):
        tracks = metadata.get('tracks') or []
        if not 1 <= len(tracks) <= 100:
            raise ValueError('В альбоме должно быть от 1 до 100 треков.')
        checked = [(track, *parse_link(track['url'])) for track in tracks]
        with self.lock:
            if redemption and (self.get_redemption(redemption) or self.db.execute('SELECT 1 FROM deleted_redemptions WHERE redemption=?',(redemption,)).fetchone()):
                return None, []
            existing = self.db.execute('SELECT id FROM albums WHERE url=?', (metadata['url'],)).fetchone()
            if existing and not redemption:
                return existing['id'], []
            ident = uuid.uuid4().hex
            parent = self._add(metadata['url'], 'Альбом', viewer, redemption, viewer_id, max_duration, reward_title, donation=donation)
            parent_row = self.get(parent)
            key, viewer = parent_row['viewer_key'], parent_row['viewer']
            position = parent_row['position']
            ids=[]
            try:
                with self.db:
                    self.db.execute('INSERT INTO albums VALUES(?,?,?,?,?,?,?)',
                        (ident,metadata['url'],metadata['title'],metadata.get('artist',''),str(metadata.get('year','')),str(folder),metadata['source']))
                    self.db.execute("UPDATE queue SET album_id=?, album_folder=?, title=?, artist=?, status='Загрузка альбома', duration=? WHERE id=?",
                        (ident,str(folder),(metadata.get('artist','')+' — ' if metadata.get('artist') else '')+metadata['title'],metadata.get('artist',''),sum(float(t.get('duration') or 0) for t in tracks),parent))
                    self.db.execute('UPDATE queue SET cover_url=? WHERE id=?',(metadata.get('cover_url') or next((t.get('cover_url') for t in tracks if t.get('cover_url')), ''),parent))
                    for number,(track,url,source) in enumerate(checked,1):
                        track_id=uuid.uuid4().hex;ids.append(track_id)
                        self.db.execute("""INSERT INTO queue(id,url,source,viewer,title,status,position,viewer_key,created_at,
                            artist,track,duration,cover_url,in_queue,album_id,album_folder,album_position,max_duration)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)""",
                            (track_id,url,source,viewer,track.get('title') or url,'Ожидает загрузки',position+number,key,time.time(),
                             track.get('artist',''),track.get('track',''),track.get('duration',0),track.get('cover_url',''),ident,str(folder),number,max_duration))
            except Exception:
                self.delete(parent)
                if donation and redemption:
                    # An internal catalog/storage failure is not a user deletion.
                    self.db.execute('DELETE FROM deleted_redemptions WHERE redemption=?',(redemption,))
                    self.db.commit()
                raise
            return ident,ids

    def album_request(self, ident):
        with self.lock:
            row=self.db.execute('SELECT * FROM queue WHERE album_id=? AND album_position=0',(ident,)).fetchone()
            return dict(row) if row else None

    def update_album_progress(self, ident):
        with self.lock:
            parent=self.album_request(ident)
            if not parent or parent['status'] not in ('Загрузка альбома','Ошибка альбома','Ожидает загрузки'):
                return
            tracks=self.album_tracks(ident)
            ready=[r for r in tracks if r['status']=='Готово' and r['path']]
            errors=[r for r in tracks if r['error']]
            if len(ready)==len(tracks) and tracks:
                self.update(parent['id'],status='Готово',error='',path=tracks[0]['path'],
                            cover_path=parent['cover_path'] or next((r['cover_path'] for r in tracks if r['cover_path']),''),
                            duration=sum(r['duration'] or 0 for r in tracks))
            else:
                self.update(parent['id'],status='Ошибка альбома' if errors else 'Загрузка альбома',
                            error=(f'Скачано {len(ready)}/{len(tracks)}. '+errors[0]['error']) if errors else '')

    def profiles(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('''SELECT v.viewer_key, v.nickname, v.vip, v.vip_used,
                COUNT(q.id) AS total, COUNT(q.rating) AS rated,
                AVG(CASE WHEN q.rating_scale=10 THEN q.rating END) AS average,
                AVG(CASE WHEN q.rating_scale=66 THEN q.rating
                    WHEN q.rating_scale=10 OR q.rating_scale IS NULL THEN 11.0+(q.rating-1)*55.0/9.0 END) AS average66
                FROM viewers v LEFT JOIN queue q ON q.viewer_key=v.viewer_key AND q.in_queue=1
                GROUP BY v.viewer_key HAVING COUNT(q.id)>0 OR v.vip=1
                ORDER BY v.vip DESC, average66 DESC, average DESC, total DESC, v.nickname''')]

    def viewer_tracks(self, key):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM queue WHERE viewer_key=? AND in_queue=1 ORDER BY position DESC', (key,))]

    def update_download(self, ident, **values):
        # The downloader must never revive a request skipped by the streamer.
        with self.lock:
            row = self.get(ident)
            if not row or row['status'] == 'Пропущено':
                return False
            self.update(ident, **values)
            return True

def load_config(folder):
    try:
        return json.loads((Path(folder)/'settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}

def save_config(folder, value):
    p = Path(folder)/'settings.json'
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(p)
