"""Twitch public device authorization and EventSub WebSocket subscription."""
import json
import threading
import time
import webbrowser
from urllib.parse import urlparse
import keyring
import requests
import websocket

BASE = 'https://api.twitch.tv/helix/'
OAUTH = 'https://id.twitch.tv/oauth2/'
SCOPE = 'channel:read:redemptions'

class TwitchClient:
    def __init__(self, client_id, emit):
        self.client_id, self.emit = client_id, emit
        self.tokens = None
        self.user = None
        self.stopping = threading.Event()
        self.socket = None
        self.thread = None
        self.last_validate = 0

    def check(self, response):
        if not response.ok:
            raise RuntimeError(f'Twitch HTTP {response.status_code}: проверь вход, Client ID и доступ к наградам.')
        return response.json()

    def persist(self):
        try:
            keyring.set_password('TwitchTune', self.client_id, json.dumps(self.tokens))
        except Exception:
            self.emit('log', 'Не удалось сохранить вход в хранилище Windows. После перезапуска войди снова.')

    def refresh(self):
        if not self.tokens or not self.tokens.get('refresh_token'):
            raise RuntimeError('Нужно снова войти в Twitch.')
        r = requests.post(OAUTH+'token', data={'client_id': self.client_id,
                          'grant_type': 'refresh_token', 'refresh_token': self.tokens['refresh_token']}, timeout=15)
        self.tokens = self.check(r)
        self.persist()

    def validate(self):
        r = requests.get(OAUTH+'validate', headers={'Authorization': 'OAuth '+self.tokens['access_token']}, timeout=15)
        if r.status_code == 401:
            self.refresh()
            r = requests.get(OAUTH+'validate', headers={'Authorization': 'OAuth '+self.tokens['access_token']}, timeout=15)
        info = self.check(r)
        if info.get('client_id') != self.client_id or SCOPE not in info.get('scopes', []):
            raise RuntimeError('Неверный Client ID или нет разрешения channel:read:redemptions. Войди заново.')
        self.user = info
        self.last_validate = time.monotonic()
        return info

    def login(self, force=False):
        if not force:
            try:
                stored = keyring.get_password('TwitchTune', self.client_id)
                if stored:
                    self.tokens = json.loads(stored)
                    return self.validate()
            except Exception:
                self.tokens = None
        self.stopping.clear()
        d = self.check(requests.post(OAUTH+'device', data={'client_id': self.client_id, 'scopes': SCOPE}, timeout=15))
        self.emit('auth', {'code': d['user_code'], 'url': d['verification_uri']})
        webbrowser.open(d['verification_uri'])
        deadline = time.monotonic()+d['expires_in']
        interval = max(1, d.get('interval', 5))
        while time.monotonic() < deadline and not self.stopping.wait(interval):
            r = requests.post(OAUTH+'token', data={'client_id': self.client_id, 'device_code': d['device_code'],
                              'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'}, timeout=15)
            if r.ok:
                self.tokens = r.json()
                self.persist()
                return self.validate()
            message = r.json().get('message', r.json().get('error', ''))
            if message == 'slow_down':
                interval += 5
            elif message not in ('authorization_pending',):
                raise RuntimeError('Вход Twitch не завершён: '+str(message))
        raise RuntimeError('Вход отменён или время ожидания истекло.')

    def api(self, method, endpoint, **kwargs):
        if time.monotonic()-self.last_validate > 3500:
            self.validate()
        headers = {'Client-Id': self.client_id, 'Authorization': 'Bearer '+self.tokens['access_token']}
        r = requests.request(method, BASE+endpoint, headers=headers, timeout=15, **kwargs)
        if r.status_code == 401:
            self.refresh()
            headers['Authorization'] = 'Bearer '+self.tokens['access_token']
            r = requests.request(method, BASE+endpoint, headers=headers, timeout=15, **kwargs)
        return self.check(r)

    def rewards(self):
        return self.api('GET', 'channel_points/custom_rewards', params={'broadcaster_id': self.user['user_id']})['data']

    def connect(self, reward_id):
        self.stopping.clear()
        self.thread = threading.Thread(target=self.listen, args=(reward_id,), daemon=True)
        self.thread.start()

    def subscribe(self, session_id, reward_id):
        ids = [reward_id] if isinstance(reward_id,str) else list(dict.fromkeys(reward_id))
        for ident in ids:
            self.api('POST','eventsub/subscriptions',json={
                'type':'channel.channel_points_custom_reward_redemption.add','version':'1',
                'condition':{'broadcaster_user_id':self.user['user_id'],'reward_id':ident},
                'transport':{'method':'websocket','session_id':session_id}})
        # Updates cover older queued requests too, even after reward selection changes.
        self.api('POST', 'eventsub/subscriptions', json={
            'type': 'channel.channel_points_custom_reward_redemption.update', 'version': '1',
            'condition': {'broadcaster_user_id': self.user['user_id']},
            'transport': {'method': 'websocket', 'session_id': session_id}})

    def listen(self, reward_id):
        delay = 1
        while not self.stopping.is_set():
            try:
                self.validate()
                self.socket = websocket.create_connection('wss://eventsub.wss.twitch.tv/ws', timeout=20)
                welcome = json.loads(self.socket.recv())
                session = welcome['payload']['session']
                self.subscribe(session['id'], reward_id)
                self.socket.settimeout((session.get('keepalive_timeout_seconds') or 10)+10)
                self.emit('connection', 'Подключено · заявки принимаются')
                delay = 1
                while not self.stopping.is_set():
                    if time.monotonic()-self.last_validate > 3500:
                        self.validate()
                    message = json.loads(self.socket.recv())
                    kind = message['metadata']['message_type']
                    if kind == 'notification':
                        self.notification(message)
                    elif kind == 'session_reconnect':
                        url = message['payload']['session']['reconnect_url']
                        p = urlparse(url)
                        if p.scheme != 'wss' or p.hostname != 'eventsub.wss.twitch.tv':
                            raise RuntimeError('Неожиданный адрес переподключения Twitch.')
                        # Keep old socket alive until the new session has welcomed us.
                        new = websocket.create_connection(url, timeout=20)
                        try:
                            new_session = json.loads(new.recv())['payload']['session']
                        except Exception:
                            new.close()
                            raise
                        old, self.socket = self.socket, new
                        old.close()
                        self.socket.settimeout((new_session.get('keepalive_timeout_seconds') or 10)+10)
                    elif kind == 'revocation':
                        self.emit('connection', 'Доступ к наградам отозван. Войди в Twitch заново.')
                        return
            except Exception as exc:
                if not self.stopping.is_set():
                    self.emit('connection', f'Связь прервана. Повтор через {delay} с.')
                    self.emit('log', str(exc))
            finally:
                if self.socket:
                    try: self.socket.close()
                    except Exception: pass
            if self.stopping.wait(delay):
                break
            delay = min(delay*2, 30)
        self.emit('connection', 'Отключено')

    def notification(self,message):
        kind = message.get('metadata',{}).get('subscription_type') or message.get('payload',{}).get('subscription',{}).get('type')
        if kind == 'channel.channel_points_custom_reward_redemption.update':
            self.emit('redemption_update',message['payload']['event'])
        elif kind == 'channel.channel_points_custom_reward_redemption.add':
            self.emit('redemption',message['payload']['event'])

    def close(self):
        self.stopping.set()
        if self.socket:
            try: self.socket.close()
            except Exception: pass

    def logout(self):
        self.close()
        if self.tokens:
            try:
                requests.post(OAUTH+'revoke', data={'client_id': self.client_id, 'token': self.tokens['access_token']}, timeout=10)
            except requests.RequestException:
                pass
        try: keyring.delete_password('TwitchTune', self.client_id)
        except Exception: pass
        self.tokens = None
