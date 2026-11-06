"""Settings, persisted inbox and generation-checked donation event routing."""
import threading
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QGroupBox,QCheckBox,
                              QDialog,QTreeWidget,QTreeWidgetItem,QDialogButtonBox,QHeaderView,QSpinBox,QPlainTextEdit)
from core import save_config
from albums import album_reference,catalog
from donation_clients import DonationClient
from donation_auth import load_token,save_token,forget_token,authorize,REDIRECT
from donations import priced_message_checked,allocate_orders,track_price,PROVIDERS


class DonationHistoryDialog(QDialog):
    def __init__(self,records,account,parent=None,price=200):
        super().__init__(parent)
        self.setWindowTitle('DonationAlerts / сообщения из кабинета');self.resize(960,580)
        layout=QVBoxLayout(self)
        hint=QLabel('Отметь заказы, которые нужно добавить. Дата сообщения может быть любой.\n'
                    'Показаны все сообщения API, включая пропущенные. Выбери строку, чтобы увидеть подробности.')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.list=QTreeWidget();self.list.setRootIsDecorated(False)
        self.list.setHeaderLabels(['Добавить / зритель','Сумма','Дата','Результат проверки','Сообщение / ссылки'])
        self.events=[]
        for raw in records:
            event,reason=priced_message_checked('donationalerts',account,raw,price)
            eligible=event is not None and event['paid_slots']>0
            status=f"Можно добавить · лимит песен: {event['paid_slots']}" if eligible else reason
            message=str(raw.get('message') or '')
            item=QTreeWidgetItem([str(raw.get('username') or 'Аноним'),str(raw.get('amount'))+' '+str(raw.get('currency') or ''),
                                 str(raw.get('created_at') or ''),status,message])
            details='Результат: '+status+'\nСообщение: '+(message or '(поле message пустое)')
            fields=('id','name','message_type','amount','currency','is_test','isTest','is_potentially_unsafe','isPotentiallyUnsafe')
            details+='\nПоля API: '+', '.join(k+'='+repr(raw[k])[:120] for k in fields if k in raw)
            if not message and isinstance(raw.get('content'),str):details+='\nПоле content: '+raw['content'][:10000]
            item.setData(0,Qt.UserRole,details)
            for col in (3,4):item.setToolTip(col,details)
            if eligible:
                item.setCheckState(0,Qt.Unchecked);self.events.append((item,event))
            self.list.addTopLevelItem(item)
        for col in range(3):self.list.header().setSectionResizeMode(col,QHeaderView.ResizeToContents)
        self.list.setColumnWidth(3,230)
        self.list.header().setStretchLastSection(True);layout.addWidget(self.list)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(110)
        self.details.setPlaceholderText('Выбери сообщение: здесь будут полный текст и причина пропуска. Текст можно скопировать.')
        self.list.currentItemChanged.connect(lambda item,_previous:self.details.setPlainText(item.data(0,Qt.UserRole) if item else ''))
        layout.addWidget(self.details)
        label=QLabel(f'Сервис вернул сообщений: {len(records)} · со ссылками и суммой от {price} ₽: {len(self.events)}.')
        label.setWordWrap(True);layout.addWidget(label)
        empty=QLabel('Флажки доступны у подходящих заказов. Пропущенные строки остаются видимыми с причиной.\n'
                     'Действует текущая цена; уже обработанные заказы повторно не добавляются.')
        empty.setWordWrap(True);empty.setObjectName('micro');layout.addWidget(empty)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Добавить выбранные')
        buttons.button(QDialogButtonBox.Cancel).setText('Закрыть')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def selected_events(self):
        return [event for item,event in self.events if item.checkState(0)==Qt.Checked]


