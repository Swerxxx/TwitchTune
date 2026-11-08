"""Music reward cards and second-accurate, independently persisted limits."""
import re
from limits import MAX_TRACK_SECONDS
from settings_wheel import SettingsWheelGuard
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QWidget, QCheckBox, QSpinBox, QDialogButtonBox, QFrame)


def guess_limit(title):
    match = re.search(r'\b(\d{1,2})\s*(?:мин(?:ут[аыу]?)?|minutes?|min)\b', title, re.I)
    return int(match[1])*60 if match and 1 <= int(match[1]) <= 90 else MAX_TRACK_SECONDS


def limit_text(seconds):
    minutes, seconds = divmod(seconds, 60)
    return f'{minutes} мин {seconds:02} сек'


class DurationDigits(QSpinBox):
    def textFromValue(self, value):
        return f'{value:02}'


class DurationControl(QWidget):
    """Edit 1..5400 seconds without rounding existing saved limits to minutes."""
    def __init__(self, value, reward_title, parent=None):
        super().__init__(parent)
        self.setObjectName('durationControl')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.wheel_guard = SettingsWheelGuard(self)
        self.minutes = DurationDigits()
        self.minutes.setRange(0, 90)
        self.seconds = DurationDigits()
        self.seconds.setRange(0, 59)
        for field, caption in ((self.minutes, 'мин'), (self.seconds, 'сек')):
            column = QVBoxLayout()
            column.setSpacing(3)
            field.setObjectName('durationDigits')
            self.wheel_guard.protect(field)
            field.setToolTip('Введи число или используй клавиши ↑ / ↓.')
            field.setAlignment(Qt.AlignCenter)
            field.setFixedWidth(82)
            field.setAccessibleName(f'{reward_title}: {"минуты" if caption == "мин" else "секунды"}')
            field.setKeyboardTracking(False)
            column.addWidget(field)
            label = QLabel(caption)
            label.setObjectName('micro')
            label.setAlignment(Qt.AlignCenter)
            label.setBuddy(field)
            column.addWidget(label)
            layout.addLayout(column)
        self.setValue(value)
        self.minutes.valueChanged.connect(self.update_seconds_range)
        self.setToolTip('Лимит от 0 мин 01 сек до 90 мин 00 сек')

    def update_seconds_range(self, *_):
        self.seconds.setRange(1 if self.minutes.value() == 0 else 0,
                              0 if self.minutes.value() == 90 else 59)

    def setValue(self, value):
        value = max(1, min(MAX_TRACK_SECONDS, int(value)))
        self.minutes.setValue(value//60)
        self.update_seconds_range()
        self.seconds.setValue(value%60)

    def value(self):
        # Commit typed values even when Save is activated from the keyboard.
        self.minutes.interpretText()
        self.seconds.interpretText()
        return self.minutes.value()*60 + self.seconds.value()


class RewardDialog(QDialog):
    def __init__(self, rewards, selected, parent=None):
        super().__init__(parent)
        self.setWindowTitle('TwitchTune · Музыкальные награды')
        self.setObjectName('rewardDialog')
        self.resize(780, 660)
        self.setMinimumSize(640, 460)
        self.controls = {}
        self.cards = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        tag = QLabel('ACID DECK  /  TWITCH REWARDS')
        tag.setObjectName('section')
        heading.addWidget(tag)
        title = QLabel('Музыкальные награды')
        title.setObjectName('rewardHeading')
        heading.addWidget(title)
        header.addLayout(heading, 1)
        badge = QLabel('ON AIR\nPOINTS → MUSIC')
        badge.setObjectName('rewardBadge')
        badge.setAlignment(Qt.AlignCenter)
        header.addWidget(badge)
        layout.addLayout(header)
        hint = QLabel('Выбери награды для заказа музыки и задай лимит каждой.\nВсе выбранные награды принимаются одновременно.')
        hint.setWordWrap(True)
        hint.setObjectName('muted')
        layout.addWidget(hint)
        section = QHBoxLayout()
        caption = QLabel('ПРИЁМ ЗАКАЗОВ')
        caption.setObjectName('micro')
        section.addWidget(caption)
        section.addStretch()
        self.count_label = QLabel()
        self.count_label.setObjectName('section')
        section.addWidget(self.count_label)
        layout.addLayout(section)
        scroll = QScrollArea()
        scroll.setObjectName('rewardScroll')
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName('rewardList')
        rows = QVBoxLayout(body)
        rows.setContentsMargins(0, 0, 6, 0)
        rows.setSpacing(10)
        for number, reward in enumerate(rewards, 1):
            ident, title = reward['id'], reward['title']
            card = QFrame()
            card.setObjectName('rewardCard')
            row = QHBoxLayout(card)
            row.setContentsMargins(16, 16, 16, 16)
            row.setSpacing(14)
            check = QCheckBox()
            check.setObjectName('rewardCheck')
            check.setAccessibleName('Принимать награду: ' + title)
            check.setToolTip('Принимать награду: ' + title)
            check.setChecked(ident in selected)
            row.addWidget(check)
            text = QVBoxLayout()
            text.setSpacing(7)
            label = QLabel(title)
            label.setObjectName('rewardTitle')
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            text.addWidget(label)
            cost = reward.get('cost')
            meta = QLabel(f'НАГРАДА {number:02}' + (f'  ·  {cost:,} баллов'.replace(',', ' ') if type(cost) is int else ''))
            meta.setObjectName('micro')
            text.addWidget(meta)
            row.addLayout(text, 1)
            duration_column = QVBoxLayout()
            duration_column.setSpacing(5)
            caption = QLabel('МАКС. ДЛИТЕЛЬНОСТЬ')
            caption.setObjectName('micro')
            caption.setAlignment(Qt.AlignCenter)
            duration_column.addWidget(caption)
            limit = selected.get(ident, guess_limit(title))
            if type(limit) is not int or not 1 <= limit <= MAX_TRACK_SECONDS:
                limit = guess_limit(title)
            duration = DurationControl(limit, title)
            duration_column.addWidget(duration)
            row.addLayout(duration_column)
            rows.addWidget(card)
            self.controls[ident] = (check, duration)
            self.cards[ident] = card
            check.toggled.connect(lambda checked, key=ident: self.update_card(key))
        if not rewards:
            empty = QLabel('Музыкальные награды пока не найдены.')
            empty.setObjectName('muted')
            empty.setWordWrap(True)
            rows.addWidget(empty)
        rows.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        footer = QHBoxLayout()
        note = QLabel('Лимит: от 0:01 до 15:00')
        note.setObjectName('micro')
        footer.addWidget(note)
        footer.addStretch()
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save = self.buttons.button(QDialogButtonBox.Save)
        save.setText('Сохранить награды')
        save.setObjectName('primary')
        self.buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)
        for ident in self.controls:
            self.update_card(ident)
        self.update_count()

    def update_card(self, ident):
        check, duration = self.controls[ident]
        duration.setEnabled(check.isChecked())
        card = self.cards[ident]
        card.setProperty('selected', check.isChecked())
        card.style().unpolish(card)
        card.style().polish(card)
        self.update_count()

    def update_count(self):
        count = sum(check.isChecked() for check, _ in self.controls.values())
        self.count_label.setText(f'Выбрано: {count:02}')
        self.buttons.button(QDialogButtonBox.Save).setEnabled(count > 0)

    def selection(self):
        return {ident: duration.value() for ident, (check, duration) in self.controls.items() if check.isChecked()}
