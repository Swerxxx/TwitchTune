import sys
import os
from limits import MAX_TRACK_SECONDS
from ratings import CRITERIA, calculate, details, score_label, summary as rating_summary
import traceback
import keyring
from pathlib import Path
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import QObject, QUrl, Signal, Slot, QThread, QTimer, Qt
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QComboBox,
    QDialog, QDialogButtonBox, QPlainTextEdit, QGroupBox, QInputDialog, QSlider,
    QSpinBox, QTabWidget, QSplitter, QCheckBox, QFormLayout, QFrame, QGridLayout, QSizePolicy, QScrollArea, QFileDialog)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QAudioBufferOutput
from audio_visualizer import AudioWaves
from local_audio import import_mp3
from albums import album_reference, catalog, create_album
from album_window import AlbumsPanel
from album_covers import load_album_cover
from donation_settings import DonationSettings
from binary_update_settings import BinaryUpdateSettings
from core import QueueStore, load_config, save_config, telegram_request, telegram_delivery_note, parse_link
from media import Downloader, search, load_saved_cover, soundcloud_lyrics
from twitch import TwitchClient
from theme import INTERFACE_STYLES, THEMES, normalize_interface_style, normalize_theme, stylesheet
from visuals import Chassis, DeckDisplay, ElideLabel, TransportButton, QueueDelegate, ThemeCard
from telegram_support import TelegramClient, TelegramError, TelegramInbox
from telegram_orders import TelegramOrders
from lyrics import LyricsClient, song_query
from lyrics_window import LyricsDialog
from lyrics_resolver import LyricsResolver
from source_metadata import yandex_metadata, replacement_query
from track_files import remove_track_files, track_folder
from reward_settings import RewardDialog, guess_limit, limit_text
from yandex_direct import check_session
from vk_settings import VKSettings

APP = Path(os.environ.get('TWITCHTUNE_PROFILE_DIR') or (Path.home() / 'TwitchTune'))
APP_ICON = Path(__file__).resolve().parent / 'assets' / 'twitchtune.ico'
TELEGRAM_PAUSED = True