class DonationSettings(QWidget):
    def __init__(self,owner,folder):
        super().__init__()
        self.owner,self.folder=owner,folder
        self.active,self.clients={},[]
        self.auth_stop=threading.Event()
        self.status_labels={}
        self.resolving=set()
        self.reported_skips=set()
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        title=QLabel('ДОНАТЫ / ЗАКАЗЫ БЕЗ ОЧЕРЕДИ');title.setObjectName('section');layout.addWidget(title)
        info=QLabel('Ссылка в донате → загрузка → перед VIP и обычными заявками.\nИграющий трек не прерывается; несколько донатов идут в порядке поступления.')
        info.setWordWrap(True);info.setObjectName('muted');layout.addWidget(info)
        pricing=QGroupBox('Расценки на донатные треки');pl=QVBoxLayout(pricing)
        line=QHBoxLayout();line.addWidget(QLabel('Один трек'))
        self.price=QSpinBox();self.price.setRange(1,1000000);self.price.setSuffix(' ₽')
        self.price.setValue(track_price(owner.cfg.get('donation_track_price',200)));line.addWidget(self.price)
        pl.addLayout(line);self.price_examples=QLabel();self.price_examples.setWordWrap(True);pl.addWidget(self.price_examples)
        hint=QLabel('Одна цена для DonationAlerts и DonateX, валюта RUB. Ниже минимума — пропуск.\n'
                    'Несколько ссылок: первые оплаченные треки. Альбом оплачивается по числу песен и принимается целиком.')
        hint.setWordWrap(True);hint.setObjectName('micro');pl.addWidget(hint);layout.addWidget(pricing)
        self.price.valueChanged.connect(self.save_price);self.update_price_examples()
        da=QGroupBox('DonationAlerts');dl=QVBoxLayout(da)
        self.da_id=QLineEdit(str(owner.cfg.get('donation_da_client_id','')));self.da_id.setPlaceholderText('Client ID приложения DonationAlerts')
        dl.addWidget(self.da_id)
        hint=QLabel('Создай приложение в DonationAlerts → API. Redirect URI:\n'+REDIRECT+'\nClient Secret не требуется. Вход откроется в браузере.')
        hint.setWordWrap(True);hint.setTextInteractionFlags(Qt.TextSelectableByMouse);hint.setObjectName('micro');dl.addWidget(hint)
        buttons=QHBoxLayout();self.da_login=QPushButton('Войти в DonationAlerts');self.da_login.clicked.connect(self.login_da);buttons.addWidget(self.da_login)
        stop=QPushButton('Отключить');stop.clicked.connect(lambda:self.disconnect('donationalerts'));buttons.addWidget(stop);dl.addLayout(buttons)
        label=QLabel('Не подключено');label.setTextFormat(Qt.PlainText);label.setWordWrap(True);dl.addWidget(label);self.status_labels['donationalerts']=label
        self.da_history_button=QPushButton('Сообщения из кабинета…');self.da_history_button.clicked.connect(self.show_da_history)
        dl.addWidget(self.da_history_button)
        self.da_history_status=QLabel('После подключения новые сообщения проверяются каждые 10 секунд.')
        self.da_history_status.setWordWrap(True);self.da_history_status.setTextFormat(Qt.PlainText)
        self.da_history_status.setObjectName('micro');dl.addWidget(self.da_history_status)
        layout.addWidget(da)
        dx=QGroupBox('DonateX');xl=QVBoxLayout(dx)
        self.dx_token=QLineEdit();self.dx_token.setEchoMode(QLineEdit.Password);self.dx_token.setPlaceholderText('External token · кабинет DonateX → Настройки → API и доступ')
        xl.addWidget(self.dx_token)
        buttons=QHBoxLayout();connect=QPushButton('Подключить DonateX');connect.clicked.connect(self.connect_dx);buttons.addWidget(connect)
        stop=QPushButton('Отключить');stop.clicked.connect(lambda:self.disconnect('donatex'));buttons.addWidget(stop);xl.addLayout(buttons)
        label=QLabel('Не подключено');label.setTextFormat(Qt.PlainText);label.setWordWrap(True);xl.addWidget(label);self.status_labels['donatex']=label
        layout.addWidget(dx)
        self.tests=QCheckBox('Принимать тестовые донаты DonateX');self.tests.setChecked(bool(owner.cfg.get('donation_tests',False)))
        self.tests.toggled.connect(self.save_tests);layout.addWidget(self.tests)
        self.pending_label=QLabel();self.pending_label.setObjectName('micro');layout.addWidget(self.pending_label)
        self.timer=QTimer(self);self.timer.setInterval(2000);self.timer.timeout.connect(self.process_pending);self.timer.start()

    def save_tests(self,value):
        self.owner.cfg['donation_tests']=value;save_config(self.folder,self.owner.cfg)

    def update_price_examples(self):
        price=self.price.value()
        self.price_examples.setText(f'1 трек — {price} ₽ · 2 — {price*2} ₽ · 3 — {price*3} ₽ · 5 — {price*5} ₽')

    def save_price(self,value):
        self.owner.cfg['donation_track_price']=value;save_config(self.folder,self.owner.cfg)
        self.update_price_examples()

    def start(self,provider,token):
        old=self.active.pop(provider,None)
        if old:old.close()
        client=DonationClient(provider,token,lambda kind,data:self.owner.incoming.emit('paid_service',(client,kind,data)))
        self.active[provider]=client;self.clients.append(client)
        self.owner.cfg['donation_'+provider+'_enabled']=True;save_config(self.folder,self.owner.cfg)
        client.start()

    def autostart(self):
        for provider in PROVIDERS:
            if not self.owner.cfg.get('donation_'+provider+'_enabled'):continue
            token=load_token(provider,self.da_id.text().strip())
            if token:self.start(provider,token)
            else:self.status_labels[provider].setText('Подключи заново: сохранённый вход не найден.')

    def login_da(self):
        ident=self.da_id.text().strip()
        if not ident.isdigit():self.status_labels['donationalerts'].setText('Укажи числовой Client ID приложения DonationAlerts.');return
        self.auth_stop.clear();self.da_login.setEnabled(False)
        self.status_labels['donationalerts'].setText('Ожидаем вход в браузере…')
        self.owner.cfg['donation_da_client_id']=ident;save_config(self.folder,self.owner.cfg)
        def done(token):
            self.da_login.setEnabled(True)
            if self.auth_stop.is_set() or self.owner.closing:return
            if not save_token('donationalerts',token,ident):self.owner.log.appendPlainText('DonationAlerts: вход работает до закрытия; хранилище Windows недоступно.')
            self.start('donationalerts',token)
        def failed(error):
            self.da_login.setEnabled(True)
            if not self.owner.closing:self.status_labels['donationalerts'].setText(str(error))
        self.owner.run_async(lambda:authorize(ident,cancelled=self.auth_stop.is_set),done,failed)

    def connect_dx(self):
        token=self.dx_token.text().strip() or load_token('donatex')
        if not token or any(c.isspace() for c in token) or len(token)>8192:
            self.status_labels['donatex'].setText('Вставь корректный External token из кабинета DonateX.');return
        if not save_token('donatex',token):self.owner.log.appendPlainText('DonateX: вход работает до закрытия; хранилище Windows недоступно.')
        self.dx_token.clear();self.start('donatex',token)

    def disconnect(self,provider):
        client=self.active.pop(provider,None)
        if client:client.close()
        if provider=='donationalerts':self.auth_stop.set()
        forget_token(provider,self.da_id.text().strip())
        self.owner.cfg['donation_'+provider+'_enabled']=False;save_config(self.folder,self.owner.cfg)
        self.status_labels[provider].setText('Отключено')

    def show_da_history(self):
        client=self.active.get('donationalerts')
        if not client or not client.account:
            self.da_history_status.setText('Сначала подключи DonationAlerts и дождись входа в аккаунт.');return
        account=client.account
        self.da_history_button.setEnabled(False);self.da_history_status.setText('Читаем сообщения из API DonationAlerts…')
        def current():return not self.owner.closing and self.active.get('donationalerts') is client
        def done(records):
            self.da_history_button.setEnabled(True)
            if not current():return
            self.da_history_status.setText(f'Из API получено сообщений: {len(records)}. Выбери нужные заказы в списке.')
            dialog=DonationHistoryDialog(records,account,self,price=self.price.value())
            if dialog.exec()!=QDialog.Accepted or not current():return
            self.import_da_history(client,dialog.selected_events())
        def failed(_error):
            self.da_history_button.setEnabled(True)
            if current():self.da_history_status.setText('Не удалось прочитать историю. Проверь подключение и войди заново с правом чтения донатов.')
        self.owner.run_async(client.fetch_da_history,done,failed)

    def import_da_history(self,client,events):
        if self.owner.closing or self.active.get('donationalerts') is not client:return
        added=sum(bool(self.owner.store.record_donation(event)) for event in events)
        self.process_pending()
        message=f'Принято заказов: {added} · уже обработано: {len(events)-added}.'
        self.da_history_status.setText(message);self.owner.log.appendPlainText('DonationAlerts: '+message)

    def handle(self,client,kind,data):
        if self.owner.closing or self.active.get(client.provider) is not client:return
        if kind=='status':self.status_labels[client.provider].setText(str(data))
        elif kind=='history_status':self.da_history_status.setText(str(data))
        elif kind=='warning':self.owner.log.appendPlainText(PROVIDERS[client.provider]+': '+str(data))
        elif kind=='donation':
            account,raw=data
            event,reason=priced_message_checked(client.provider,account,raw,self.price.value(),self.tests.isChecked())
            if event is None and reason:
                key=(client.provider,str(account),str(raw.get('id')) if isinstance(raw,dict) else '',reason)
                if key not in self.reported_skips:
                    if len(self.reported_skips)>=1000:self.reported_skips.clear()
                    self.reported_skips.add(key)
                    self.owner.log.appendPlainText(PROVIDERS[client.provider]+': сообщение пропущено. '+reason)
            if event and self.owner.store.record_donation(event):self.process_pending()

    def process_pending(self):
        if self.owner.closing:return
        store=self.owner.store
        for event in store.pending_donations():
            key=event['event_key']
            if 'order_urls' in event:
                if key in self.resolving:continue
                if not event['paid_slots']:
                    if store.expand_donation(key,[]):self.owner.log.appendPlainText(PROVIDERS[event['provider']]+': заказ пропущен. '+event['pricing_note'])
                    continue
                def done(orders,event=event,key=key):
                    self.resolving.discard(key)
                    if self.owner.closing:return
                    if store.expand_donation(key,orders):
                        count=sum(o['paid_tracks'] for o in orders)
                        note=' Не хватило оплаты на все ссылки; альбомы принимаются только целиком.' if len(orders)<len(event['order_urls']) else ''
                        self.owner.log.appendPlainText(f"{PROVIDERS[event['provider']]}: принято {count} треков, цена {event['unit_price']} ₽/трек."+note)
                    self.process_pending()
                def failed(_error,key=key):
                    self.resolving.discard(key)
                    if not self.owner.closing:
                        store.donation_retry(key,'Не удалось проверить состав альбома. Повторим через минуту.')
                        self.owner.log.appendPlainText('Донат: не удалось проверить цену альбома. Повторим через минуту.')
                if any(album_reference(url) for url in event['order_urls']):
                    self.resolving.add(key);settings=dict(self.owner.cfg)
                    self.owner.run_async(lambda event=event,settings=settings:allocate_orders(event,lambda url:catalog(url,cancelled=lambda:self.owner.closing,settings=settings)),done,failed)
                else:
                    done(allocate_orders(event))
                    return
                continue
            if key in getattr(self.owner,'pending_albums',set()):continue
            existing=store.get_redemption(key)
            if existing:
                store.donation_added(key,existing['id']);continue
            if len(store.rows('active'))>=100:break
            try:
                if album_reference(event['url']):
                    self.owner.import_album(event['url'],dict(viewer=event['viewer'],redemption=key,donation=event),metadata=event.get('album_metadata'))
                else:
                    ident=store.add(event['url'],event['viewer'],redemption=key,donation=event)
                    store.donation_added(key,ident)
                    if ident:self.owner.submit_request(ident);self.owner.refresh()
            except Exception:
                store.donation_retry(key,'Заказ пока не добавлен. Повторим через минуту.')
                self.owner.log.appendPlainText('Донатный заказ сохранён и будет добавлен повторно.')
        count=store.donation_pending_count()
        self.pending_label.setText(f'Донатов ждёт добавления: {count}' if count else 'Новые заказы добавляются автоматически. Токены хранятся в хранилище Windows.')

    def close_clients(self):
        self.timer.stop();self.auth_stop.set()
        for client in self.clients:client.close()

    def alive(self):return any(c.alive() for c in self.clients)
