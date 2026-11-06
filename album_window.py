"""Album folders and their ordered contents; one review per whole album."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QListWidget, QListWidgetItem, QStackedWidget)


class AlbumsPanel(QWidget):
    def __init__(self, store, import_album, play, retry, review, delete):
        super().__init__()
        self.store, self.album_id = store, None
        self.review = review
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.info = QLabel('Альбом — одна заявка в очереди и одна общая оценка.\nЯндекс / Spotify / SoundCloud sets / плейлисты YouTube.')
        self.info.setObjectName('muted'); self.info.setWordWrap(True)
        layout.addWidget(self.info)
        top = QHBoxLayout()
        self.url = QLineEdit(); self.url.setPlaceholderText('Ссылка на альбом…')
        self.import_button = QPushButton('Скачать альбом')
        self.import_button.setObjectName('primary')
        self.import_button.clicked.connect(lambda: import_album(self.url.text()))
        self.url.returnPressed.connect(lambda: import_album(self.url.text()))
        top.addWidget(self.url, 1); top.addWidget(self.import_button)
        layout.addLayout(top)
        self.stack = QStackedWidget(); layout.addWidget(self.stack, 1)
        self.list = QListWidget(); self.list.setWordWrap(True)
        self.list.setIconSize(QSize(76,76))
        self.list.setSpacing(5)
        self.list.itemClicked.connect(lambda item: self.open_album(item.data(Qt.UserRole)))
        self.stack.addWidget(self.list)
        details = QWidget(); detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        back = QPushButton('← Альбомы'); back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        header.addWidget(back)
        self.cover = QLabel(); self.cover.setFixedSize(64,64); self.cover.setAlignment(Qt.AlignCenter)
        header.addWidget(self.cover)
        self.heading = QLabel(); self.heading.setWordWrap(True); self.heading.setObjectName('section')
        header.addWidget(self.heading, 1); detail_layout.addLayout(header)
        self.viewer = QLabel(); self.viewer.setObjectName('muted'); self.viewer.setTextFormat(Qt.PlainText)
        detail_layout.addWidget(self.viewer)
        self.tracks = QListWidget(); self.tracks.setWordWrap(True)
        self.tracks.itemDoubleClicked.connect(lambda item: play(self.album_id, item.data(Qt.UserRole)))
        detail_layout.addWidget(self.tracks, 1)
        actions = QHBoxLayout()
        listen = QPushButton('Слушать альбом'); listen.clicked.connect(lambda: play(self.album_id, 0))
        again = QPushButton('Повторить загрузки'); again.clicked.connect(lambda: retry(self.album_id))
        folder = QPushButton('Папка'); folder.clicked.connect(self.open_folder)
        actions.addWidget(listen); actions.addWidget(again); actions.addWidget(folder)
        detail_layout.addLayout(actions)
        hint = QLabel('Двойной щелчок — начать с выбранного трека. Оценка справа относится ко всему альбому.')
        hint.setObjectName('micro'); hint.setWordWrap(True)
        footer=QHBoxLayout();footer.addWidget(hint,1)
        self.delete_button=QPushButton('Удалить альбом')
        self.delete_button.clicked.connect(lambda:delete(self.album_id))
        footer.addWidget(self.delete_button)
        detail_layout.addLayout(footer)
        self.stack.addWidget(details)

    def open_album(self, ident):
        self.album_id = ident
        self.refresh()
        self.stack.setCurrentIndex(1)
        self.review(ident)

    def refresh(self):
        self.list.clear()
        for album in self.store.albums():
            parent = self.store.album_request(album['id'])
            state = parent['status'] if parent else ''
            viewer=parent['viewer'] if parent else 'Вручную'
            item = QListWidgetItem(f"{album['title']}\n{album['artist']} · {album['year']} · {album['source']}\nОт: {viewer}\nСкачано {album['downloaded'] or 0}/{album['tracks']} · {state}")
            item.setIcon(QIcon(self.cover_file(parent)))
            item.setSizeHint(QSize(0,110))
            item.setData(Qt.UserRole, album['id']); item.setToolTip(album['folder'])
            self.list.addItem(item)
        if not self.list.count():
            item = QListWidgetItem('АЛЬБОМОВ ПОКА НЕТ\nДобавь ссылку сверху — альбом скачается в отдельную папку.')
            item.setFlags(Qt.NoItemFlags); self.list.addItem(item)
        selected = self.tracks.currentRow()
        self.tracks.clear()
        if self.album_id:
            album = next((a for a in self.store.albums() if a['id'] == self.album_id), None)
            if not album:
                self.album_id = None; self.stack.setCurrentIndex(0); return
            self.heading.setTextFormat(Qt.PlainText)
            self.heading.setText(album['artist'] + ' — ' + album['title'])
            parent=self.store.album_request(self.album_id)
            self.viewer.setText('От: '+(parent['viewer'] if parent else 'Вручную'))
            self.cover.setPixmap(QIcon(self.cover_file(parent)).pixmap(QSize(64,64)))
            for index, row in enumerate(self.store.album_tracks(self.album_id)):
                seconds = int(row['duration'] or 0)
                item = QListWidgetItem(f"{index+1:02}  {row['title']}\n{seconds//60}:{seconds%60:02} · {row['status']}" + (' · Замена: YouTube' if row['source']=='Яндекс Музыка' and row['replacement'] else '') + ('\n' + row['error'] if row['error'] else ''))
                item.setData(Qt.UserRole, index); self.tracks.addItem(item)
            self.tracks.setCurrentRow(selected)

    @staticmethod
    def cover_file(parent):
        path=parent.get('cover_path') if parent else ''
        if path and Path(path).is_file():return path
        return str(Path(__file__).resolve().parent/'assets'/'twitchtune.ico')

    def open_folder(self):
        album = next((a for a in self.store.albums() if a['id'] == self.album_id), None)
        if album:
            QDesktopServices.openUrl(QUrl.fromLocalFile(album['folder']))
