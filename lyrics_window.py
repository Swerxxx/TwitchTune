"""Nonmodal lyrics reader; background work is owned by Main, not the popup."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QTextBlockFormat
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QComboBox, QTextEdit, QFrame, QWidget)
from lyrics import song_query, plain_text, exact_match


class LyricsDialog(QDialog):
    def __init__(self, row, client, parent, results=None):
        super().__init__(parent)
        self.row, self.client, self.owner = row, client, parent
        self.closed, self.generation = False, 0
        self.setWindowTitle('Текст песни · ' + row['title'])
        screen = self.screen().availableGeometry()
        self.resize(min(720, max(430, screen.width() - 60)), min(820, max(440, screen.height() - 80)))
        self.setMinimumSize(430, 440)
        self.setWindowModality(Qt.NonModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(14)
        title = QLabel('ACID DECK  /  ТЕКСТ ПЕСНИ')
        title.setObjectName('section')
        layout.addWidget(title)
        artist, track = song_query(row)
        self.track_label = QLabel(track or row['title'])
        self.track_label.setTextFormat(Qt.PlainText)
        self.track_label.setObjectName('lyricsTitle')
        self.track_label.setWordWrap(True)
        layout.addWidget(self.track_label)
        self.artist_label = QLabel(artist or 'Неизвестный исполнитель')
        self.artist_label.setTextFormat(Qt.PlainText)
        self.artist_label.setObjectName('muted')
        layout.addWidget(self.artist_label)
        toolbar = QHBoxLayout()
        self.refine = QPushButton('Уточнить поиск')
        self.refine.setCheckable(True)
        toolbar.addWidget(self.refine)
        self.description_button=QPushButton('Описание SoundCloud')
        self.description_button.setVisible(row.get('source')=='SoundCloud')
        self.description_button.clicked.connect(self.show_soundcloud_description)
        toolbar.addWidget(self.description_button)
        toolbar.addStretch()
        self.font_size = 15
        for caption, step in [('А−', -1), ('А+', 1)]:
            button = QPushButton(caption)
            button.setFixedWidth(46)
            button.clicked.connect(lambda checked=False, delta=step: self.resize_text(delta))
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
        self.search_panel = QWidget()
        panel_layout = QVBoxLayout(self.search_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        inputs = QHBoxLayout()
        self.artist = QLineEdit(artist)
        self.artist.setPlaceholderText('Исполнитель')
        self.track = QLineEdit(track)
        self.track.setPlaceholderText('Название песни')
        inputs.addWidget(self.artist)
        inputs.addWidget(self.track, 1)
        self.search_button = QPushButton('Найти')
        self.search_button.clicked.connect(self.search)
        self.artist.returnPressed.connect(self.search)
        self.track.returnPressed.connect(self.search)
        inputs.addWidget(self.search_button)
        panel_layout.addLayout(inputs)
        layout.addWidget(self.search_panel)
        self.refine.toggled.connect(self.search_panel.setVisible)
        self.search_panel.hide()
        self.info = QLabel('')
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.PlainText)
        self.info.setObjectName('muted')
        layout.addWidget(self.info)
        self.versions = QComboBox()
        self.versions.setPlaceholderText('Выбери подходящую запись')
        self.versions.currentIndexChanged.connect(self.show_version)
        layout.addWidget(self.versions)
        reader = QFrame()
        reader.setObjectName('lyricsPaper')
        reader_layout = QVBoxLayout(reader)
        reader_layout.setContentsMargins(14, 14, 14, 14)
        self.body = QTextEdit()
        self.body.setObjectName('lyricsBody')
        self.body.setReadOnly(True)
        self.body.setAcceptRichText(False)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.resize_text(0)
        self.body.setPlaceholderText('Здесь появится найденный текст.')
        reader_layout.addWidget(self.body)
        layout.addWidget(reader, 1)
        footer = QLabel('Источники: LRCLIB · Lyrics.ovh · Genius · Яндекс Музыка')
        self.source_label = footer
        footer.setObjectName('micro')
        footer.setWordWrap(True)
        layout.addWidget(footer)
        self.finished.connect(self.finish_reader)
        if results is None:
            self.search()
        else:
            self.show_results(results)

    def resize_text(self, delta):
        self.font_size = max(11, min(24, self.font_size + delta))
        self.body.setStyleSheet(f'QTextEdit#lyricsBody {{ font: {self.font_size}pt "Segoe UI"; }}')

    def set_lyrics(self, text):
        self.body.setPlainText(text)
        cursor = self.body.textCursor()
        cursor.select(QTextCursor.Document)
        spacing = QTextBlockFormat()
        spacing.setLineHeight(150.0, QTextBlockFormat.ProportionalHeight.value)
        cursor.mergeBlockFormat(spacing)
        cursor.clearSelection()
        cursor.movePosition(QTextCursor.Start)
        self.body.setTextCursor(cursor)

    def finish_reader(self, *_):
        self.closed = True
        self.deleteLater()

    def search(self):
        if self.closed:
            return
        self.generation += 1
        generation = self.generation
        artist, track = self.artist.text().strip(), self.track.text().strip()
        self.info.setText('Поиск текста…')
        self.source_label.setText('Источники: LRCLIB · Lyrics.ovh · Genius · Яндекс Музыка')
        self.source_label.setToolTip('')
        self.search_button.setEnabled(False)
        self.owner.run_async(
            lambda: self.client.search(artist, track, self.row.get('duration', 0), force=True, row=self.row,
                cancelled=lambda: self.closed or self.owner.closing or generation != self.generation),
            lambda rows: self.show_results(rows) if not self.closed and generation == self.generation else None,
            lambda error: self.show_error(error) if not self.closed and generation == self.generation else None)

    def show_soundcloud_description(self):
        from media import soundcloud_lyrics
        self.generation+=1;generation=self.generation
        self.description_button.setEnabled(False);self.info.setText('Получаем полное описание SoundCloud…')
        def cancelled():return self.closed or self.owner.closing or self.generation!=generation
        def done(record):
            if self.closed:return
            self.description_button.setEnabled(True)
            if cancelled():return
            if record:self.show_results([record])
            else:self.info.setText('Описание пустое или SoundCloud не ответил. Попробуй ещё раз позже.')
        def failed(_error):
            if self.closed:return
            self.description_button.setEnabled(True)
            if not cancelled():self.info.setText('Не удалось получить описание SoundCloud. Попробуй ещё раз позже.')
        row=self.owner.store.get(self.row['id']) or self.row
        self.owner.run_async(lambda:soundcloud_lyrics(self.owner.store,row,cancelled=cancelled,force=True,full=True),done,failed)

    def show_error(self, error):
        if self.closed:
            return
        self.search_button.setEnabled(True)
        self.info.setText(str(error)+(' На экране оставлен предыдущий результат.' if self.body.toPlainText() else ''))

    def show_results(self, results):
        if self.closed:
            return
        self.search_button.setEnabled(True)
        self.versions.blockSignals(True)
        self.versions.clear()
        for row in results:
            secs = int(row.get('duration') or 0)
            label = f"{row.get('artistName', '')} — {row['trackName']}"
            if row.get('albumName'):
                label += ' · ' + row['albumName']
            label += ' · ' + row.get('source', 'LRCLIB')
            if secs:
                label += f' · {secs//60}:{secs%60:02}'
            self.versions.addItem(label, row)
        self.versions.setCurrentIndex(-1)
        self.versions.blockSignals(False)
        self.body.clear()
        self.versions.setVisible(len(results) > 1)
        if not results:
            self.refine.setChecked(True)
            self.info.setText('Текст не найден. Уточни исполнителя и название и нажми «Найти».')
            return
        artist, title = self.artist.text().strip(), self.track.text().strip()
        if results[0].get('source') in ('SoundCloud', 'Яндекс Музыка') or exact_match(results[0], artist, title, self.row.get('duration', 0)):
            self.versions.setCurrentIndex(0)
        else:
            self.versions.show()
            self.refine.setChecked(True)
            self.info.setText('Найдены возможные совпадения. Выбери нужную запись из списка.')

    def show_version(self, index):
        if self.closed or index < 0:
            return
        record = self.versions.itemData(index)
        source = ('SoundCloud · полное описание' if record.get('descriptionFallback') else 'SoundCloud · описание трека') if record.get('source') == 'SoundCloud' else record.get('source', 'LRCLIB')
        self.source_label.setText('Источник текста: ' + source+(' · сохранённый текст' if record.get('cachedCopy') else ''))
        self.source_label.setToolTip(record.get('sourceUrl', ''))
        text = plain_text(record)
        if text:
            self.track_label.setText(record['trackName'])
            self.artist_label.setText(record.get('artistName', ''))
            self.info.setText('Показано полное описание: оно может содержать ссылки, контакты и сведения о релизе.' if record.get('descriptionFallback') else ('Показан ранее сохранённый текст. Для новой проверки нажми «Найти».' if record.get('cachedCopy') else ''))
            # Never interpret provider data as HTML or executable rich text.
            self.set_lyrics(text)
        elif record.get('instrumental'):
            self.info.setText('Эта запись отмечена как инструментальная — текста нет.')
            self.body.clear()
        else:
            self.info.setText('Для этой версии текст не найден.')
            self.body.clear()