def clock(milliseconds):
    seconds = max(0, milliseconds // 1000)
    return f'{seconds // 60}:{seconds % 60:02}'


class Worker(QObject):
    done = Signal(object)
    failed = Signal(str)
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
    @Slot()
    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as exc:
            self.failed.emit(str(exc))


class SearchDialog(QDialog):
    def __init__(self, query, parent, source_url='', resolver=None):
        super().__init__(parent)
        self.setWindowTitle('Найти версию на YouTube')
        self.resize(760, 510)
        self.choice, self.closed, self.generation = None, False, 0
        self.owner = parent
        layout = QVBoxLayout(self)
        self.info = QLabel('Введи исполнителя и название песни.')
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        if source_url:
            origin = QLineEdit(source_url)
            origin.setReadOnly(True)
            origin.setToolTip('Исходная ссылка; её можно скопировать и открыть в браузере.')
            layout.addWidget(origin)
        line = QHBoxLayout()
        self.query = QLineEdit(query)
        self.query.setPlaceholderText('Исполнитель — название песни')
        self.query.returnPressed.connect(self.run_search)
        self.search_button = QPushButton('Искать')
        self.search_button.clicked.connect(self.run_search)
        line.addWidget(self.query, 1)
        line.addWidget(self.search_button)
        layout.addLayout(line)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        manual_row = QHBoxLayout()
        self.manual = QLineEdit()
        self.manual.setPlaceholderText('Или вставь прямую ссылку на нужное видео YouTube')
        manual_row.addWidget(self.manual, 1)
        manual_button = QPushButton('Выбрать ссылку')
        manual_button.clicked.connect(self.choose_manual)
        self.manual.returnPressed.connect(self.choose_manual)
        manual_row.addWidget(manual_button)
        layout.addLayout(manual_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.use = buttons.addButton('Выбрать запись', QDialogButtonBox.AcceptRole)
        self.use.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        self.list.currentItemChanged.connect(lambda *_: self.use.setEnabled(bool(self.list.currentItem())))
        self.finished.connect(lambda *_: setattr(self, 'closed', True))
        if query:
            self.run_search()
        elif resolver:
            self.info.setText('Пробуем определить исполнителя и название по исходной ссылке…')
            parent.run_async(resolver, self.metadata_loaded, self.metadata_failed)
        else:
            self.query.setFocus()

    def metadata_loaded(self, metadata):
        if self.closed or self.query.text().strip():
            return
        query = replacement_query(metadata)
        self.query.setText(query)
        if query:
            self.run_search()
        else:
            self.metadata_failed('Название недоступно.')

    def metadata_failed(self, error):
        if not self.closed and not self.query.text().strip():
            self.info.setText(str(error)[:500] + '\nУкажи название вручную — сама ссылка не подходит для поиска песни.')
            self.query.setFocus()

    def run_search(self):
        if self.closed:
            return
        query = self.query.text().strip()
        if not query:
            self.info.setText('Впиши исполнителя и название песни, затем нажми «Искать».')
            return
        self.generation += 1
        generation = self.generation
        self.list.clear()
        self.use.setEnabled(False)
        self.search_button.setEnabled(False)
        self.info.setText('Поиск записей на YouTube…')
        self.owner.run_async(lambda: search(query),
            lambda results: self.show_results(results) if not self.closed and generation == self.generation else None,
            lambda error: self.search_failed(error) if not self.closed and generation == self.generation else None)

    def search_failed(self, error):
        if self.closed:
            return
        self.search_button.setEnabled(True)
        self.info.setText('Ошибка поиска (это не означает, что песни нет):\n' + str(error)[:600]
                          + '\nМожно изменить запрос или вставить прямую ссылку YouTube внизу.')

    def show_results(self, results):
        if self.closed:
            return
        self.search_button.setEnabled(True)
        self.info.setText('Выбери подходящую запись:' if results else 'По этому запросу ничего не найдено. Уточни название или вставь ссылку YouTube внизу.')
        self.list.clear()
        for row in results:
            duration = ' · ' + clock(row['duration'] * 1000) if row.get('duration') else ''
            item = QListWidgetItem(row['title'] + duration + ' · ' + row['channel'])
            item.setData(Qt.UserRole, row)
            self.list.addItem(item)

    def choose_manual(self):
        try:
            url, source = parse_link(self.manual.text())
            if source != 'YouTube':
                raise ValueError('Здесь нужна ссылка на отдельное видео YouTube.')
            self.choice = dict(url=url, title=self.query.text().strip() or 'Выбранная версия YouTube')
            super().accept()
        except ValueError as exc:
            self.info.setText(str(exc))

    def accept(self):
        if self.list.currentItem():
            self.choice = self.list.currentItem().data(Qt.UserRole)
            super().accept()


class Main(QMainWindow):
    # All network/downloader callbacks cross this queued bridge before touching Qt.
    incoming = Signal(str, object)

    def __init__(self,smoke_test=False):
        super().__init__()
        self.smoke_test=smoke_test
        self.setWindowTitle('TwitchTune · ACID DECK')
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.resize(1280, 800)
        self.setMinimumSize(1040, 700)
        APP.mkdir(exist_ok=True)
        self.store = QueueStore(str(APP / 'queue.sqlite3'))
        self.cfg = load_config(APP)
        self.theme_key = normalize_theme(self.cfg.get('theme', 'green'))
        self.interface_style = normalize_interface_style(self.cfg.get('interface_style', self.cfg.get('button_style')))
        self.client = None
        self.current = None
        self.review_id = None
        self.lyrics_client = LyricsClient()
        self.lyrics_resolver = LyricsResolver(self.lyrics_client, settings=lambda: dict(self.cfg),store=self.store)
        self.lyrics_generation = 0
        self.lyrics_candidates = None
        self.lyrics_window = None
        self.reward = None
        self.available_rewards = []
        self.active_reward_limits = {}
        self.cover_attempts = set()
        self.displayed_cover = None
        self.jobs = []
        self.closing = False
        self.telegram = None
        self.telegram_orders = None
        self.telegram_reward_options = ()
        self.telegram_clients = []
        self.telegram_inbox = TelegramInbox(self.store)
        self.incoming.connect(self.handle_event, Qt.QueuedConnection)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(max(0, min(100, self.cfg.get('volume', 80))) / 100)
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.errorOccurred.connect(lambda *_: self.log.appendPlainText('Плеер: ' + self.player.errorString()))
        self.build()
        self.audio_buffers = QAudioBufferOutput(self)
        self.player.setAudioBufferOutput(self.audio_buffers)
        self.audio_buffers.audioBufferReceived.connect(self.audio_waves.receive_buffer)
        self.player.sourceChanged.connect(self.audio_waves.reset)
        self.player.positionChanged.connect(self.check_visualizer_seek)
        self.apply_theme(self.theme_key, persist=False)
        self.down = Downloader(self.store, APP / 'tracks', self.incoming.emit, settings=lambda: dict(self.cfg))
        self.refresh()
        for row in self.store.rows('all'):
            if row['status'] == 'Ожидает загрузки' and row['source'] != 'Telegram':
                self.down.submit(row['id'])
        self.login_if_ready()
        self.telegram_if_ready()
        self.start_private_bot_if_ready()
        self.donation_settings.autostart()

    def button(self, label, callback, layout):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def label(self, text, name=None):
        label = QLabel(text)
        if name:
            label.setObjectName(name)
        return label

    def panel(self, name='metal'):
        frame = QFrame()
        frame.setObjectName(name)
        return frame

    def build(self):
        self.setStyleSheet(stylesheet(self.theme_key, self.interface_style))
        root = Chassis()
        root.setObjectName('shell')
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(22, 15, 22, 16)
        main.setSpacing(12)

        header = QHBoxLayout()
        mark = self.label('TT', 'brandMark')
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(48, 48)
        header.addWidget(mark)
        wordmark = QVBoxLayout()
        wordmark.setSpacing(0)
        wordmark.addWidget(self.label('TWITCHTUNE', 'brand'))
        self.wordmark_subtitle = self.label('', 'micro')
        wordmark.addWidget(self.wordmark_subtitle)
        header.addLayout(wordmark)
        header.addStretch()
        header.addWidget(self.label('VOL. 01    /    DIGITAL AUDIO', 'micro'))
        header.addSpacing(20)
        self.status = ElideLabel('Twitch не подключён')
        self.status.setObjectName('muted')
        self.status.setMaximumWidth(240)
        header.addWidget(self.status)
        self.login = self.button('Подключить Twitch', self.connect_twitch, header)
        main.addLayout(header)

        request = self.panel()
        form = QHBoxLayout(request)
        form.setContentsMargins(14, 10, 14, 10)
        form.setSpacing(10)
        form.addWidget(self.label('INPUT /\nНОВАЯ ЗАЯВКА', 'acid'))
        self.viewer = QLineEdit()
        self.viewer.setPlaceholderText('Ник зрителя')
        self.viewer.setFixedWidth(160)
        self.url = QLineEdit()
        self.url.setPlaceholderText('Вставь ссылку на трек: YouTube, SoundCloud, Spotify…')
        self.url.returnPressed.connect(self.add_manual)
        form.addWidget(self.viewer)
        form.addWidget(self.url, 1)
        add = self.button('+ В очередь', self.add_manual, form)
        add.setObjectName('primary')
        self.import_mp3_button = self.button('+ MP3', self.choose_local_mp3, form)
        self.import_mp3_button.setToolTip('Добавить MP3 с компьютера. Ник берётся из поля слева.')
        main.addWidget(request)

        workspace = QHBoxLayout()
        workspace.setSpacing(12)
        rail = self.panel()
        rail.setFixedWidth(164)
        rail_lay = QVBoxLayout(rail)
        rail_lay.setContentsMargins(12, 16, 12, 14)
        rail_lay.setSpacing(7)
        rail_lay.addWidget(self.label('LIBRARY.EXE', 'micro'))
        rail_lay.addSpacing(6)
        self.nav_buttons = []
        for i, text in enumerate(('01  Очередь', '02  Зрители', '03  История', '04  Настройки', '05  Альбомы')):
            button = self.button(text, lambda checked=False, n=i: self.tabs.setCurrentIndex(n), rail_lay)
            button.setObjectName('nav')
            button.setCheckable(True)
            button.setFixedHeight(42)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        rail_lay.addSpacing(18)
        rail_lay.addWidget(self.label('СЕЙЧАС В ОЧЕРЕДИ', 'micro'))
        self.queue_count = self.label('00', 'counter')
        self.queue_count.setFixedHeight(43)
        rail_lay.addWidget(self.queue_count)
        rail_lay.addWidget(self.label('ОЦЕНЕНО ТРЕКОВ', 'micro'))
        self.rated_count = self.label('00', 'counter')
        self.rated_count.setFixedHeight(43)
        rail_lay.addWidget(self.rated_count)
        rail_lay.addStretch()
        self.source_legend = self.label('YOUTUBE\nSOUNDCLOUD\nSPOTIFY / YANDEX\nVK MUSIC', 'micro')
        rail_lay.addWidget(self.source_legend)
        self.source_legend.hide()
        rail_lay.addSpacing(15)
        log_button = self.button('Журнал событий', lambda: self.log.setVisible(not self.log.isVisible()), rail_lay)
        log_button.setObjectName('nav')
        log_button.setProperty('compact', True)
        log_button.setFixedHeight(42)
        self.rail_footer = self.label('TT—01 / LOCAL\nSTREAM AUDIO SYSTEM', 'micro')
        rail_lay.addWidget(self.rail_footer)
        workspace.addWidget(rail)

        content = self.panel()
        center = QVBoxLayout(content)
        center.setContentsMargins(14, 16, 14, 10)
        center.setSpacing(10)
        heading = QHBoxLayout()
        self.page_title = self.label('QUEUE / ОЧЕРЕДЬ', 'section')
        heading.addWidget(self.page_title)
        heading.addStretch()
        self.queue_summary = self.label('0 ЗАЯВОК', 'micro')
        heading.addWidget(self.queue_summary)
        center.addLayout(heading)
        reward_line = QHBoxLayout()
        reward_line.addWidget(self.label('НАГРАДА', 'micro'))
        self.rewards = QComboBox()
        self.rewards.setPlaceholderText('Выбери награду после подключения Twitch')
        self.rewards.setEnabled(False)
        self.rewards.activated.connect(self.choose_reward)
        reward_line.addWidget(self.rewards, 1)
        self.reward_settings_button = self.button('Награды', self.configure_rewards, reward_line)
        self.reward_settings_button.setEnabled(False)
        center.addLayout(reward_line)
        self.tabs = QTabWidget()
        self.tabs.setObjectName('pages')
        self.tabs.tabBar().hide()
        queue_tab = QWidget()
        queue_tab.setObjectName('page')
        queue_lay = QVBoxLayout(queue_tab)
        queue_lay.setContentsMargins(0, 0, 0, 0)
        queue_lay.setSpacing(8)
        columns = self.label('   №     ТРЕК / ЗРИТЕЛЬ                              СТАТУС', 'micro')
        columns.setFixedHeight(20)
        queue_lay.addWidget(columns)
        self.list = QListWidget()
        self.list.setObjectName('queue')
        self.list.setSpacing(2)
        self.list.setItemDelegate(QueueDelegate(self.list))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.itemDoubleClicked.connect(self.play_item)
        self.list.currentItemChanged.connect(self.show_download_error)
        queue_lay.addWidget(self.list, 1)
        self.download_error_box = QFrame()
        self.download_error_box.setObjectName('metal')
        error_layout = QVBoxLayout(self.download_error_box)
        self.download_error = QLabel()
        self.download_error.setTextFormat(Qt.PlainText)
        self.download_error.setWordWrap(True)
        self.download_error.setTextInteractionFlags(Qt.TextSelectableByMouse)
        error_layout.addWidget(self.download_error)
        self.yandex_settings_button = QPushButton('Настроить Яндекс Музыку')
        self.yandex_settings_button.clicked.connect(lambda: self.tabs.setCurrentIndex(3))
        error_layout.addWidget(self.yandex_settings_button, 0, Qt.AlignLeft)
        self.vk_settings_button = QPushButton('Настроить VK Музыку')
        self.vk_settings_button.clicked.connect(self.open_vk_settings)
        error_layout.addWidget(self.vk_settings_button, 0, Qt.AlignLeft)
        self.download_error_box.hide()
        queue_lay.addWidget(self.download_error_box)
        self.empty = self.label('ОЧЕРЕДЬ ПУСТА\n\nДобавь ссылку сверху или подключи Twitch.\nЗдесь появятся треки твоих зрителей.', 'muted')
        self.empty.setAlignment(Qt.AlignCenter)
        queue_lay.addWidget(self.empty, 1)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.button('↑', lambda: self.move(-1), actions).setToolTip('Поднять выбранную заявку')
        self.button('↓', lambda: self.move(1), actions).setToolTip('Опустить выбранную заявку')
        self.button('Найти версию', self.find_version, actions)
        self.button('Повторить', self.retry_selected, actions)
        actions.addStretch()
        self.button('Завершить', self.skip_selected, actions)
        self.button('Удалить', self.delete_selected, actions)
        queue_lay.addLayout(actions)
        self.tabs.addTab(queue_tab, 'Очередь')
        profile_tab = QWidget()
        profile_tab.setObjectName('page')
        profile_lay = QVBoxLayout(profile_tab)
        profile_lay.setContentsMargins(0, 0, 0, 0)
        info = self.label('Средний балл 11–66 · только оценённые треки и альбомы', 'muted')
        info.setWordWrap(True)
        info.setMaximumHeight(36)
        profile_lay.addWidget(info)
        split = QSplitter()
        self.profiles = QListWidget()
        self.profiles.setWordWrap(True)
        self.profiles.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.profiles.currentItemChanged.connect(self.show_profile)
        self.profile_history = QListWidget()
        self.profile_history.itemDoubleClicked.connect(self.select_history_track)
        split.addWidget(self.profiles)
        split.addWidget(self.profile_history)
        split.setStretchFactor(1, 2)
        profile_lay.addWidget(split, 1)
        vip_actions = QHBoxLayout()
        self.button('VIP по нику',self.grant_vip_by_name,vip_actions)
        self.vip_grant_button = self.button('Выдать VIP',lambda:self.change_vip('grant'),vip_actions)
        self.vip_remove_button = self.button('Снять VIP',lambda:self.change_vip('remove'),vip_actions)
        self.vip_reset_button = self.button('Обновить VIP',lambda:self.change_vip('reset'),vip_actions)
        profile_lay.addLayout(vip_actions)
        self.vip_info = self.label('VIP TwitchTune: 3 новых трека с приоритетом. «Обновить VIP» возвращает лимит 3/3.', 'muted')
        self.vip_info.setWordWrap(True)
        profile_lay.addWidget(self.vip_info)
        self.tabs.addTab(profile_tab, 'Зрители')
        self.build_history_tab()
        self.build_settings_tab()
        # Keep the integration for a later release; no visible Telegram entry now.
        self.albums_panel = AlbumsPanel(self.store, self.import_album, self.play_album_at, self.retry_album, self.review_album, self.delete_album)
        self.tabs.addTab(self.albums_panel, 'Альбомы')
        self.build_telegram_tab()
        self.tabs.setTabVisible(5, not TELEGRAM_PAUSED)
        self.tabs.currentChanged.connect(self.navigation_changed)
        center.addWidget(self.tabs, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMaximumHeight(90)
        self.log.setPlaceholderText('Журнал TwitchTune')
        self.log.hide()
        center.addWidget(self.log)
        workspace.addWidget(content, 1)

        right = QVBoxLayout()
        right.setSpacing(12)
        self.deck = DeckDisplay()
        right.addWidget(self.deck)
        review = self.panel()
        review.setFixedWidth(264)
        review_lay = QVBoxLayout(review)
        review_lay.setContentsMargins(14, 15, 14, 15)
        review_lay.setSpacing(4)
        review_lay.addWidget(self.label('REVIEW.EXE  /  ОЦЕНКА', 'section'))
        self.rating_target = self.label('Выбери заявку в очереди', 'muted')
        self.rating_target.setWordWrap(True)
        self.rating_target.setMaximumHeight(45)
        review_lay.addWidget(self.rating_target)
        self.text_rating_enabled = QCheckBox('Оценивать текст')
        self.text_rating_enabled.setChecked(True)
        self.text_rating_enabled.toggled.connect(self.update_rating_total)
        review_lay.addWidget(self.text_rating_enabled)
        self.criterion_spins = {}
        criteria_form = QFormLayout()
        criteria_form.setVerticalSpacing(4)
        for key, caption in CRITERIA:
            spin = QSpinBox()
            spin.setObjectName('criterionScore')
            spin.setRange(0, 6)
            spin.setSpecialValueText('—')
            spin.setSuffix(' / 6')
            spin.setFixedWidth(78)
            spin.setFixedHeight(26)
            spin.setToolTip('Выбери оценку от 1 до 6')
            spin.valueChanged.connect(self.update_rating_total)
            self.criterion_spins[key] = spin
            criteria_form.addRow(self.label(caption, 'muted'), spin)
        review_lay.addLayout(criteria_form)
        self.rating_total = self.label('— / 66', 'section')
        self.rating_total.setStyleSheet('font-size: 26px; font-weight: bold;')
        review_lay.addWidget(self.rating_total)
        self.rating_hint = self.label('Заполни включённые критерии', 'micro')
        self.rating_hint.setWordWrap(True)
        review_lay.addWidget(self.rating_hint)
        self.comment = QLineEdit()
        self.comment.setMaxLength(2000)
        self.comment.setPlaceholderText('Комментарий к треку…')
        self.comment.returnPressed.connect(self.save_rating)
        review_lay.addWidget(self.comment)
        self.save_rating_btn = self.button('Сохранить оценку', self.save_rating, review_lay)
        self.save_rating_btn.setObjectName('primary')
        self.save_rating_btn.setEnabled(False)
        self.list.currentItemChanged.connect(self.select_review)
        self.audio_waves = AudioWaves()
        review_lay.addWidget(self.audio_waves, 1)
        note = self.label('Оценка попадёт в профиль зрителя.\nМожно изменить после прослушивания.', 'micro')
        note.setWordWrap(True)
        review_lay.addWidget(note)
        right.addWidget(review, 1)
        workspace.addLayout(right)
        main.addLayout(workspace, 1)
        self.build_player_dock(main)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'deck'):
            self.deck.setVisible(self.height() >= 960)
            self.rail_footer.setVisible(self.height() >= 760)

    def navigation_changed(self, index):
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        titles = ('QUEUE / ОЧЕРЕДЬ', 'VIEWERS / ЗРИТЕЛИ', 'HISTORY / ИСТОРИЯ', 'SETTINGS / НАСТРОЙКИ', 'ALBUMS / АЛЬБОМЫ', 'TELEGRAM / MP3')
        self.page_title.setText(titles[index] if 0 <= index < len(titles) else titles[0])
        self.update_section_count()
        widget = self.history_list if index == 2 else self.list if index == 0 else None
        if widget and widget.currentItem():
            self.select_review(widget.currentItem())

    def build_settings_tab(self):
        page = QWidget()
        page.setObjectName('page')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName('settingsScroll')
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 4, 8, 4)
        content.setSpacing(12)
        self.binary_updates=BinaryUpdateSettings(self,APP)
        content.addWidget(self.binary_updates)
        appearance = QGroupBox('ОФОРМЛЕНИЕ ИНТЕРФЕЙСА')
        appearance_layout = QFormLayout(appearance)
        self.interface_style_picker = QComboBox()
        self.interface_style_picker.setAccessibleName('Стиль интерфейса')
        for key, name in INTERFACE_STYLES.items():
            self.interface_style_picker.addItem(name, key)
        self.interface_style_picker.setCurrentIndex(self.interface_style_picker.findData(self.interface_style))
        self.interface_style_picker.currentIndexChanged.connect(self.apply_interface_style)
        appearance_layout.addRow('Стиль интерфейса', self.interface_style_picker)
        preview = QPushButton('Пример кнопки')
        preview.setCheckable(True)
        preview.setToolTip('Нажми, чтобы увидеть выбранное состояние')
        appearance_layout.addRow('Предпросмотр', preview)
        description = self.label('Dark Goth — гравированные рамки, потёртый металл и бордовая подсветка.', 'muted')
        description.setWordWrap(True)
        appearance_layout.addRow(description)
        content.addWidget(appearance)
        bot_box = QGroupBox('TELEGRAM · ПРИЁМ MP3')
        bot_layout = QVBoxLayout(bot_box)
        bot_hint = QLabel('Зритель входит через Twitch в личном чате бота, отправляет MP3 и текст. '
                          'Трек попадает в очередь только после подходящей награды с кодом от бота.')
        bot_hint.setWordWrap(True)
        bot_layout.addWidget(bot_hint)
        self.private_bot_status = QLabel('Бот не подключён')
        self.private_bot_status.setTextFormat(Qt.PlainText)
        self.private_bot_status.setWordWrap(True)
        bot_layout.addWidget(self.private_bot_status)
        actions = QHBoxLayout()
        self.button('Подключить бота', self.configure_private_bot, actions)
        self.button('Отключить', self.stop_private_bot, actions)
        bot_layout.addLayout(actions)
        content.addWidget(bot_box)
        self.donation_settings=DonationSettings(self,APP)
        content.addWidget(self.donation_settings)
        self.build_yandex_settings(content)
        self.vk_settings = VKSettings(self, APP)
        content.addWidget(self.vk_settings)
        self.settings_scroll = scroll
        self.palette_options = QWidget()
        palette_layout = QVBoxLayout(self.palette_options)
        palette_layout.setContentsMargins(0, 0, 0, 0)
        palette_layout.setSpacing(12)
        palette_layout.addWidget(self.label('Цвет интерфейса', 'section'))
        intro = self.label('Тёмный корпус и аппаратный стиль остаются. Меняются подсветка, дисплей, кнопки и оттенок панелей.', 'muted')
        intro.setWordWrap(True)
        palette_layout.addWidget(intro)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.theme_buttons = {}
        for i, key in enumerate(THEMES):
            button = ThemeCard(key)
            button.clicked.connect(lambda checked=False, value=key: self.apply_theme(value))
            grid.addWidget(button, i//2, i%2)
            self.theme_buttons[key] = button
        palette_layout.addLayout(grid)
        self.theme_description = self.label('', 'muted')
        self.theme_description.setWordWrap(True)
        palette_layout.addWidget(self.theme_description)
        palette_layout.addWidget(self.label('Применяется сразу · сохраняется автоматически', 'micro'))
        content.addWidget(self.palette_options)
        content.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll)
        self.tabs.addTab(page, 'Настройки')

    def build_yandex_settings(self, layout):
        box = QGroupBox('ЯНДЕКС МУЗЫКА / ПРЯМАЯ ЗАГРУЗКА')
        form = QVBoxLayout(box)
        help_text = QLabel('Для полных треков нужен ваш вход Яндекса с подпиской и доступом к песне. Открой music.yandex.ru и войди в выбранном браузере.')
        help_text.setWordWrap(True)
        form.addWidget(help_text)
        self.yandex_auth = QComboBox()
        for title, value in [('Выбери источник входа', ''), ('Firefox', 'firefox'), ('Chrome', 'chrome'), ('Edge', 'edge'), ('Файл cookies.txt', 'cookies')]:
            self.yandex_auth.addItem(title, value)
        index = self.yandex_auth.findData(self.cfg.get('yandex_auth', ''))
        self.yandex_auth.setCurrentIndex(max(0, index))
        form.addWidget(self.yandex_auth)
        self.yandex_session_generation = 0
        self.yandex_profile_row = QWidget()
        profile_line = QHBoxLayout(self.yandex_profile_row)
        profile_line.setContentsMargins(0,0,0,0)
        self.yandex_profile_path = QLineEdit()
        self.yandex_profile_path.setReadOnly(True)
        self.yandex_profile_path.setPlaceholderText('Профиль браузера: определить автоматически')
        saved_profiles = self.cfg.get('yandex_profiles') or {}
        if isinstance(saved_profiles, dict):
            self.yandex_profile_path.setText(saved_profiles.get(self.yandex_auth.currentData(),''))
        profile_line.addWidget(self.yandex_profile_path,1)
        self.button('Папка профиля',self.choose_yandex_profile,profile_line)
        self.button('Авто',lambda:self.set_yandex_profile(''),profile_line)
        form.addWidget(self.yandex_profile_row)
        self.yandex_profile_row.setVisible(self.yandex_auth.currentData() in ('firefox','chrome','edge'))
        self.yandex_cookie_row = QWidget()
        row = QHBoxLayout(self.yandex_cookie_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.yandex_cookie_path = QLineEdit(self.cfg.get('yandex_cookie_file', ''))
        self.yandex_cookie_path.setReadOnly(True)
        self.yandex_cookie_path.setPlaceholderText('Файл сессии Яндекса в формате Netscape')
        row.addWidget(self.yandex_cookie_path, 1)
        self.button('Выбрать файл', self.choose_yandex_cookie_file, row)
        form.addWidget(self.yandex_cookie_row)
        self.yandex_cookie_row.setVisible(self.yandex_auth.currentData() == 'cookies')
        note = QLabel('Выбор сохраняется. Затем выдели неудачную заявку и нажми «Повторить». Короткие превью не принимаются. Если браузер не даёт прочитать вход, попробуй закрыть его или выбери файл сессии.')
        note.setObjectName('muted')
        note.setWordWrap(True)
        form.addWidget(note)
        self.yandex_check_button = QPushButton('Проверить вход')
        self.yandex_check_button.clicked.connect(self.check_yandex_session)
        self.yandex_check_button.setEnabled(bool(self.yandex_auth.currentData()))
        form.addWidget(self.yandex_check_button,0,Qt.AlignLeft)
        self.yandex_session_info = QLabel('Для Firefox: about:profiles → Корневой каталог. Выбирай профиль, в котором открыт Яндекс.')
        self.yandex_session_info.setObjectName('muted')
        self.yandex_session_info.setWordWrap(True)
        self.yandex_session_info.setTextFormat(Qt.PlainText)
        self.yandex_session_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(self.yandex_session_info)
        self.yandex_auth.currentIndexChanged.connect(self.save_yandex_source)
        layout.addWidget(box)

    def save_yandex_source(self, *_):
        self.cfg['yandex_auth'] = self.yandex_auth.currentData()
        self.yandex_cookie_row.setVisible(self.yandex_auth.currentData() == 'cookies')
        self.yandex_profile_row.setVisible(self.yandex_auth.currentData() in ('firefox','chrome','edge'))
        profiles=self.cfg.get('yandex_profiles') or {}
        self.yandex_profile_path.setText(profiles.get(self.yandex_auth.currentData(),'') if isinstance(profiles,dict) else '')
        self.yandex_session_generation += 1
        self.yandex_session_info.setText('Источник входа изменён. Нажми «Проверить вход».')
        self.yandex_check_button.setEnabled(bool(self.yandex_auth.currentData()))
        save_config(APP, self.cfg)

    def choose_yandex_profile(self):
        path = QFileDialog.getExistingDirectory(self,'Папка профиля браузера',self.yandex_profile_path.text())
        if path:
            self.set_yandex_profile(path)

    def set_yandex_profile(self,path):
        profiles=self.cfg.get('yandex_profiles')
        if not isinstance(profiles,dict):profiles={}
        profiles[self.yandex_auth.currentData()]=path
        self.cfg['yandex_profiles']=profiles
        self.yandex_profile_path.setText(path)
        self.yandex_session_generation += 1
        self.yandex_session_info.setText('Профиль сохранён. Нажми «Проверить вход».')
        save_config(APP,self.cfg)

    def check_yandex_session(self):
        if not self.yandex_auth.currentData():return
        generation=self.yandex_session_generation
        settings=dict(self.cfg)
        profiles=self.cfg.get('yandex_profiles')
        settings['yandex_profiles']=dict(profiles) if isinstance(profiles,dict) else {}
        self.yandex_check_button.setEnabled(False)
        self.yandex_session_info.setText('Проверяю выбранный источник входа…')
        def finish(message):
            self.yandex_check_button.setEnabled(bool(self.yandex_auth.currentData()))
            if generation == self.yandex_session_generation:
                self.yandex_session_info.setText(str(message))
        self.run_async(lambda:check_session(settings),finish,finish)

    def choose_yandex_cookie_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Файл сессии Яндекс Музыки', '', 'Cookies (*.txt);;Все файлы (*)')
        if path:
            self.yandex_cookie_path.setText(path)
            self.cfg['yandex_cookie_file'] = path
            self.yandex_session_generation += 1
            self.yandex_session_info.setText('Файл сохранён. Нажми «Проверить вход».')
            save_config(APP, self.cfg)

    def apply_interface_style(self, index):
        self.interface_style = normalize_interface_style(self.interface_style_picker.itemData(index))
        self.cfg['interface_style'] = self.interface_style
        self.apply_theme(self.theme_key, persist=False)
        try:
            save_config(APP, self.cfg)
        except OSError:
            QMessageBox.warning(self, 'Настройки', 'Стиль применён, но сохранить выбор не удалось.')

    def apply_theme(self, key, persist=True):
        self.theme_key = normalize_theme(key)
        self.setStyleSheet(stylesheet(self.theme_key, self.interface_style))
        goth = self.interface_style == 'dark_goth'
        deck = 'DARK GOTH' if goth else 'ACID DECK'
        self.setWindowTitle(f'TwitchTune · {deck}')
        self.wordmark_subtitle.setText(f'{deck}  /  STREAM MUSIC SYSTEM')
        self.palette_options.setVisible(not goth)
        self.show_cover(self.store.get(self.current) if self.current else None)
        for value, button in self.theme_buttons.items():
            button.setChecked(value == self.theme_key)
        theme = THEMES[self.theme_key]
        self.theme_description.setText(theme['name'] + ' / ' + theme['description'])
        # Repaint all custom-drawn surfaces with this window's palette.
        self.centralWidget().update()
        for widget in self.findChildren(QWidget):
            widget.update()
        self.list.viewport().update()
        if persist:
            self.cfg['theme'] = self.theme_key
            try:
                save_config(APP, self.cfg)
            except OSError:
                QMessageBox.warning(self, 'Настройки', 'Цвет применён, но сохранить выбор не удалось.')

    def rating_draft(self):
        return ({key: spin.value() for key, spin in self.criterion_spins.items()},
                self.text_rating_enabled.isChecked(), self.comment.text())

    def restore_rating_draft(self, draft):
        scores, enabled, comment = draft
        for key, spin in self.criterion_spins.items():
            spin.setValue(scores.get(key) or 0)
        self.text_rating_enabled.setChecked(enabled)
        self.comment.setText(comment)
        self.update_rating_total()

    def update_rating_total(self, *_):
        if not hasattr(self, 'rating_hint'):
            return
        enabled = self.text_rating_enabled.isChecked()
        row = self.selected()
        for key, spin in self.criterion_spins.items():
            spin.setEnabled(bool(row) and (key != 'text' or enabled))
        self.text_rating_enabled.setEnabled(bool(row))
        try:
            total = calculate({key: spin.value() for key, spin in self.criterion_spins.items()}, enabled)
            self.rating_total.setText(f'{total} / 66')
            self.rating_hint.setText('С текстом' if enabled else 'Без текста · максимум тоже 66')
            complete = True
        except ValueError:
            self.rating_total.setText('— / 66')
            legacy = score_label(row) if row and row['rating'] is not None and row.get('rating_scale') != 66 else ''
            self.rating_hint.setText(('Старая оценка: ' + legacy + '. ') if legacy else 'Заполни включённые критерии')
            complete = False
        if hasattr(self, 'save_rating_btn'):
            self.save_rating_btn.setEnabled(bool(row) and complete)

    def build_player_dock(self, main):
        self.player_dock = self.panel('dock')
        self.player_dock.setFixedHeight(112)
        dock = QHBoxLayout(self.player_dock)
        dock.setContentsMargins(17, 8, 17, 8)
        dock.setSpacing(16)
        left = QWidget()
        left.setFixedWidth(250)
        track = QHBoxLayout(left)
        track.setContentsMargins(0, 0, 0, 0)
        badge = self.label('', 'coverBadge')
        self.cover_badge = badge
        badge.setFixedSize(49, 49)
        badge.setAlignment(Qt.AlignCenter)
        track.addWidget(badge)
        self.show_cover(None)
        metadata = QVBoxLayout()
        metadata.setSpacing(4)
        metadata.setAlignment(Qt.AlignVCenter)
        self.now_title = ElideLabel('Ничего не играет')
        self.now_title.setObjectName('nowTitle')
        self.now_title.setFixedHeight(20)
        self.now_viewer = ElideLabel('Выбери готовый трек')
        self.now_viewer.setObjectName('muted')
        self.now_viewer.setFixedHeight(18)
        metadata.addWidget(self.now_title)
        metadata.addWidget(self.now_viewer)
        self.lyrics_button = QPushButton('Текст песни')
        self.lyrics_button.setObjectName('lyricsButton')
        self.lyrics_button.setFixedHeight(24)
        self.lyrics_button.setEnabled(False)
        self.lyrics_button.clicked.connect(self.show_lyrics)
        metadata.addWidget(self.lyrics_button, 0, Qt.AlignLeft)
        track.addLayout(metadata, 1)
        dock.addWidget(left)
        middle = QWidget()
        middle.setObjectName('transportCenter')
        player = QVBoxLayout(middle)
        player.setContentsMargins(0, 0, 0, 0)
        player.setSpacing(5)
        controls = QHBoxLayout()
        controls.setSpacing(15)
        controls.addStretch()
        self.prev = TransportButton('prev')
        self.prev.clicked.connect(self.previous)
        controls.addWidget(self.prev)
        self.play = TransportButton('play')
        self.play.clicked.connect(self.toggle)
        controls.addWidget(self.play)
        self.next = TransportButton('next')
        self.next.clicked.connect(self.skip_current)
        controls.addWidget(self.next)
        controls.addStretch()
        player.addLayout(controls)
        timeline = QHBoxLayout()
        timeline.setSpacing(9)
        self.elapsed = self.label('0:00', 'micro')
        self.duration = self.label('0:00', 'micro')
        self.elapsed.setFixedWidth(35)
        self.duration.setFixedWidth(35)
        self.duration.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setToolTip('Перемотка трека')
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.playback_changed)
        timeline.addWidget(self.elapsed)
        timeline.addWidget(self.seek, 1)
        timeline.addWidget(self.duration)
        player.addLayout(timeline)
        dock.addWidget(middle, 1)
        right = QWidget()
        right.setFixedWidth(250)
        volume = QVBoxLayout(right)
        volume.setContentsMargins(15, 10, 0, 10)
        volume.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        volume.addWidget(self.label('VOLUME / ГРОМКОСТЬ', 'micro'), 0, Qt.AlignRight)
        volume_line = QHBoxLayout()
        volume_line.addStretch()
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setFixedWidth(140)
        self.volume.setValue(round(self.audio.volume() * 100))
        self.volume.setToolTip('Громкость')
        self.volume_label = self.label(f'{self.volume.value()}%', 'acid')
        self.volume_label.setFixedWidth(38)
        self.volume.valueChanged.connect(self.set_volume)
        volume_line.addWidget(self.volume)
        volume_line.addWidget(self.volume_label)
        volume.addLayout(volume_line)
        dock.addWidget(right)
        main.addWidget(self.player_dock)

    def playback_changed(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play.set_playing(playing)
        self.deck.set_playing(playing)
        self.audio_waves.set_playing(playing)

    def check_visualizer_seek(self, position):
        previous = getattr(self, 'visualizer_position', position)
        if position < previous or position - previous > 1000:
            self.audio_waves.reset()
        self.visualizer_position = position

    def start_lyrics_lookup(self, row):
        self.lyrics_generation += 1
        generation = self.lyrics_generation
        self.lyrics_candidates = None
        artist, title = song_query(row)
        self.lyrics_button.setText('Ищем текст…')
        self.lyrics_button.setEnabled(False)
        if row.get('submitted_lyrics') is not None:
            text = row['submitted_lyrics']
            self.lyrics_candidates = ([dict(artistName=artist, trackName=title or row['title'],
                                            plainLyrics=text, source='Текст от зрителя')] if text else [])
            self.lyrics_button.setText('Текст песни' if text else 'Текста нет')
            self.lyrics_button.setToolTip('Текст от зрителя' if text else 'Зритель указал, что текста нет')
            self.lyrics_button.setEnabled(bool(text))
            return
        if not title:
            self.lyrics_button.setText('Найти текст')
            self.lyrics_button.setEnabled(True)
            return
        def active():
            return not self.closing and self.playing_track_id() == row['id'] and generation == self.lyrics_generation
        def found(results):
            if active():
                self.lyrics_candidates = results
                self.lyrics_button.setText('Текст песни' if results else 'Найти текст')
                self.lyrics_button.setEnabled(True)
                self.lyrics_button.setToolTip('Открыть текст / выбрать версию' if results else 'Текст не найден — уточнить название')
        def failed(error):
            if active():
                self.lyrics_button.setText('Найти текст')
                self.lyrics_button.setEnabled(True)
                self.lyrics_button.setToolTip(str(error))
        def lookup():
            local = soundcloud_lyrics(self.store, row, cancelled=lambda: not active())
            if local:
                return [local]
            return self.lyrics_resolver.search(artist, title, row.get('duration', 0), row=row, cancelled=lambda: not active())
        self.run_async(lookup, found, failed)

    def show_lyrics(self):
        row = self.store.get(self.playing_track_id()) if self.current else None
        if not row:
            return
        if self.lyrics_window and self.lyrics_window.row['id'] == row['id']:
            self.lyrics_window.showNormal()
            self.lyrics_window.raise_()
            self.lyrics_window.activateWindow()
            return
        if self.lyrics_window:
            self.lyrics_window.reject()
        window = LyricsDialog(row, self.lyrics_resolver, self, self.lyrics_candidates)
        self.lyrics_window = window
        window.finished.connect(lambda *_: self.forget_lyrics_window(window))
        window.show()

    def show_cover(self, row):
        path = row.get('cover_path', '') if row else ''
        key = (row['id'] if row else '', path, self.interface_style)
        if key == self.displayed_cover:
            return
        self.displayed_cover = key
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull() and self.interface_style == 'dark_goth':
            self.cover_badge.setText('TT')
            self.cover_badge.setToolTip(row['title'] if row else 'TwitchTune')
            return
        if pixmap.isNull():
            pixmap = QIcon(str(APP_ICON)).pixmap(49, 49)
        pixmap = pixmap.scaled(49, 49, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        if not pixmap.isNull():
            pixmap = pixmap.copy((pixmap.width()-49)//2, (pixmap.height()-49)//2, 49, 49)
        self.cover_badge.setPixmap(pixmap)
        self.cover_badge.setToolTip(row['title'] if row else 'TwitchTune')

    def ensure_cover(self, row):
        if row.get('cover_path') and Path(row['cover_path']).is_file():
            return
        if row['id'] in self.cover_attempts:
            return
        self.cover_attempts.add(row['id'])
        def done(_):
            if self.playing_track_id() == row['id']:
                self.displayed_cover = None
                self.show_cover(self.store.get(row['id']))
            self.refresh()
        self.run_async(lambda: load_saved_cover(self.store, APP/'tracks', row, cancelled=lambda: self.closing), done, lambda _: None)

    def forget_lyrics_window(self, window):
        if self.lyrics_window is window:
            self.lyrics_window = None

    @Slot(str, object)
    def handle_event(self, typ, data):
        if typ == 'job_finished':
            self.release_job(data)
            return
        if self.closing:
            return
        if typ == 'twitch':
            client, kind, value = data
            if client is self.client:
                self.handle_event(kind, value)
        elif typ == 'telegram':
            client, kind, value = data
            if client is self.telegram:
                if kind == 'telegram_status':
                    self.telegram_status.setText(value)
                    self.telegram_status.setToolTip(value)
                if kind in ('telegram_status', 'telegram_refresh'):
                    self.refresh()
        elif typ == 'private_bot':
            bot, status = data
            if bot is self.telegram_orders:
                self.private_bot_status.setText(status)
        elif typ == 'callback':
            callback, value = data
            callback(value)
        elif typ == 'paid_service':
            self.donation_settings.handle(*data)
        elif typ == 'binary_update_progress':
            self.binary_updates.progress(*data)
        elif typ == 'queue':
            self.refresh()
            if getattr(self,'waiting_paid',False) and not self.current:self.next_track()
        elif typ == 'log':
            self.log.appendPlainText(str(data))
        elif typ == 'connection':
            self.status.setText(str(data))
            self.telegram_reward_options = tuple(
                (reward['id'], self.active_reward_limits[reward['id']], reward['title'])
                for reward in self.available_rewards if reward['id'] in self.active_reward_limits
            ) if str(data).startswith('Подключено') else ()
        elif typ == 'auth':
            self.log.appendPlainText(f"Открылся Twitch: код {data['code']}")
        elif typ == 'redemption':
            self.redeem(data)
        elif typ == 'redemption_update':
            self.redemption_updated(data)

    def login_if_ready(self):
        if self.cfg.get('client_id'):
            self.connect_twitch()

    def make_client(self, client_id):
        client = TwitchClient(client_id, lambda kind, value:
                              self.incoming.emit('twitch', (client, kind, value)))
        return client

    def connect_twitch(self):
        self.telegram_reward_options = ()
        cid = self.cfg.get('client_id')
        if not cid:
            cid, ok = QInputDialog.getText(self, 'Twitch Client ID', 'Вставь Client ID приложения Twitch (без секрета):')
            if not ok or not cid.strip():
                return
            self.cfg['client_id'] = cid.strip()
            save_config(APP, self.cfg)
        if self.client:
            self.client.close()
        self.client = self.make_client(self.cfg['client_id'])
        self.login.setEnabled(False)
        self.rewards.setEnabled(False)
        self.reward_settings_button.setEnabled(False)
        self.status.setText('Вход в Twitch…')
        self.run_async(self.client.login, self.after_login, self.login_failed)

    def login_failed(self, error):
        self.login.setEnabled(True)
        self.status.setText('Ошибка входа')
        self.log.appendPlainText(error)

    def after_login(self, info):
        self.status.setText(f"Вошёл как {info.get('login')}")
        self.login.setText('Переподключить Twitch')
        self.run_async(self.client.rewards, self.show_rewards, self.rewards_failed)

    def rewards_failed(self, error):
        self.login.setEnabled(True)
        self.async_error(error)

    def show_rewards(self, rewards):
        self.login.setEnabled(True)
        self.available_rewards = [r for r in rewards if r.get('is_enabled') and r.get('is_user_input_required')]
        self.reward_settings_button.setEnabled(bool(self.available_rewards))
        self.rewards.clear()
        for reward in self.available_rewards:
            self.rewards.addItem(reward['title'], reward)
        if not self.rewards.count():
            self.log.appendPlainText('Создай награду с обязательным текстовым полем, затем переподключи Twitch.')
            return
        saved = self.cfg.get('reward_limits', {})
        selected = {r['id']: saved[r['id']] for r in self.available_rewards
                    if isinstance(saved, dict) and type(saved.get(r['id'])) is int and 1 <= saved[r['id']] <= MAX_TRACK_SECONDS}
        if len(selected) > 1:
            self.rewards.insertItem(0, f'Принимаются награды: {len(selected)}', None)
            self.rewards.setCurrentIndex(0)
            self.rewards.setEnabled(True)
            self.activate_rewards(selected)
            return
        index = next((i for i in range(self.rewards.count())
                      if self.rewards.itemData(i)['id'] in selected), -1)
        if index < 0:
            index = next((i for i in range(self.rewards.count())
                      if self.rewards.itemData(i)['id'] == self.cfg.get('reward_id')), 0)
        self.rewards.setCurrentIndex(index)
        self.rewards.setEnabled(True)
        self.choose_reward(index)

    def choose_reward(self, index):
        reward = self.rewards.itemData(index)
        if not reward or not self.client:
            return
        if self.rewards.count() and self.rewards.itemData(0) is None:
            self.rewards.removeItem(0)
        saved = self.cfg.get('reward_limits', {})
        limit = saved.get(reward['id'], guess_limit(reward['title'])) if isinstance(saved, dict) else guess_limit(reward['title'])
        if type(limit) is not int or not 1 <= limit <= MAX_TRACK_SECONDS:
            limit = guess_limit(reward['title'])
        self.activate_rewards({reward['id']: limit})

    def configure_rewards(self):
        if not self.available_rewards or not self.client:
            return
        dialog = RewardDialog(self.available_rewards, self.active_reward_limits, self)
        if dialog.exec() == QDialog.Accepted:
            self.cfg['reward_limits'] = dialog.selection()
            self.show_rewards(self.available_rewards)
        dialog.deleteLater()

    def activate_rewards(self, limits):
        selected = [r for r in self.available_rewards if r['id'] in limits]
        if not selected or not self.client:
            return
        self.telegram_reward_options = ()
        self.reward = selected[0]
        self.active_reward_limits = dict(limits)
        self.cfg['reward_limits'] = dict(limits)
        self.cfg['reward_id'] = selected[0]['id']
        save_config(APP, self.cfg)
        self.reward_settings_button.setText(f'Награды ({len(selected)})')
        description = '\n'.join(f"{r['title']} — до {limit_text(limits[r['id']])}" for r in selected)
        self.rewards.setToolTip(description)
        self.log.appendPlainText('Принимаются награды:\n' + description)
        # Reuse authorization, but a separate client owns each socket lifecycle.
        old = self.client
        old.close()
        client = self.make_client(old.client_id)
        client.tokens, client.user = old.tokens, old.user
        client.last_validate = old.last_validate
        self.client = client
        ids = [r['id'] for r in selected]
        client.connect(ids if len(ids) > 1 else ids[0])

    def redeem(self, event):
        if str(event.get('status','')).lower() in ('canceled','cancelled'):
            self.redemption_updated(event)
            return
        # A late event from the previous socket cannot select a different reward.
        event_reward = event.get('reward', {})
        reward_id = event_reward.get('id')
        if self.active_reward_limits and reward_id not in self.active_reward_limits:
            return
        if not self.active_reward_limits and self.reward and reward_id != self.reward['id']:
            return
        if self.telegram_orders and not self.telegram_orders.stop.is_set():
            try:
                code = telegram_request(event.get('user_input', ''))
                if code:
                    if self.telegram_orders.accept_reward(event, code):
                        self.log.appendPlainText('MP3 из Telegram добавлен по награде Twitch.')
                        self.refresh()
                    return
                if code == '':
                    raise ValueError('Вставь в награду код MP3 из бота.')
            except ValueError as exc:
                self.log.appendPlainText('Заявка Telegram отклонена: ' + str(exc))
                return
        if TELEGRAM_PAUSED and telegram_delivery_note(event.get('user_input', '')):
            self.log.appendPlainText(f"Заявка от {event.get('user_name', 'зритель')}: пропущена — трек в Telegram.")
            return
        if album_reference(event.get('user_input','')):
            self.import_album(event.get('user_input',''),dict(viewer=event.get('user_name','зритель'),
                redemption=event.get('id'),viewer_id=event.get('user_id'),
                max_duration=self.active_reward_limits.get(reward_id,MAX_TRACK_SECONDS),reward_title=event_reward.get('title','')))
            return
        try:
            ident = self.add_request(event.get('user_input', ''), event.get('user_name', 'зритель'),
                                   event.get('id'), viewer_id=event.get('user_id'),
                                   max_duration=self.active_reward_limits.get(reward_id, MAX_TRACK_SECONDS),
                                   reward_title=event_reward.get('title', ''))
            if ident:
                self.submit_request(ident)
                self.log.appendPlainText(f"Заявка от {event.get('user_name')}: добавлена")
        except Exception as exc:
            self.log.appendPlainText('Заявка отклонена: ' + str(exc))
        self.refresh()

    def redemption_updated(self,event):
        if str(event.get('status','')).lower() not in ('canceled','cancelled'):
            return
        redemption=event.get('id')
        if not isinstance(redemption,str) or not redemption:
            return
        row=self.store.get_redemption(redemption)
        reward_id=event.get('reward',{}).get('id')
        if not row and redemption not in getattr(self,'pending_albums',set()) and reward_id not in self.active_reward_limits and (not self.reward or reward_id != self.reward['id']):
            return
        removed=self.store.cancel_redemption(redemption)
        if removed:
            self.finish_deleted_request(removed)
            self.log.appendPlainText('Заявка удалена: награда отменена в Twitch.')

    def playing_track_id(self):
        return getattr(self,'album_track_id',None) or self.current

    def ensure_album_covers(self):
        if self.closing or getattr(self,'album_cover_busy',False):return
        attempts=getattr(self,'album_cover_attempts',set())
        self.album_cover_attempts=attempts
        for album in self.store.albums():
            row=self.store.album_request(album['id'])
            if not row or row['id'] in attempts:continue
            if row['cover_path'] and Path(row['cover_path']).is_file() and Path(row['cover_path']).name==row['id']+'.cover.png':continue
            attempts.add(row['id'])
            self.album_cover_busy=True
            settings=dict(self.cfg)
            def done(_):
                self.album_cover_busy=False
                if not self.closing:self.refresh()
            self.run_async(lambda row=row:load_album_cover(self.store,APP/'tracks',row,settings,cancelled=lambda:self.closing),done,done)
            break

    def review_album(self, album):
        parent=self.store.album_request(album)
        if parent:
            self.review_id=parent['id']
            self.load_rating()

    def import_album(self, url, request=None, metadata=None):
        reward=bool(request)
        key=request.get('redemption') if request else None
        pending=getattr(self,'pending_albums',set())
        self.pending_albums=pending
        if key and key in pending:return
        if not reward and getattr(self,'album_importing',False):return
        if key:pending.add(key)
        if not reward:
            self.album_importing=True
            self.albums_panel.import_button.setEnabled(False)
            self.albums_panel.info.setText('Читаем список альбома…')
        viewer=request.get('viewer','Вручную') if request else (self.viewer.text().strip() or 'Вручную')
        options={k:v for k,v in (request or {}).items() if k!='viewer'}
        def release():
            pending.discard(key)
            if not reward:
                self.album_importing=False
                self.albums_panel.import_button.setEnabled(True)
        def done(metadata):
            release()
            if self.closing:return
            try:
                album,ids=create_album(self.store,APP/'Альбомы',metadata,viewer,**options)
                if options.get('donation'):
                    parent=self.store.album_request(album) if album else None
                    self.store.donation_added(key,parent['id'] if parent else None)
                if not album:return
                for ident in ids:self.down.submit(ident)
                self.refresh()
                if not reward:
                    self.url.clear()
                    self.tabs.setCurrentIndex(4)
                    self.albums_panel.open_album(album)
                    self.albums_panel.info.setText('Альбом добавлен одной заявкой. Его треки скачиваются по порядку.')
                self.log.appendPlainText('Альбом добавлен одной заявкой · '+viewer)
            except Exception as exc:failed(str(exc))
        def failed(error):
            release()
            if options.get('donation'):self.store.donation_retry(key,str(error))
            if reward:self.log.appendPlainText('Альбом отклонён: '+str(error))
            else:
                self.albums_panel.info.setText(str(error))
                if not self.closing:QMessageBox.warning(self,'Альбом',str(error))
        if metadata is not None:done(metadata)
        else:self.run_async(lambda:catalog(url,cancelled=lambda:self.closing,settings=dict(self.cfg)),done,failed)

    def retry_album(self, album):
        parent=self.store.album_request(album)
        if not parent:return
        for row in self.store.album_tracks(album):
            if row['path'] and Path(row['path']).is_file():continue
            if row['status'] in ('Загрузка','Поиск записи','Ожидает загрузки'):continue
            self.store.update(row['id'],status='Ожидает загрузки',error='')
            self.down.submit(row['id'])
        if parent['status'] not in ('Играет','Ждёт оценки','Прослушано','Пропущено'):
            self.store.update(parent['id'],status='Загрузка альбома',error='')
        self.store.update_album_progress(album)
        self.refresh()

    def play_album_at(self, album, index=0):
        parent=self.store.album_request(album)
        tracks=self.store.album_tracks(album)
        if not parent or not tracks or not 0<=index<len(tracks):return
        if not all(r['path'] and Path(r['path']).is_file() for r in tracks):
            self.log.appendPlainText('Альбом ещё не скачан целиком. Открой «Альбомы» и проверь загрузку.')
            return
        if self.current and self.current!=parent['id']:
            self.store.finish(self.current)
        self.player.stop()
        self.current=parent['id']
        self.album_track_id=tracks[index]['id']
        self.album_track_index=index
        row=tracks[index]
        self.store.update(parent['id'],status='Играет')
        self.player.setSource(QUrl.fromLocalFile(row['path']))
        self.now_title.setText(f"[{index+1}/{len(tracks)}] "+row['title'])
        self.now_title.setToolTip(parent['title']+' · '+row['title'])
        self.now_viewer.setText(parent['viewer']+' · '+parent['title'])
        self.show_cover(row)
        self.deck.set_track(row['title'],parent['title'])
        self.player.play()
        self.refresh()
        self.start_lyrics_lookup(row)
        self.ensure_cover(row)

    def choose_local_mp3(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Добавить MP3 с компьютера', '', 'MP3 (*.mp3 *.MP3)')
        if not paths:
            return
        viewer = self.viewer.text().strip() or 'Вручную'
        self.import_mp3_button.setEnabled(False)
        self.import_mp3_button.setText('Импорт…')

        def work():
            added, errors = [], []
            for path in paths:
                if self.closing:
                    break
                try:
                    added.append(import_mp3(self.store, APP / 'tracks', path, viewer,
                                            cancelled=lambda: self.closing))
                except Exception as exc:
                    errors.append(Path(path).name + ': ' + str(exc))
            return added, errors

        def done(result):
            added, errors = result
            self.import_mp3_button.setEnabled(True)
            self.import_mp3_button.setText('+ MP3')
            self.refresh()
            if added:
                self.select_track(added[0])
                self.log.appendPlainText(f'Добавлено MP3: {len(added)} · {viewer}')
            if errors and not self.closing:
                QMessageBox.warning(self, 'Импорт MP3', '\n'.join(errors[:8]) +
                                    (f'\nИ ещё ошибок: {len(errors)-8}' if len(errors)>8 else ''))

        def failed(error):
            self.import_mp3_button.setEnabled(True)
            self.import_mp3_button.setText('+ MP3')
            if not self.closing:
                QMessageBox.warning(self, 'Импорт MP3', error)

        self.run_async(work, done, failed)

    def add_manual(self):
        if album_reference(self.url.text()):
            self.import_album(self.url.text())
            return
        try:
            ident = self.add_request(self.url.text(), self.viewer.text())
            self.url.clear()
            self.submit_request(ident)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, 'Не удалось добавить', str(exc))

    def refresh(self):
        selected = self.selected()
        draft = self.rating_draft() if selected else None
        rows = self.store.rows()
        active = [row for row in rows if row['status'] not in ('Прослушано', 'Пропущено')]
        self.active_count = len(active)
        self.history_count = len(rows)-len(active)
        self.queue_count.setText(f'{self.active_count:02}')
        self.rated_count.setText(f"{sum(row['rating'] is not None for row in rows):02}")
        self.update_section_count()
        self.fill_track_list(self.list, active, selected)
        self.fill_track_list(self.history_list, self.store.rows('history'), selected)
        self.show_download_error()
        self.empty.setVisible(not active)
        self.list.setVisible(bool(active))
        self.history_empty.setVisible(not self.history_count)
        self.history_list.setVisible(bool(self.history_count))
        self.load_rating()
        if draft and self.selected() and self.selected()['id'] == selected['id']:
            self.restore_rating_draft(draft)
        self.refresh_profiles()
        self.refresh_telegram()
        self.albums_panel.refresh()
        self.ensure_album_covers()

    def fill_track_list(self, widget, rows, selected):
        previous = widget.currentItem()
        previous_id = previous.data(Qt.UserRole)['id'] if previous else None
        widget.blockSignals(True)
        widget.clear()
        chosen = None
        for row in rows:
            score = " · " + score_label(row) if row['rating'] is not None else ''
            item = QListWidgetItem(f"{row['status']} · {row['title']}\n{row['viewer']}{score}")
            item.setData(Qt.UserRole, row)
            item.setToolTip('\n\n'.join(x for x in (row['error'], rating_summary(row), row['comment'], row['url']) if x))
            widget.addItem(item)
            if selected and row['id'] == selected['id']:
                chosen = item
            elif chosen is None and row['id'] == previous_id:
                chosen = item
        widget.setCurrentItem(chosen)
        widget.blockSignals(False)

    def update_section_count(self):
        if self.tabs.currentIndex()==4:
            self.queue_summary.setText(f'{len(self.store.albums()):02} АЛЬБОМОВ')
            return
        count = getattr(self, 'history_count', 0) if self.tabs.currentIndex() == 2 else getattr(self, 'active_count', 0)
        self.queue_summary.setText(f'{count:02} ЗАЯВОК')

    def build_history_tab(self):
        page = QWidget()
        page.setObjectName('page')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        info = self.label('Завершённые треки и альбомы с сохранённой оценкой', 'muted')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.history_list = QListWidget()
        self.history_list.setObjectName('queue')
        self.history_list.setSpacing(2)
        self.history_list.setItemDelegate(QueueDelegate(self.history_list))
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history_list.currentItemChanged.connect(self.select_review)
        self.history_list.itemDoubleClicked.connect(self.play_item)
        layout.addWidget(self.history_list, 1)
        self.history_empty = self.label('ИСТОРИЯ ПУСТА\n\nПосле завершения и сохранения оценки заявка появится здесь.', 'muted')
        self.history_empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.history_empty, 1)
        bottom = QHBoxLayout()
        caption = self.label('Двойной щелчок — послушать снова', 'micro')
        caption.setWordWrap(True)
        bottom.addWidget(caption, 1)
        self.button('Вернуть в очередь', self.restore_history, bottom)
        self.button('Удалить', self.delete_selected, bottom)
        layout.addLayout(bottom)
        self.tabs.addTab(page, 'История')

    def restore_history(self):
        item = self.history_list.currentItem()
        row = self.store.get(item.data(Qt.UserRole)['id']) if item else None
        if not row:
            return
        if not row['path'] or not Path(row['path']).is_file():
            QMessageBox.information(self, 'История', 'Аудиофайл недоступен. Добавь ссылку на этот трек заново.')
            return
        try:
            self.store.requeue(row['id'])
            self.refresh()
            self.select_track(row['id'])
        except ValueError as exc:
            QMessageBox.warning(self, 'История', str(exc))

    def add_request(self, text, viewer='Вручную', redemption=None, viewer_id=None, max_duration=MAX_TRACK_SECONDS, reward_title=''):
        if TELEGRAM_PAUSED and telegram_request(text) is not None:
            raise ValueError('Приём из Telegram пока отложен. Укажи ссылку на трек.')
        return self.store.add(text, viewer, redemption, viewer_id, max_duration=max_duration, reward_title=reward_title)

    def submit_request(self, ident):
        if self.store.get(ident)['source'] == 'MP3':
            return
        if self.store.get(ident)['source'] != 'Telegram':
            self.down.submit(ident)
        elif self.telegram and self.telegram.bot_id:
            self.telegram_inbox.match_pending(self.telegram.chat_id, self.telegram.bot_id)

    def configure_private_bot(self):
        if self.telegram_orders and self.telegram_orders.thread.is_alive():
            QMessageBox.information(self, 'Telegram', 'Сначала отключи текущего бота и дождись остановки.')
            return
        if not self.cfg.get('client_id'):
            QMessageBox.information(self, 'Telegram', 'Сначала подключи Twitch через публичный Client ID.')
            return
        token, ok = QInputDialog.getText(self, 'Telegram MP3', 'Вставь токен отдельного бота от @BotFather:',
                                         QLineEdit.Password)
        if not ok or not token.strip():
            return
        try:
            self.start_private_bot(token)
        except (TelegramError, OSError) as exc:
            QMessageBox.warning(self, 'Telegram', str(exc))
            return
        try:
            keyring.set_password('TwitchTune.TelegramMP3', 'bot', token.strip())
        except Exception:
            self.log.appendPlainText('Токен бота не сохранился. Подключение работает до закрытия TwitchTune.')
            return
        self.cfg['telegram_mp3_enabled'] = True
        save_config(APP, self.cfg)

    def start_private_bot(self, token):
        bot = TelegramOrders(token, self.cfg['client_id'], self.telegram_inbox, APP / 'tracks',
                             lambda status: self.incoming.emit('private_bot', (bot, status)),
                             lambda: self.telegram_reward_options)
        self.telegram_orders = bot
        self.telegram_clients.append(bot)
        self.private_bot_status.setText('Подключение бота…')
        bot.start()

    def start_private_bot_if_ready(self):
        if not self.cfg.get('telegram_mp3_enabled') or not self.cfg.get('client_id'):
            return
        try:
            token = keyring.get_password('TwitchTune.TelegramMP3', 'bot')
            if token:
                self.start_private_bot(token)
                return
        except Exception:
            pass
        self.private_bot_status.setText('Подключи бота заново в настройках.')

    def stop_private_bot(self):
        if self.telegram_orders:
            self.telegram_orders.close()
        self.cfg['telegram_mp3_enabled'] = False
        save_config(APP, self.cfg)
        self.private_bot_status.setText('Бот отключён')

    def build_telegram_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()
        self.telegram_status = QLabel('Telegram не подключён')
        self.telegram_status.setWordWrap(True)
        top.addWidget(self.telegram_status, 1)
        self.button('Настроить бота', self.configure_telegram, top)
        self.button('Отключить', self.disconnect_telegram, top)
        layout.addLayout(top)
        help_text = QLabel('Новый зритель: MP3 в чат → код от бота → заказ «в тг TT-…».\n'
                          'После привязки достаточно «в тг», если есть ровно один свежий MP3 и одна заявка.')
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.telegram_files = QListWidget()
        layout.addWidget(self.telegram_files, 1)
        bottom = QHBoxLayout()
        self.telegram_pending = QComboBox()
        self.telegram_pending.setMinimumWidth(230)
        self.telegram_pending.setPlaceholderText('Заявки, ожидающие Telegram')
        bottom.addWidget(self.telegram_pending, 1)
        self.button('Назначить MP3', self.assign_telegram, bottom)
        self.button('Повторить загрузку', self.retry_telegram, bottom)
        layout.addLayout(bottom)
        self.button('Сбросить Telegram-привязку выбранного в очереди зрителя', self.unbind_telegram, layout)
        self.tabs.addTab(tab, '03 / Telegram MP3')

    def refresh_telegram(self):
        selected = self.telegram_files.currentItem()
        code = selected.data(Qt.UserRole) if selected else None
        self.telegram_files.clear()
        client = self.telegram
        files = self.telegram_inbox.files(client.chat_id, client.bot_id) if client and client.bot_id else self.telegram_inbox.files()
        for file in files:
            item = QListWidgetItem(f"{file['code']} · {file['title']}\n{file['nickname']} · {file['status']} · {file['file_size'] / 1048576:.1f} МБ")
            item.setData(Qt.UserRole, file['code'])
            item.setToolTip(file['error'] or file['title'])
            self.telegram_files.addItem(item)
            if file['code'] == code:
                self.telegram_files.setCurrentItem(item)
        pending_id = self.telegram_pending.currentData()
        self.telegram_pending.clear()
        for row in self.store.rows():
            if row['source'] == 'Telegram' and row['status'] == 'Ожидает Telegram':
                self.telegram_pending.addItem(row['viewer'] + ' · ' + row['title'], row['id'])
        index = self.telegram_pending.findData(pending_id)
        if index >= 0:
            self.telegram_pending.setCurrentIndex(index)

    def configure_telegram(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Telegram · подключение чата')
        dialog.resize(660, 380)
        layout = QVBoxLayout(dialog)
        hint = QLabel('Создай отдельного бота через @BotFather и добавь его в чат.\n'
                      'Сделай его администратором или отключи Group Privacy и добавь заново.\n'
                      'Для поиска ID отправь в группе /twitchtune_chat и нажми «Найти чаты».')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        token = QLineEdit()
        token.setEchoMode(QLineEdit.Password)
        try:
            token.setText(keyring.get_password('TwitchTune.Telegram', 'bot') or '')
        except Exception:
            pass
        chat = QLineEdit(str(self.cfg.get('telegram_chat', '')))
        chat.setPlaceholderText('-1001234567890 или @имя_публичной_группы')
        announce = QCheckBox('Отвечать кодом трека под MP3 в этом чате')
        announce.setChecked(self.cfg.get('telegram_announce', True))
        form.addRow('Токен бота', token)
        form.addRow('Чат', chat)
        form.addRow(announce)
        layout.addLayout(form)
        discovered = QComboBox()
        discovered.setPlaceholderText('Найденные чаты')
        discovered.activated.connect(lambda i: chat.setText(discovered.itemData(i)))
        line = QHBoxLayout()
        line.addWidget(discovered, 1)
        find_button = self.button('Найти чаты', lambda: find_chats(), line)
        layout.addLayout(line)
        info = QLabel('Скачивание MP3 до 20 МБ и 15 минут. Программа должна быть открыта.')
        info.setWordWrap(True)
        layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Подключить')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def found(chats):
            find_button.setEnabled(True)
            discovered.clear()
            for ident, name in chats.items():
                discovered.addItem(name + ' · ' + ident, ident)
            if discovered.count():
                chat.setText(discovered.itemData(0))
            else:
                info.setText('Чат не найден: добавь бота, отправь /twitchtune_chat в группе и повтори поиск.')

        def failed(error):
            find_button.setEnabled(True)
            info.setText(str(error))

        def find_chats():
            if self.telegram and self.telegram.thread and self.telegram.thread.is_alive():
                info.setText('Сначала отключи Telegram во вкладке, затем запусти поиск чатов.')
                return
            try:
                client = TelegramClient(token.text(), '', self.telegram_inbox, APP / 'tracks', self.incoming.emit)
                find_button.setEnabled(False)
                self.run_async(client.discover_chats, found, failed)
            except Exception as exc:
                failed(str(exc))

        if dialog.exec() != QDialog.Accepted:
            return
        try:
            value = chat.text().strip()
            if not value:
                raise ValueError('Укажи ID или @имя Telegram-группы.')
            # Validate before changing the previous configuration.
            client = TelegramClient(token.text(), value, self.telegram_inbox, APP / 'tracks', self.incoming.emit)
            persisted = False
            try:
                keyring.set_password('TwitchTune.Telegram', 'bot', token.text().strip())
                persisted = True
            except Exception:
                self.log.appendPlainText('Токен Telegram не сохранён системой. Подключение доступно до закрытия программы.')
            self.cfg.update(telegram_chat=value, telegram_announce=announce.isChecked(), telegram_enabled=persisted)
            save_config(APP, self.cfg)
            self.start_telegram(client.token, value, announce.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, 'Подключение Telegram', str(exc))

    def telegram_if_ready(self):
        if TELEGRAM_PAUSED:
            return
        if not self.cfg.get('telegram_enabled') or not self.cfg.get('telegram_chat'):
            return
        try:
            token = keyring.get_password('TwitchTune.Telegram', 'bot')
            if token:
                self.start_telegram(token, self.cfg['telegram_chat'], self.cfg.get('telegram_announce', True))
            else:
                self.telegram_status.setText('Укажи токен бота в настройках Telegram.')
        except Exception:
            self.telegram_status.setText('Не удалось прочитать токен Telegram. Открой настройки бота.')

    def start_telegram(self, token, chat_id, announce):
        if self.telegram:
            self.telegram.close()
        client = TelegramClient(token, chat_id, self.telegram_inbox, APP / 'tracks',
                                lambda kind, value: self.incoming.emit('telegram', (client, kind, value)), announce)
        self.telegram = client
        self.telegram_clients.append(client)
        self.telegram_status.setText('Подключение Telegram…')
        client.start()

    def disconnect_telegram(self):
        if self.telegram:
            self.telegram.close()
        self.telegram = None
        self.cfg['telegram_enabled'] = False
        save_config(APP, self.cfg)
        self.telegram_status.setText('Telegram отключён')

    def assign_telegram(self):
        item, ident = self.telegram_files.currentItem(), self.telegram_pending.currentData()
        if not self.telegram or not self.telegram.bot_id or not item or not ident:
            QMessageBox.information(self, 'Telegram', 'Подключи бота, выбери MP3 и ожидающую заявку.')
            return
        try:
            self.telegram_inbox.assign(ident, item.data(Qt.UserRole), self.telegram.chat_id, self.telegram.bot_id)
            self.refresh()
        except ValueError as exc:
            QMessageBox.warning(self, 'Сопоставление MP3', str(exc))

    def retry_telegram(self):
        item = self.telegram_files.currentItem()
        file = self.telegram_inbox.get(item.data(Qt.UserRole)) if item else None
        row = self.store.get(file['used_by']) if file and file['used_by'] else None
        if row and row['status'] == 'Ошибка Telegram':
            self.store.update(row['id'], status='Ожидает загрузки', error='')
            self.telegram_inbox.set_file(file['code'], status='Ожидает загрузки', error='')
            self.refresh()

    def unbind_telegram(self):
        row = self.selected()
        if row:
            self.telegram_inbox.unbind(row['viewer_key'])
            self.log.appendPlainText('Сброшена Telegram-привязка: ' + row['viewer'])
        else:
            QMessageBox.information(self, 'Telegram', 'Сначала выбери заявку нужного зрителя во вкладке очереди.')

    def select_review(self, item, *_):
        if item:
            self.review_id = item.data(Qt.UserRole)['id']
            self.load_rating()

    def selected(self):
        return self.store.get(self.review_id) if self.review_id else None

    def queue_selected(self):
        item = self.list.currentItem()
        return self.store.get(item.data(Qt.UserRole)['id']) if item else None

    def show_download_error(self, *_):
        row = self.queue_selected()
        error = row.get('error', '') if row else ''
        self.download_error_box.setVisible(bool(error))
        self.download_error.setText('Не удалось загрузить трек\n' + error)
        self.yandex_settings_button.setVisible(bool(row and row['source'] == 'Яндекс Музыка'))
        self.vk_settings_button.setVisible(bool(row and row['source'] == 'VK'))

    def open_vk_settings(self):
        self.tabs.setCurrentIndex(3)
        self.settings_scroll.ensureWidgetVisible(self.vk_settings)

    def load_rating(self, *_):
        row = self.selected()
        title = (row['viewer'] + ' · ' + row['title']) if row else 'Выбери трек в очереди или истории'
        self.rating_target.setText(title if len(title) <= 75 else title[:72] + '…')
        self.rating_target.setToolTip(title)
        value = details(row) if row else None
        self.restore_rating_draft((value['scores'] if value else {},
                                   value['text_enabled'] if value else not row or row.get('submitted_lyrics') != '',
                                   row['comment'] if row else ''))

    def save_rating(self):
        row = self.selected()
        if not row:
            return
        try:
            scores, enabled, comment = self.rating_draft()
            total = self.store.rate_criteria(row['id'], scores, enabled, comment)
        except ValueError as exc:
            QMessageBox.warning(self, 'Оценка трека', str(exc))
            return
        self.refresh()
        self.log.appendPlainText(f"Сохранено: {row['viewer']} · {total}/66")

    def refresh_profiles(self):
        previous = self.profiles.currentItem()
        key = previous.data(Qt.UserRole)['viewer_key'] if previous else None
        self.profiles.blockSignals(True)
        self.profiles.clear()
        for profile in self.store.profiles():
            average = f"Средний балл: {profile['average66']:.2f}/66" if profile['average66'] is not None else 'нет оценок'
            vip = f" · VIP {max(0,3-profile['vip_used'])}/3" if profile['vip'] else ''
            provider=profile['viewer_key'].split(':')[1] if profile['viewer_key'].startswith('donor:') else ''
            if provider:vip+=' · '+('DonationAlerts' if provider=='donationalerts' else 'DonateX')
            item = QListWidgetItem(f"{profile['nickname']}{vip} · {average}\nЗаявок: {profile['total']} · Оценено: {profile['rated']}")
            item.setData(Qt.UserRole, profile)
            self.profiles.addItem(item)
            if profile['viewer_key'] == key:
                self.profiles.setCurrentItem(item)
        self.profiles.blockSignals(False)
        if not self.profiles.currentItem() and self.profiles.count():
            self.profiles.setCurrentRow(0)
        else:
            self.show_profile()

    def show_profile(self, *_):
        self.profile_history.clear()
        item = self.profiles.currentItem()
        profile=item.data(Qt.UserRole) if item else None
        self.vip_grant_button.setEnabled(bool(profile and not profile['vip']))
        self.vip_remove_button.setEnabled(bool(profile and profile['vip']))
        self.vip_reset_button.setEnabled(bool(profile and profile['vip']))
        if profile:
            self.vip_info.setText(f"{profile['nickname']} · VIP: осталось {max(0,3-profile['vip_used'])}/3 приоритетных трека" if profile['vip'] else profile['nickname']+' · обычная очередь')
        else:
            self.vip_info.setText('VIP TwitchTune: 3 новых трека с приоритетом. Выбери зрителя или добавь его по нику.')
        if not item:
            return
        for row in self.store.viewer_tracks(item.data(Qt.UserRole)['viewer_key']):
            score = score_label(row)
            title = ('Альбом · ' if row['source']=='Альбом' else '') + row['title']
            text = title + ' · ' + score + '\n' + row['status']
            if row['comment']:
                text += '\n«' + row['comment'] + '»'
            track = QListWidgetItem(text)
            track.setData(Qt.UserRole, row['id'])
            self.profile_history.addItem(track)

    def change_vip(self,action):
        item=self.profiles.currentItem()
        if not item:return
        profile=item.data(Qt.UserRole)
        self.store.set_vip(profile['viewer_key'],action!='remove',reset=action=='reset')
        self.refresh()

    def grant_vip_by_name(self):
        nickname,ok=QInputDialog.getText(self,'VIP TwitchTune','Ник зрителя в Twitch:')
        if not ok:return
        try:
            key=self.store.grant_vip(nickname)
        except ValueError as exc:
            QMessageBox.warning(self,'VIP TwitchTune',str(exc))
            return
        self.refresh()
        for index in range(self.profiles.count()):
            if self.profiles.item(index).data(Qt.UserRole)['viewer_key']==key:
                self.profiles.setCurrentRow(index);break

    def select_history_track(self, item):
        self.select_track(item.data(Qt.UserRole))

    def select_track(self, ident):
        row = self.store.get(ident)
        if not row:
            return
        archived = row['status'] in ('Прослушано', 'Пропущено')
        self.tabs.setCurrentIndex(2 if archived else 0)
        widget = self.history_list if archived else self.list
        for index in range(widget.count()):
            track = widget.item(index)
            if track.data(Qt.UserRole)['id'] == ident:
                widget.setCurrentItem(track)
                widget.scrollToItem(track)
                self.select_review(track)
                return

    def find_version(self):
        row = self.queue_selected()
        if not row:
            return
        if row['source']=='Альбом':
            self.tabs.setCurrentIndex(4)
            self.albums_panel.open_album(row['album_id'])
            return
        if row['id'] == self.current or row['status'] == 'Загрузка':
            return
        def resolve():
            metadata = yandex_metadata(row['url'])
            self.store.update_download(row['id'], **metadata)
            self.incoming.emit('queue', None)
            return metadata
        query = replacement_query(row)
        resolver = resolve if not query and row['source'] == 'Яндекс Музыка' else None
        dialog = SearchDialog(query, self, row['url'], resolver)
        if dialog.exec() and dialog.choice:
            self.store.update(row['id'], replacement=dialog.choice['url'], title=dialog.choice['title'],
                              status='Ожидает загрузки', error='', path='', artist='', track='', duration=0)
            self.down.submit(row['id'])
            self.refresh()

    def move(self, delta):
        row = self.queue_selected()
        if row:
            self.store.move(row['id'], delta)
            self.refresh()

    def retry_selected(self):
        row = self.queue_selected()
        if row and row['source']=='Альбом':
            self.retry_album(row['album_id'])
            return
        if not row or row['status'] not in ('Нужна замена', 'Ошибка Яндекса', 'Нужен вход Яндекс', 'Ошибка Telegram', 'Ошибка SoundCloud'):
            return
        if row['source'] == 'Telegram':
            return
        values = dict(status='Ожидает загрузки', error='')
        if row['source'] == 'Яндекс Музыка':
            values['replacement'] = ''
        self.store.update(row['id'], **values)
        self.down.submit(row['id'])
        self.refresh()

    def delete_selected(self):
        widget = self.history_list if self.tabs.currentIndex() == 2 else self.list
        item = widget.currentItem()
        row = self.store.get(item.data(Qt.UserRole)['id']) if item else None
        self.confirm_delete_request(row)

    def delete_album(self, album):
        self.confirm_delete_request(self.store.album_request(album))

    def confirm_delete_request(self, row):
        if not row:
            return
        is_album=row['source']=='Альбом'
        answer = QMessageBox.question(self, 'Удалить альбом полностью?' if is_album else 'Удалить трек полностью?',
            row['title'] + ('\n\nУдалить весь альбом, все его песни и обложки, заявку, общую оценку и комментарий?'
                            if is_album else '\n\nУдалить заявку, оценку, комментарий и скачанный файл?')+
                            '\nЗаявка также исчезнет из профиля зрителя.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.store.delete(row['id'])
        self.finish_deleted_request(row)

    def finish_deleted_request(self,row):
        playing = self.current == row['id']
        if playing:
            self.clear_current()
        if self.review_id == row['id']:
            self.review_id = None
        if self.lyrics_window and (self.lyrics_window.row['id'] == row['id'] or
                (row['source']=='Альбом' and self.lyrics_window.row.get('album_id')==row['album_id'])):
            self.lyrics_window.reject()
        folder = track_folder(APP/'tracks', row)
        if row['source']=='Альбом':
            import shutil
            try:
                shutil.rmtree(folder)
            except FileNotFoundError:
                pass
            except OSError:
                self.log.appendPlainText('Папка альбома пока занята и осталась на диске.')
            failed = []
        else:
            failed = remove_track_files(folder, row['id'], row['path'])
        if failed:
            def retry_cleanup():
                remaining = remove_track_files(folder, row['id'], row['path'])
                if remaining and not self.closing:
                    self.log.appendPlainText('Заявка удалена, но её файл пока занят другой программой и остался в папке tracks.')
            QTimer.singleShot(800, retry_cleanup)
        if playing:
            self.next_track()
        self.refresh()
        self.log.appendPlainText('Удалён трек: ' + row['title'])

    def play_item(self, item):
        self.play_row(self.store.get(item.data(Qt.UserRole)['id']))

    def play_row(self, row):
        if row and row['source']=='Альбом':
            if row['id']==self.current:
                self.player.play()
            else:
                self.play_album_at(row['album_id'],0)
            return
        self.album_track_id = None
        if not row or not row['path']:
            return
        if not Path(row['path']).is_file():
            self.log.appendPlainText('Аудиофайл не найден: ' + row['path'])
            return
        if self.current == row['id']:
            self.player.play()
            return
        if self.current:
            self.store.finish(self.current, 'Прослушано')
        self.player.stop()
        self.current = row['id']
        self.store.update(row['id'], status='Играет')
        self.player.setSource(QUrl.fromLocalFile(row['path']))
        self.now_title.setText(row['title'])
        self.now_title.setToolTip(row['title'])
        self.now_viewer.setText('от ' + row['viewer'])
        self.show_cover(row)
        self.deck.set_track(row['title'], row['viewer'])
        self.player.play()
        self.refresh()
        self.start_lyrics_lookup(self.store.get(row['id']))
        self.ensure_cover(self.store.get(row['id']))

    def toggle(self):
        if not self.current:
            selected = self.selected()
            if selected and selected['path']:
                self.play_row(selected)
            else:
                self.next_track()
        elif self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def media_status(self, status):
        if status == QMediaPlayer.EndOfMedia and self.current:
            row = self.store.get(self.current)
            if row and row['source']=='Альбом':
                tracks=self.store.album_tracks(row['album_id'])
                index=getattr(self,'album_track_index',0)+1
                if index<len(tracks):
                    self.play_album_at(row['album_id'],index)
                    return
            self.finish_current('Прослушано',advance=False)

    def finish_current(self, status,advance=True):
        if self.current:
            self.store.finish(self.current, status)
        self.clear_current()
        if advance:self.next_track()
        else:self.now_viewer.setText('Воспроизведение остановлено. Следующий трек — вручную.')
        self.refresh()

    def clear_current(self):
        self.current = None
        self.waiting_paid = False
        self.album_track_id = None
        self.lyrics_generation += 1
        self.lyrics_candidates = None
        self.lyrics_button.setText('Текст песни')
        self.lyrics_button.setEnabled(False)
        self.player.stop()
        self.player.setSource(QUrl())
        self.now_title.setText('Ничего не играет')
        self.now_viewer.setText('Очередь готовых треков закончилась')
        self.show_cover(None)
        self.deck.set_track('NO TRACK LOADED', 'Ожидание трека')

    def next_track(self):
        self.waiting_paid=False
        for row in self.store.rows():
            if row.get('donation_priority') and row['status'] in ('Ожидает загрузки','Загрузка','Поиск записи','Поиск замены','Загрузка альбома'):
                self.waiting_paid=True
                self.now_viewer.setText('Ожидаем загрузку донатного заказа')
                return
            if row['status'] == 'Готово' and row['path'] and Path(row['path']).is_file():
                self.play_row(row)
                return

    def skip_current(self):
        if self.current:
            self.finish_current('Пропущено')
        else:
            self.next_track()

    def skip_selected(self):
        row = self.queue_selected()
        if not row:
            return
        if row['id'] == self.current:
            self.skip_current()
        else:
            self.store.finish(row['id'], 'Пропущено')
            self.refresh()

    def previous(self):
        current = self.store.get(self.current) if self.current else None
        if current and current['source']=='Альбом' and getattr(self,'album_track_index',0)>0:
            self.play_album_at(current['album_id'],self.album_track_index-1)
            return
        rows = self.store.rows()
        index = next((i for i, row in enumerate(rows) if row['id'] == self.current), len(rows))
        for row in reversed(rows[:index]):
            if row['path'] and row['status'] != 'Пропущено' and Path(row['path']).is_file():
                self.play_row(row)
                return

    def position_changed(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.elapsed.setText(clock(position))
        self.deck.set_position(position)

    def duration_changed(self, duration):
        self.seek.setRange(0, duration)
        self.duration.setText(clock(duration))

    def set_volume(self, value):
        self.audio.setVolume(value / 100)
        self.volume_label.setText(f'{value}%')
        self.cfg['volume'] = value
        save_config(APP, self.cfg)

    def run_async(self, fn, done, failed=None):
        thread = QThread(self)
        worker = Worker(fn)
        worker.moveToThread(thread)
        self.jobs.append((thread, worker))
        thread.started.connect(worker.run)
        worker.done.connect(lambda result: self.incoming.emit('callback', (done, result)))
        worker.failed.connect(lambda error: self.incoming.emit('callback', (failed or self.async_error, error)))
        worker.done.connect(thread.quit, Qt.DirectConnection)
        worker.failed.connect(thread.quit, Qt.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(lambda: self.incoming.emit('job_finished', thread))
        thread.start()

    def release_job(self, thread):
        if not any(t is thread for t, _ in self.jobs):
            return
        # finished can precede native thread-local cleanup. Keep the QThread
        # alive until it has joined, especially with concurrent artwork jobs.
        if not thread.wait(0):
            QTimer.singleShot(10, lambda: self.release_job(thread))
            return
        self.jobs = [(t, w) for t, w in self.jobs if t is not thread]
        thread.deleteLater()

    def async_error(self, error):
        self.log.appendPlainText('Ошибка: ' + error)

    def closeEvent(self, event):
        if not self.closing:
            self.closing = True
            if self.client:
                self.client.close()
            for client in self.telegram_clients:
                client.close()
            self.donation_settings.close_clients()
            self.down.stop.set()
            self.player.stop()
        if self.jobs or self.donation_settings.alive() or any(c.thread and c.thread.is_alive() for c in self.telegram_clients):
            event.ignore()
            self.status.setText('Завершение фоновых операций…')
            self.setEnabled(False)
            QTimer.singleShot(200, self.close)
            return
        super().closeEvent(event)


if __name__ == '__main__':
    try:
        smoke_test='--smoke-test' in sys.argv
        if smoke_test:sys.argv.remove('--smoke-test')
        health_file=None
        if '--update-health-file' in sys.argv:
            index=sys.argv.index('--update-health-file')
            health_file=sys.argv[index+1]
            del sys.argv[index:index+2]
        if sys.platform == 'win32':
            import ctypes
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('TwitchTune.AcidDeck')
            except (AttributeError, OSError):
                pass
        app = QApplication(sys.argv)
        app.setApplicationName('TwitchTune')
        app.setWindowIcon(QIcon(str(APP_ICON)))
        from desktop_runtime import acquire_lock
        try:
            instance_lock=acquire_lock(APP/'runtime.lock')
        except BlockingIOError:
            QMessageBox.information(None,'TwitchTune','TwitchTune уже запущен или сейчас обновляется. Открой существующее окно.')
            sys.exit(0)
        window = Main(smoke_test=smoke_test)
        window.show()
        if health_file:
            from binary_updates import write_health_marker
            from release_version import VERSION
            QTimer.singleShot(1200,lambda:write_health_marker(health_file,APP,VERSION))
        if smoke_test:QTimer.singleShot(3000,window.close)
        sys.exit(app.exec())
    except Exception:
        error = traceback.format_exc()
        try:
            APP.mkdir(exist_ok=True)
            (APP / 'startup_error.txt').write_text(error, encoding='utf-8')
        except Exception:
            pass
        try:
            QMessageBox.critical(None, 'TwitchTune не запустился',
                'Подробности сохранены в:\n' + str(APP / 'startup_error.txt') + '\n\n' + error[-1200:])
        except Exception:
            pass
        raise
