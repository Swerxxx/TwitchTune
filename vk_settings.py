"""VK Music session settings, using the app's existing worker ownership."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QComboBox,QLineEdit,QPushButton,QWidget,QFileDialog
from core import save_config
from vk_session import check_vk_session


class VKSettings(QGroupBox):
    def __init__(self,owner,app_folder):
        super().__init__('VK МУЗЫКА / СВОЙ АККАУНТ')
        self.owner,self.app_folder=owner,app_folder
        self.generation=0
        layout=QVBoxLayout(self)
        hint=QLabel('Войди на vk.ru в браузере, в котором воспроизводится нужная песня. Здесь подключается этот вход; пароль в TwitchTune вводить не нужно.')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.source=QComboBox()
        for caption,value in [('Без подключения',''),('Firefox','firefox'),('Chrome','chrome'),('Edge','edge'),('Файл cookies.txt','cookies')]:self.source.addItem(caption,value)
        self.source.setCurrentIndex(max(0,self.source.findData(owner.cfg.get('vk_auth',''))))
        layout.addWidget(self.source)
        self.profile_row=QWidget();profile=QHBoxLayout(self.profile_row);profile.setContentsMargins(0,0,0,0)
        self.profile=QLineEdit();self.profile.setReadOnly(True);self.profile.setPlaceholderText('Определить профиль автоматически')
        profile.addWidget(self.profile,1)
        self.add_button(profile,'Папка профиля',self.choose_profile)
        self.add_button(profile,'Авто',lambda:self.set_profile(''))
        layout.addWidget(self.profile_row)
        self.cookie_row=QWidget();cookies=QHBoxLayout(self.cookie_row);cookies.setContentsMargins(0,0,0,0)
        self.cookie_file=QLineEdit(owner.cfg.get('vk_cookie_file',''));self.cookie_file.setReadOnly(True);self.cookie_file.setPlaceholderText('Локальный cookies.txt в формате Netscape')
        cookies.addWidget(self.cookie_file,1);self.add_button(cookies,'Выбрать файл',self.choose_cookies)
        layout.addWidget(self.cookie_row)
        note=QLabel('Firefox: about:profiles → Корневой каталог. Chrome: chrome://version → Путь к профилю. Если Chrome/Edge не дают прочитать сессию, попробуй Firefox. После подключения выдели заявку и нажми «Повторить».')
        note.setObjectName('muted');note.setWordWrap(True);layout.addWidget(note)
        buttons=QHBoxLayout()
        self.check=self.add_button(buttons,'Проверить вход',self.check_session)
        self.add_button(buttons,'Отключить',self.disconnect)
        buttons.addStretch();layout.addLayout(buttons)
        self.info=QLabel('Подключается только сессия VK. Доступ к каждой песне проверяется при загрузке.')
        self.info.setObjectName('muted');self.info.setWordWrap(True);self.info.setTextFormat(Qt.PlainText)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse);layout.addWidget(self.info)
        self.source.currentIndexChanged.connect(self.source_changed)
        self.refresh_fields()
    def add_button(self,layout,caption,action):
        button=QPushButton(caption);button.clicked.connect(action);layout.addWidget(button);return button
    def persist(self):
        save_config(self.app_folder,self.owner.cfg)
        self.generation+=1
    def refresh_fields(self):
        mode=self.source.currentData()
        self.profile_row.setVisible(mode in ('firefox','chrome','edge'))
        self.cookie_row.setVisible(mode=='cookies')
        profiles=self.owner.cfg.get('vk_profiles') or {}
        self.profile.setText(profiles.get(mode,'') if isinstance(profiles,dict) else '')
        self.check.setEnabled(bool(mode))
    def source_changed(self,*_):
        self.owner.cfg['vk_auth']=self.source.currentData();self.persist();self.refresh_fields()
        self.info.setText('Источник входа сохранён. Нажми «Проверить вход».')
    def choose_profile(self):
        path=QFileDialog.getExistingDirectory(self,'Папка профиля с входом VK',self.profile.text())
        if path:self.set_profile(path)
    def set_profile(self,path):
        profiles=self.owner.cfg.get('vk_profiles')
        if not isinstance(profiles,dict):profiles={}
        profiles[self.source.currentData()]=path
        self.owner.cfg['vk_profiles']=profiles;self.persist();self.refresh_fields()
        self.info.setText('Профиль сохранён. Нажми «Проверить вход».')
    def choose_cookies(self):
        path,_=QFileDialog.getOpenFileName(self,'Файл сессии VK','','Cookies (*.txt);;Все файлы (*)')
        if path:
            self.owner.cfg['vk_cookie_file']=path;self.cookie_file.setText(path);self.persist()
            self.info.setText('Файл выбран. Нажми «Проверить вход».')
    def disconnect(self):
        self.source.setCurrentIndex(0)
        self.owner.cfg['vk_auth']=''
        self.owner.cfg.pop('vk_cookie_file',None);self.cookie_file.clear();self.persist()
        self.info.setText('Вход отключён для следующих загрузок. Вход в самом браузере сохранён.')
    def check_session(self):
        if not self.source.currentData():return
        generation=self.generation
        settings=dict(self.owner.cfg)
        profiles=settings.get('vk_profiles')
        settings['vk_profiles']=dict(profiles) if isinstance(profiles,dict) else {}
        self.check.setEnabled(False);self.info.setText('Проверяю вход VK…')
        def finish(message):
            if self.owner.closing or generation!=self.generation:return
            self.check.setEnabled(bool(self.source.currentData()));self.info.setText(str(message))
        self.owner.run_async(lambda:check_vk_session(settings),finish,finish)
