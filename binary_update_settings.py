"""User-facing binary update controls; all network work stays off the Qt thread."""
import json
import shutil
import sys
import uuid
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QGroupBox,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QLineEdit,QMessageBox,QProgressBar
from core import save_config
from release_version import VERSION,UPDATE_URL
from binary_updates import UpdateError,https_url,version_tuple,read_manifest,download_package,stage_package,launch_installer


class BinaryUpdateSettings(QGroupBox):
    def __init__(self,owner,data):
        super().__init__('ОБНОВЛЕНИЯ ПРИЛОЖЕНИЯ')
        self.owner,self.data=owner,Path(data);self.manifest=None;self.busy=False;self.generation=0
        self.url=UPDATE_URL or str(owner.cfg.get('update_manifest_url') or '')
        self.frozen=bool(getattr(sys,'frozen',False) and sys.platform=='win32')
        layout=QVBoxLayout(self);layout.addWidget(QLabel('Установленная версия: '+VERSION))
        self.status=QLabel('');self.status.setWordWrap(True);self.status.setTextFormat(Qt.PlainText);layout.addWidget(self.status)
        self.channel=QLineEdit(self.url);self.channel.setPlaceholderText('HTTPS-адрес latest.json · задаёт автор программы')
        self.channel.setVisible(not bool(UPDATE_URL));self.channel.editingFinished.connect(self.save_channel);layout.addWidget(self.channel)
        buttons=QHBoxLayout();self.check_button=QPushButton('Проверить обновления');self.check_button.clicked.connect(lambda:self.check(False));buttons.addWidget(self.check_button)
        self.install_button=QPushButton('Скачать и обновить');self.install_button.setEnabled(False);self.install_button.clicked.connect(self.install);buttons.addWidget(self.install_button);layout.addLayout(buttons)
        self.progress_bar=QProgressBar();self.progress_bar.setRange(0,100);self.progress_bar.hide();layout.addWidget(self.progress_bar)
        if not self.frozen:self.status.setText('Запущены исходники. Для них доступен маленький установщик; обновление EXE работает в готовой Windows-сборке.')
        elif not self.url:self.status.setText('Автор ещё не настроил канал обновлений.')
        else:self.status.setText('Можно проверить наличие новой версии.')
        result=self.data/'updates/last-result.json'
        if result.is_file():
            try:
                previous=json.loads(result.read_text(encoding='utf-8-sig'))
                if previous.get('status')=='failed':self.status.setText('Предыдущее обновление не установилось. Подробности: '+str(result))
            except (ValueError,OSError):pass
        if self.frozen and self.url and not getattr(owner,'smoke_test',False):QTimer.singleShot(1800,lambda:self.check(True))

    def save_channel(self):
        if self.busy:return
        value=self.channel.text().strip()
        if value:
            try:https_url(value)
            except UpdateError as error:self.status.setText(str(error));return
        self.url=value;self.owner.cfg['update_manifest_url']=value;save_config(self.data,self.owner.cfg)
        self.manifest=None;self.install_button.setEnabled(False)

    def set_busy(self,value):
        self.busy=value;self.check_button.setEnabled(not value);self.channel.setEnabled(not value)
        self.install_button.setEnabled(not value and self.frozen and self.manifest is not None)

    def check(self,background=False):
        if self.owner.closing or self.busy:return
        if not self.url:self.status.setText('Канал обновлений ещё не настроен автором.');return
        self.set_busy(True);self.status.setText('Проверяем новую версию…')
        def done(manifest):
            if self.owner.closing:return
            self.manifest=manifest if version_tuple(manifest['version'])>version_tuple(VERSION) else None
            self.set_busy(False)
            if not self.manifest:self.status.setText('Установлена актуальная версия.');return
            self.status.setText('Доступна версия '+manifest['version']+'. '+manifest.get('notes',''))
            if background and self.frozen and not self.owner.current:
                answer=QMessageBox.question(self,'Обновление TwitchTune','Доступна версия '+manifest['version']+'. Скачать, установить и перезапустить приложение?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
                if answer==QMessageBox.Yes:self.install()
        def failed(error):
            if self.owner.closing:return
            self.set_busy(False);self.status.setText(str(error) or 'Не удалось проверить обновления.')
        self.owner.run_async(lambda:read_manifest(self.url,cancelled=lambda:self.owner.closing),done,failed)

    def progress(self,generation,downloaded,total):
        if generation==self.generation and not self.owner.closing:self.progress_bar.setValue(min(100,int(downloaded*100/max(1,total))))

    def install(self):
        if self.owner.closing or self.busy or not self.manifest or not self.frozen:return
        self.set_busy(True);self.generation+=1;generation=self.generation;manifest=dict(self.manifest)
        self.progress_bar.setValue(0);self.progress_bar.show();self.status.setText('Скачиваем и проверяем обновление…')
        job=self.data/'updates'/uuid.uuid4().hex;target=Path(sys.executable).resolve().parent
        def work():
            archive=download_package(manifest,job,cancelled=lambda:self.owner.closing,
                                     progress=lambda n,total:self.owner.incoming.emit('binary_update_progress',(generation,n,total)))
            stage=stage_package(archive,target,manifest['version'],cancelled=lambda:self.owner.closing)
            try:
                if self.owner.closing:raise UpdateError('Обновление отменено.')
                launch_installer(stage,target,self.data,manifest['version'],job)
                return stage
            except Exception:shutil.rmtree(stage,ignore_errors=True);raise
        def done(stage):
            if self.owner.closing:return
            self.status.setText('Завершаем работу для установки обновления…')
            self.owner.exit_application()
        def failed(error):
            if self.owner.closing:return
            if (job/'installer.log').is_file():(job/'package.zip').unlink(missing_ok=True)
            else:shutil.rmtree(job,ignore_errors=True)
            self.set_busy(False);self.progress_bar.hide()
            self.status.setText(str(error) or 'Не удалось подготовить обновление. Текущая версия сохранена.')
        self.owner.run_async(work,done,failed)
