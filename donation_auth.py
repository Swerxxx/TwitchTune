"""DonationAlerts public-client login and OS credential storage."""
import json
import secrets
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlencode, parse_qs
import keyring

REDIRECT='http://127.0.0.1:8765/callback'
SCOPES='oauth-user-show oauth-donation-subscribe oauth-donation-index'
SERVICE='TwitchTune.Donations'


def credential_key(provider,client_id=''):
    return provider+(':'+client_id if provider=='donationalerts' else '')


def load_token(provider,client_id=''):
    try:
        data=json.loads(keyring.get_password(SERVICE,credential_key(provider,client_id)) or '{}')
        return str(data.get('access_token') or '')
    except Exception:return ''


def save_token(provider,token,client_id=''):
    try:
        keyring.set_password(SERVICE,credential_key(provider,client_id),json.dumps({'access_token':token}))
        return True
    except Exception:return False


def forget_token(provider,client_id=''):
    try:keyring.delete_password(SERVICE,credential_key(provider,client_id))
    except Exception:pass


def authorization_url(client_id,state):
    if not str(client_id).isdigit():raise ValueError('Укажи числовой Client ID приложения DonationAlerts.')
    return 'https://www.donationalerts.com/oauth/authorize?'+urlencode(dict(client_id=client_id,redirect_uri=REDIRECT,
        response_type='token',scope=SCOPES,state=state))


def authorize(client_id,cancelled=lambda:False,opener=webbrowser.open):
    state=secrets.token_urlsafe(32)
    url=authorization_url(client_id,state)
    result={}
    class Callback(BaseHTTPRequestHandler):
        def setup(self):
            super().setup();self.connection.settimeout(3)
        def log_message(self,*_):pass
        def reply(self,status,body):
            body=body.encode('utf-8');self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path.split('?',1)[0]!='/callback':return self.reply(404,'Не найдено')
            self.reply(200,'''<!doctype html><meta charset="utf-8"><title>TwitchTune</title>
<style>body{background:#0b1008;color:#c6f52b;font:20px sans-serif;padding:40px}</style>
<h2>TwitchTune · DonationAlerts</h2><p id="status">Подключаемся…</p>
<script>const hash=location.hash.substring(1);history.replaceState(null,'','/callback');
fetch('/callback',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:hash})
.then(r=>{document.getElementById('status').textContent=r.ok?'Вход завершён. Вернись в TwitchTune.':'Вход не подтверждён. Повтори в TwitchTune.'});</script>''')
        def do_POST(self):
            if self.path!='/callback' or self.headers.get('Origin')!='http://127.0.0.1:8765':return self.reply(403,'Отказ')
            try:size=int(self.headers.get('Content-Length','0'))
            except ValueError:return self.reply(400,'Ошибка ответа')
            if not 0<size<=16384:return self.reply(400,'Ошибка ответа')
            data=parse_qs(self.rfile.read(size).decode('utf-8'))
            if not secrets.compare_digest(data.get('state',[''])[0],state):return self.reply(403,'Неверный state')
            token=data.get('access_token',[''])[0]
            if not token or len(token)>8192 or any(c.isspace() for c in token):
                result['error']='Доступ DonationAlerts не разрешён.'
                return self.reply(400,'Доступ не разрешён')
            result['token']=token;self.reply(200,'Вход завершён')
    try:server=HTTPServer(('127.0.0.1',8765),Callback)
    except OSError:raise RuntimeError('Порт 8765 занят. Закрой другое окно входа и повтори.') from None
    server.timeout=.5
    try:
        opener(url)
        deadline=time.monotonic()+180
        while not result and not cancelled() and time.monotonic()<deadline:server.handle_request()
    finally:server.server_close()
    if result.get('token'):return result['token']
    raise RuntimeError(result.get('error') or ('Вход отменён.' if cancelled() else 'Время входа истекло. Повтори подключение.'))
