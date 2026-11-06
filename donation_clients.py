"""Documented DonationAlerts Centrifugo v2 and DonateX SignalR JSON clients."""
import json
import threading
import time
from urllib.parse import urlencode
import requests
import websocket
from donations import event_time


class AuthError(RuntimeError):pass


class RateLimitError(RuntimeError):
    def __init__(self,seconds):
        try:self.seconds=max(1,min(300,int(seconds)))
        except (ValueError,TypeError):self.seconds=60


class DonationClient:
    def __init__(self,provider,token,emit):
        self.provider,self.token,self.emit=provider,token,emit
        self.stopping=threading.Event()
        self.thread,self.history_thread,self.socket=None,None,None
        self.account,self.channel='',''
        self.started_at=time.time()
        self.last_http=0.
        self.http_lock=threading.Lock()
        self.da_seen=None

    def request(self,method,path,**kwargs):
        base='https://www.donationalerts.com/api/v1/' if self.provider=='donationalerts' else 'https://donatex.gg/api/'
        with self.http_lock:
            if self.stopping.is_set():raise RuntimeError('Отключено.')
            if self.provider=='donationalerts' and self.stopping.wait(max(0,1.05-(time.monotonic()-self.last_http))):
                raise RuntimeError('Отключено.')
            self.last_http=time.monotonic()
            response=requests.request(method,base+path,headers={'Authorization':'Bearer '+self.token},timeout=(5,12),allow_redirects=False,**kwargs)
        if response.status_code in (401,403):raise AuthError('Вход истёк или нет прав. Подключи сервис заново.')
        if response.status_code==429:raise RateLimitError(response.headers.get('Retry-After'))
        if not 200<=response.status_code<300:raise RuntimeError('Сервис временно недоступен.')
        return response.json()

    def publish(self,data):
        if not self.stopping.is_set() and isinstance(data,dict):self.emit('donation',(self.account,data))

    def start(self):
        self.thread=threading.Thread(target=self.run,name='TwitchTune-'+self.provider,daemon=True)
        self.thread.start()

    def alive(self):
        return any(t and t.is_alive() for t in (self.thread,self.history_thread))

    def close(self):
        self.stopping.set()
        if self.socket:
            try:self.socket.close(timeout=1)
            except Exception:pass

    def run(self):
        delay=1
        while not self.stopping.is_set():
            try:
                self.emit('status','Подключаемся…')
                self.connect_once()
                delay=1
            except AuthError as exc:
                if not self.stopping.is_set():self.emit('status',str(exc))
                break
            except RateLimitError as exc:
                delay=exc.seconds
                if not self.stopping.is_set():self.emit('status','Лимит сервиса. Повторим подключение после паузы.')
            except websocket.WebSocketBadStatusException as exc:
                if getattr(exc,'status_code',None) in (401,403):
                    if not self.stopping.is_set():self.emit('status','Сервис отклонил вход в события. Подключи заново и проверь права.')
                    break
                if not self.stopping.is_set():self.emit('status','Сервис временно недоступен. Переподключаемся…')
            except Exception:
                # Never log exceptions containing Authorization or signed URLs.
                if not self.stopping.is_set():self.emit('status','Связь потеряна. Переподключаемся…')
            finally:
                if self.socket:
                    try:self.socket.close(timeout=1)
                    except Exception:pass
                    self.socket=None
            if self.stopping.wait(delay):break
            delay=min(30,delay*2)

    def catchup(self):
        if self.history_thread and self.history_thread.is_alive():return
        if self.provider=='donationalerts':
            self.history_thread=threading.Thread(target=self.monitor_da,name='TwitchTune-DA-history',daemon=True)
            self.history_thread.start()
            return
        def recover():
            try:
                batch=[]
                for page in range(10):
                    if self.stopping.is_set():return
                    if self.provider=='donationalerts':
                        records=self.request('GET','alerts/donations',params={'page':page+1}).get('data',[])
                    else:
                        records=self.request('GET','v1/donations',params={'skip':page*100,'take':100,'sortOrder':'NewestFirst'})
                    if not isinstance(records,list):raise ValueError()
                    recent=[r for r in records if isinstance(r,dict) and event_time(r.get('timestamp') or r.get('created_at'))>=self.started_at]
                    batch.extend(recent)
                    if not records or len(recent)<len(records):break
                    if page==9:self.emit('warning','Большой перерыв связи: восстановлены только последние страницы донатов.')
                    if self.stopping.wait(1.1):return
                for record in sorted(batch,key=lambda r:event_time(r.get('timestamp') or r.get('created_at'))):self.publish(record)
            except Exception:
                if not self.stopping.is_set():self.emit('warning','Не удалось восстановить историю обрыва. Живое соединение продолжает работать.')
        self.history_thread=threading.Thread(target=recover,name='TwitchTune-donation-history',daemon=True)
        self.history_thread.start()

    def fetch_da_history(self,page=1):
        response=self.request('GET','alerts/donations',params={'page':page})
        records=response.get('data') if isinstance(response,dict) else None
        if not isinstance(records,list):raise ValueError('Некорректный ответ истории.')
        return [r for r in records if isinstance(r,dict) and r.get('id') is not None]

    def reconcile_da(self):
        # Snapshot existing IDs once. Later inserts can have a manually chosen,
        # backdated created_at, so timestamps must not be the live cursor.
        initial=self.da_seen is None
        known=self.da_seen or set()
        batch=[]
        for page in range(1,11):
            records=self.fetch_da_history(page)
            batch.extend(records)
            if initial or not records or any(str(r['id']) in known for r in records):break
            if page==10:self.emit('warning','История DonationAlerts ограничена 10 страницами. Проверь пропуски вручную.')
        if self.stopping.is_set():return
        fresh=[r for r in batch if str(r['id']) not in known and
               (not initial or event_time(r.get('created_at'))>=self.started_at)]
        for record in sorted(fresh,key=lambda r:event_time(r.get('created_at'))):self.publish(record)
        self.da_seen=known|{str(r['id']) for r in batch}
        self.emit('history_status',f'История проверена: {time.strftime("%H:%M:%S")} · новых сообщений: {len(fresh)}. Автопроверка каждые 10 с.')

    def monitor_da(self):
        while not self.stopping.is_set():
            delay=10
            try:self.reconcile_da()
            except AuthError:
                self.emit('history_status','Нет доступа к истории DonationAlerts. Подключи заново с правом чтения донатов.')
                delay=60
            except RateLimitError as exc:
                delay=max(10,exc.seconds)
                self.emit('history_status','Лимит DonationAlerts: проверка истории продолжится после паузы.')
            except Exception:
                if not self.stopping.is_set():self.emit('history_status','История DonationAlerts временно недоступна. Повторим проверку автоматически.')
            if self.stopping.wait(delay):return

    def ready(self,name):
        self.emit('status','Подключено · '+str(name or self.account)[:100])
        if self.provider!='donationalerts':self.catchup()

    def recv_json(self):
        raw=self.socket.recv()
        if not raw or len(raw)>1024*1024:raise RuntimeError('Соединение закрыто.')
        return [json.loads(part) for part in raw.splitlines() if part.strip()]

    def da_publication(self,packet):
        push=packet.get('result') or packet.get('push') or {}
        if push.get('channel')!=self.channel:return
        value=push.get('data') or {}
        if isinstance(value,dict) and isinstance(value.get('data'),dict):value=value['data']
        if isinstance(value,dict) and value.get('id') is not None:self.publish(value)

    def da_reply(self,ident):
        deadline=time.monotonic()+20
        while not self.stopping.is_set() and time.monotonic()<deadline:
            try:packets=self.recv_json()
            except websocket.WebSocketTimeoutException:continue
            for packet in packets:
                self.da_publication(packet)
                if packet.get('id')==ident:
                    if packet.get('error'):raise AuthError('DonationAlerts не разрешил подписку. Войди снова с правами на донаты.')
                    return packet.get('result') or {}
                # Centrifugo's join confirmation may have no command id.
                if ident==2 and packet.get('result',{}).get('channel')==self.channel and packet['result'].get('type')==1:return {}
        raise RuntimeError('Нет ответа подписки.')

    def connect_once(self):
        if self.provider=='donationalerts':return self.connect_da()
        return self.connect_dx()

    def connect_da(self):
        profile=self.request('GET','user/oauth')['data']
        self.account=str(profile['id']);self.channel='$alerts:donation_'+self.account
        # REST reconciliation also works when the live socket cannot connect.
        self.catchup()
        self.socket=websocket.create_connection('wss://centrifugo.donationalerts.com/connection/websocket',timeout=12,
                                               origin='https://www.donationalerts.com')
        self.socket.settimeout(1)
        self.socket.send(json.dumps({'id':1,'params':{'token':profile['socket_connection_token']}}))
        client=self.da_reply(1)['client']
        channels=self.request('POST','centrifuge/subscribe',json={'channels':[self.channel],'client':client})['channels']
        token=next(c['token'] for c in channels if c['channel']==self.channel)
        self.socket.send(json.dumps({'id':2,'method':1,'params':{'channel':self.channel,'token':token}}))
        self.da_reply(2);self.ready(profile.get('name'))
        ping=time.monotonic();received=ping
        while not self.stopping.is_set():
            try:
                for packet in self.recv_json():
                    if packet=={}:self.socket.send('{}')
                    self.da_publication(packet)
                received=time.monotonic()
            except websocket.WebSocketTimeoutException:pass
            if time.monotonic()-ping>20:
                self.socket.send(json.dumps({'id':3,'method':7,'params':{}}));ping=time.monotonic()
            if time.monotonic()-received>65:raise RuntimeError('Соединение устарело.')

    def connect_dx(self):
        profile=self.request('GET','v1/user/me')
        self.account=str(profile['id'])
        negotiation=self.request('POST','public-donations-hub/negotiate',params={'negotiateVersion':1})
        if not any(t.get('transport')=='WebSockets' for t in negotiation.get('availableTransports',[])):
            raise RuntimeError('DonateX не предложил WebSocket.')
        query=urlencode({'id':negotiation['connectionToken']})
        self.socket=websocket.create_connection('wss://donatex.gg/api/public-donations-hub?'+query,
            header={'Authorization':'Bearer '+self.token},timeout=12,origin='https://donatex.gg')
        self.socket.settimeout(1)
        self.socket.send('{"protocol":"json","version":1}\u001e')
        buffer='';handshake=False;ping=time.monotonic();received=ping
        while not self.stopping.is_set():
            try:
                raw=self.socket.recv()
                if not raw:raise RuntimeError('Соединение закрыто.')
                buffer+=raw
                if len(buffer)>1024*1024:raise ValueError('Слишком длинное событие.')
                while '\u001e' in buffer:
                    part,buffer=buffer.split('\u001e',1)
                    if not part:continue
                    packet=json.loads(part)
                    if not handshake:
                        if packet.get('error'):raise AuthError('DonateX отклонил вход. Проверь токен.')
                        handshake=True;self.ready(profile.get('username'))
                    elif packet.get('type')==1 and packet.get('target') in ('DonationCreated','DonationRerun'):
                        arguments=packet.get('arguments') or []
                        if arguments:self.publish(arguments[0])
                    elif packet.get('type')==7:raise RuntimeError('DonateX закрыл соединение.')
                received=time.monotonic()
            except websocket.WebSocketTimeoutException:pass
            if not handshake and time.monotonic()-received>20:raise AuthError('DonateX не подтвердил вход.')
            if time.monotonic()-ping>15:
                self.socket.send('{"type":6}\u001e');ping=time.monotonic()
            if time.monotonic()-received>60:raise RuntimeError('Соединение устарело.')
