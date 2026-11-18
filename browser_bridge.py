"""Local-only native messaging broker. No HTTP listener, browser tokens or cookies."""
import json
import hashlib
import shutil
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import time

HOST_NAME = 'com.twitchtune.queue'
FIREFOX_ID = 'twitchtune-queue@swerxxx'
# Chromium ID is set by the public key in the bundled manifest.
CHROMIUM_ID = 'pfnnmpmfllkegohmjckllkimggghdpoe'
MAX_MESSAGE = 262144
HISTORY_LIMIT = 50
TWITCH_ID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', re.IGNORECASE)


def migrate(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(twitch_completions)')}
    for key, sql in {
        'viewer_login': "TEXT DEFAULT ''", 'user_input': "TEXT DEFAULT ''",
        'reward_title': "TEXT DEFAULT ''", 'channel_login': "TEXT DEFAULT ''",
        'lease_token': "TEXT DEFAULT ''", 'lease_until': 'REAL DEFAULT 0',
        'browser_note': "TEXT DEFAULT ''"}.items():
        if key not in columns:
            db.execute(f'ALTER TABLE twitch_completions ADD COLUMN {key} {sql}')
    db.execute('''CREATE TABLE IF NOT EXISTS browser_bridge_session (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER,
        broadcaster TEXT, client TEXT, login TEXT, expires REAL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS browser_imports (
        redemption TEXT, broadcaster TEXT, client TEXT, event TEXT,
        PRIMARY KEY(redemption, broadcaster, client))''')
    db.commit()


class BrowserBridge:
    def __init__(self, profile):
        self.profile = Path(profile)
        self.db = sqlite3.connect(self.profile/'queue.sqlite3', timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=5000')

    def close(self):
        self.db.close()

    def heartbeat(self, enabled, client):
        user = (getattr(client, 'user', None) or {}) if client else {}
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO browser_bridge_session VALUES(1,?,?,?,?,?)',
                (bool(enabled), str(user.get('user_id','')), str(getattr(client,'client_id','') or ''),
                 str(user.get('login','')).lower(), time.time()+40))

    def session(self, login=None):
        row = self.db.execute('SELECT * FROM browser_bridge_session WHERE singleton=1').fetchone()
        if not row or not row['enabled']:
            raise ValueError('Включи расширение в настройках TwitchTune.')
        if row['expires'] < time.time():
            raise ValueError('TwitchTune закрыт или связь с ним потеряна.')
        if not row['broadcaster'] or not row['client'] or not row['login']:
            raise ValueError('Подключи Twitch в TwitchTune.')
        if login is not None and str(login).lower() != row['login']:
            raise ValueError('Открыта очередь другого канала Twitch.')
        return row

    def handle(self, message):
        if not isinstance(message, dict):
            raise ValueError('Некорректное сообщение.')
        action = message.get('action')
        if action == 'status':
            session = self.session()
            rows = self.db.execute("SELECT state,COUNT(*) n FROM twitch_completions WHERE client=? AND broadcaster=? GROUP BY state",
                (session['client'],session['broadcaster'])).fetchall()
            return dict(ok=True, channel=session['login'], counts={r['state']:r['n'] for r in rows})
        if action == 'claim':
            return self.claim(message.get('channel'), message.get('visibleIds'))
        if action == 'import_history':
            return self.import_history(message.get('channel'), message.get('orders'))
        if action == 'report':
            return self.report(message)
        if action == 'retry':
            session=self.session()
            with self.db:
                self.db.execute("UPDATE twitch_completions SET state='pending',retry_at=0,lease_token='',lease_until=0 WHERE client=? AND broadcaster=? AND state='browser_blocked'",
                    (session['client'],session['broadcaster']))
            return dict(ok=True)
        raise ValueError('Неизвестная команда расширения.')

    def claim(self, channel, visible_ids=None):
        if visible_ids is not None and (not isinstance(visible_ids, list) or len(visible_ids) > 200
                or any(not isinstance(ident, str) or not 1 <= len(ident) <= 128 for ident in visible_ids)):
            raise ValueError('Недопустимый список заявок на странице.')
        visible_filter = '' if visible_ids is None else 'AND c.redemption IN (' + ','.join('?' for _ in visible_ids) + ')'
        # Immediate transaction prevents two browser windows claiming the same job.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            session=self.session(channel)
            now=time.time()
            self.db.execute("UPDATE twitch_completions SET state='pending',lease_token='' WHERE state='browser_claimed' AND lease_until<?", (now,))
            row=self.db.execute(f'''SELECT c.*,q.rating,q.completion_status,q.status AS queue_status FROM twitch_completions c
                JOIN queue q ON q.redemption=c.redemption
                WHERE c.client=? AND c.broadcaster=? AND c.state IN ('waiting','pending','awaiting_rating','blocked')
                  AND c.retry_at<=? AND q.rating IS NOT NULL
                  AND q.status='Прослушано' AND q.completion_status='Прослушано' {visible_filter}
                ORDER BY c.retry_at,c.redemption LIMIT 1''', (session['client'],session['broadcaster'],now,*(visible_ids or []))).fetchone()
            if not row:
                self.db.commit(); return dict(ok=True,task=None)
            # Old requests missing the original input may still match by exact ID.
            token=secrets.token_hex(24)
            self.db.execute("UPDATE twitch_completions SET state='browser_claimed',lease_token=?,lease_until=? WHERE redemption=?",
                (token,now+90,row['redemption']))
            # Other known identical requests prevent matching by visible fields.
            duplicates=self.db.execute("SELECT COUNT(*) FROM twitch_completions WHERE broadcaster=? AND reward=? AND viewer_login=? AND user_input=? AND state NOT IN ('fulfilled','canceled','discarded')",
                (row['broadcaster'],row['reward'],row['viewer_login'],row['user_input'])).fetchone()[0]
            self.db.commit()
            return dict(ok=True,task=dict(id=row['redemption'],rewardId=row['reward'],channel=session['login'],
                viewer=row['viewer_login'],input=row['user_input'],rewardTitle=row['reward_title'],
                lease=token,uniqueKnownOrder=duplicates==1))
        except Exception:
            self.db.rollback(); raise

    def import_history(self, channel, orders):
        session = self.session(channel)
        if not isinstance(orders, list) or len(orders) > HISTORY_LIMIT:
            raise ValueError('Недопустимый список старых заявок.')
        events = []
        for order in orders:
            if not isinstance(order, dict):
                raise ValueError('Некорректная старая заявка.')
            ident, reward = order.get('id'), order.get('rewardId')
            viewer, text = order.get('viewer'), order.get('input')
            viewer_id = order.get('viewerId', '')
            if (not isinstance(ident, str) or not TWITCH_ID.fullmatch(ident)
                    or not isinstance(reward, str) or not TWITCH_ID.fullmatch(reward)
                    or not isinstance(viewer, str) or not 1 <= len(viewer) <= 200
                    or not isinstance(text, str) or not 1 <= len(text) <= 4000
                    or not isinstance(viewer_id, str) or len(viewer_id) > 100):
                raise ValueError('В старой заявке не хватает точных данных Twitch.')
            event = dict(id=ident, reward={'id': reward}, user_login=viewer, user_name=viewer,
                         user_id=viewer_id or None, user_input=text, status='UNFULFILLED',
                         broadcaster_user_id=session['broadcaster'], broadcaster_user_login=session['login'])
            events.append((ident, session['broadcaster'], session['client'], json.dumps(event), ident, ident, ident))
        with self.db:
            result = self.db.executemany('''INSERT OR IGNORE INTO browser_imports(redemption,broadcaster,client,event)
                SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM queue WHERE redemption=?)
                AND NOT EXISTS (SELECT 1 FROM deleted_redemptions WHERE redemption=?)
                AND NOT EXISTS (SELECT 1 FROM twitch_completions WHERE redemption=?
                    AND state IN ('fulfilled','canceled','discarded','browser_verify'))''', events)
        return dict(ok=True, queued=result.rowcount)

    def pending_history(self):
        session = self.session()
        with self.db:
            self.db.execute('''DELETE FROM browser_imports WHERE broadcaster=? AND client=? AND (
                redemption IN (SELECT redemption FROM queue WHERE redemption IS NOT NULL)
                OR redemption IN (SELECT redemption FROM deleted_redemptions)
                OR redemption IN (SELECT redemption FROM twitch_completions
                    WHERE state IN ('fulfilled','canceled','discarded','browser_verify')))''',
                (session['broadcaster'], session['client']))
        return [json.loads(row['event']) for row in self.db.execute('''SELECT event FROM browser_imports
            WHERE broadcaster=? AND client=? ORDER BY rowid LIMIT 10''', (session['broadcaster'], session['client']))]

    def acknowledge_history(self, redemption, client):
        with self.db:
            self.db.execute('DELETE FROM browser_imports WHERE redemption=? AND broadcaster=? AND client=?',
                            (redemption, client.user['user_id'], client.client_id))

    def report(self, message):
        session=self.session(message.get('channel'))
        ident,lease=message.get('id'),message.get('lease')
        outcome=message.get('outcome')
        if outcome not in ('clicked','not_found','blocked') or not isinstance(lease,str) or len(lease)!=48:
            raise ValueError('Некорректное подтверждение.')
        row=self.db.execute("SELECT * FROM twitch_completions WHERE redemption=? AND client=? AND broadcaster=?",
            (ident,session['client'],session['broadcaster'])).fetchone()
        if not row or row['lease_token']!=lease:
            raise ValueError('Задание уже неактуально.')
        # EventSub may have arrived before the browser finished reporting.
        if row['state'] in ('fulfilled','canceled','discarded'):
            return dict(ok=True,state=row['state'])
        if row['state']!='browser_claimed' or row['lease_until']<time.time():
            raise ValueError('Время задания истекло.')
        state={'clicked':'browser_verify','not_found':'pending','blocked':'browser_blocked'}[outcome]
        note=str(message.get('note') or '')[:300]
        with self.db:
            self.db.execute("UPDATE twitch_completions SET state=?,retry_at=?,browser_note=?,lease_until=0 WHERE redemption=? AND state='browser_claimed'",
                (state,time.time()+10,note,ident))
        return dict(ok=True,state=state)

    def actionable(self, ident, lease, channel):
        session=self.session(channel)
        row=self.db.execute('''SELECT c.state,c.lease_until,q.rating,q.status AS queue_status,q.completion_status FROM twitch_completions c
            JOIN queue q ON q.redemption=c.redemption WHERE c.redemption=? AND c.lease_token=?
            AND c.client=? AND c.broadcaster=?''', (ident,lease,session['client'],session['broadcaster'])).fetchone()
        return bool(row and row['state']=='browser_claimed' and row['lease_until']>=time.time()
                    and row['rating'] is not None and row['queue_status']=='Прослушано' and row['completion_status']=='Прослушано')


def register_native_host(profile, browser):
    """Current-user installation, restricted to the bundled extension ID."""
    if sys.platform!='win32':
        raise ValueError('Установка связи с браузером пока поддерживается на Windows.')
    import winreg
    if browser not in ('firefox','chrome','edge'):
        raise ValueError('Неизвестный браузер.')
    folder=Path(profile)/'browser-bridge';folder.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).resolve().parent
    if getattr(sys,'frozen',False):
        host=Path(sys.executable).parent/'TwitchTuneBridge.exe'
        if not host.is_file():
            raise ValueError('В этой сборке нет TwitchTuneBridge.exe. Установи полную новую сборку.')
        # Keep running native hosts out of the application updater's directory.
        digest=hashlib.sha256(host.read_bytes()).hexdigest()[:16]
        installed=folder/('TwitchTuneBridge-'+digest+'.exe')
        if not installed.exists():
            shutil.copy2(host,installed)
        host=installed
    else:
        host=folder/'TwitchTuneBridge.cmd'
        host.write_text('@echo off\r\n"'+sys.executable+'" "'+str(source/'browser_native_host.py')+'" %*\r\n',encoding='utf-8')
    manifest=dict(name=HOST_NAME,description='TwitchTune local reward queue bridge',path=str(host),type='stdio')
    if browser=='firefox':manifest['allowed_extensions']=[FIREFOX_ID]
    else:manifest['allowed_origins']=['chrome-extension://'+CHROMIUM_ID+'/']
    target=folder/(browser+'.json');target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'profile.json').write_text(json.dumps({'profile':str(Path(profile).resolve())}),encoding='utf-8')
    vendor={'firefox':r'Mozilla','chrome':r'Google\Chrome','edge':r'Microsoft\Edge'}[browser]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,'Software\\'+vendor+'\\NativeMessagingHosts\\'+HOST_NAME) as key:
        winreg.SetValueEx(key,'',0,winreg.REG_SZ,str(target))
    return target
