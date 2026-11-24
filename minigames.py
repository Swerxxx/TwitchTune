"""Offline quizzes for the TwitchTune settings tab."""

from dataclasses import dataclass
from random import sample

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


IQ_TITLE = 'Тест на IQ'
ANIME_TITLE = 'Угадай аниме персонажа'
IQ_CLOSE_THRESHOLD = 70
IQ_CLOSE_DELAY_SECONDS = 5


@dataclass(frozen=True)
class Question:
    prompt: str
    answer: str
    distractors: tuple[str, ...]
    series: str = ''


# Russian adaptations of six public-domain ICAR-16 sample items.
IQ_QUESTIONS = (
    Question('Найди одну пятую от четверти одной девятой числа 900.',
             '5', ('2', '4', '6')),
    Question('Захар выше Матвея, а Роман ниже Захара. Как соотносятся рост Романа и рост Матвея?',
             'Определить невозможно', ('Роман выше Матвея', 'Роман ниже Матвея', 'Они одного роста')),
    Question('Илье 12 лет, а его сестра втрое старше. Сколько ей будет лет, когда Илье исполнится 23?',
             '47', ('39', '44', '53')),
    Question('Послезавтра будет день, который наступает за два дня до четверга. Какой сегодня день?',
             'Понедельник', ('Вторник', 'Среда', 'Воскресенье')),
    Question('Продолжи ряд букв латинского алфавита.\nV, Q, M, J, H, …',
             'G', ('E', 'F', 'I')),
    Question('Продолжи ряд букв латинского алфавита.\nI, J, L, O, S, …',
             'X', ('T', 'Y', 'Z')),
)


# Clues are written for TwitchTune using the Fandom references in QUICK_START.md.
ANIME_CASTS = (
    ('Наруто', (
        ('Наруто Удзумаки', 'Этот ниндзя в оранжевом мечтает стать хокагэ. Если дело дошло до драки, на помощь придёт целая толпа его теневых клонов.'),
        ('Сакура Харуно', 'Участница команды № 7 с розовыми волосами. Училась у Цунадэ лечить раны и бить так, что трескается земля.'),
        ('Какаси Хатакэ', 'Наставник команды № 7 прячет лицо под маской, а шаринган прикрывает повязкой.'),
    )),
    ('One Piece', (
        ('Монки Д. Луффи', 'Капитан в соломенной шляпе. Может растянуть руку через полпалубы и собирается стать Королём пиратов.'),
        ('Ророноа Зоро', 'Этот мечник берёт по клинку в каждую руку, а для третьего освобождает зубы.'),
        ('Нами', 'Штурман команды Соломенной Шляпы мечтает нарисовать карту всего мира.'),
    )),
    ('Клинок, рассекающий демонов', (
        ('Тандзиро Камадо', 'Его нос улавливает даже запах демона. За меч он взялся, чтобы найти способ вернуть сестре человеческий облик.'),
        ('Нэдзуко Камадо', 'После превращения в демона она путешествует в деревянном ящике брата и носит бамбуковый намордник.'),
        ('Дзэницу Агацума', 'Часто паникует перед боем, зато с Дыханием грома наносит удар быстрее, чем успеешь моргнуть.'),
    )),
    ('Атака титанов', (
        ('Эрен Йегер', 'Парень из Сигансины мечтает увидеть мир за стенами. После гибели матери он клянётся истребить титанов.'),
        ('Микаса Аккерман', 'В бою эта разведчица носит красный шарф, который ей подарил Эрен.'),
        ('Леви Аккерман', 'Капитан Разведкорпуса одинаково нетерпим к титанам и к пыли на полках.'),
    )),
    ('Тетрадь смерти', (
        ('Лайт Ягами', 'Отличник находит тетрадь, в которую достаточно вписать имя, чтобы человек умер. Мир узнаёт его как Киру.'),
        ('L', 'Детектив расследует дело Киры. Своё имя держит в секрете, а следственная группа зовёт его Рюдзаки.'),
        ('Рюк', 'Бог смерти из скуки роняет тетрадь в мир людей. Из человеческой еды его интересуют прежде всего яблоки.'),
    )),
)

ANIME_QUESTIONS = tuple(
    Question(clue, name, tuple(other for other, _ in cast if other != name), series)
    for series, cast in ANIME_CASTS
    for name, clue in cast
)


class QuizDialog(QDialog):
    def __init__(self, parent, *, iq_test=False):
        super().__init__(parent)
        bank = IQ_QUESTIONS if iq_test else ANIME_QUESTIONS
        self.questions = sample(bank, len(bank))
        self.iq_test = iq_test
        self.index = 0
        self.correct = 0
        self.exit_after_quiz = False
        self.setWindowTitle(IQ_TITLE if iq_test else ANIME_TITLE)
        self.setMinimumWidth(440)
        self.resize(520, 365)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        self.heading = QLabel()
        self.heading.setObjectName('section')
        layout.addWidget(self.heading)
        if iq_test:
            intro = QLabel(f'Шуточная шкала. При результате ниже {IQ_CLOSE_THRESHOLD} TwitchTune закроется.')
            intro.setObjectName('muted')
            intro.setWordWrap(True)
            layout.addWidget(intro)
        self.prompt = QLabel()
        self.prompt.setTextFormat(Qt.PlainText)
        self.prompt.setWordWrap(True)
        self.prompt.setMinimumHeight(65)
        layout.addWidget(self.prompt)
        self.options = []
        for _ in range(len(bank[0].distractors) + 1):
            button = QPushButton()
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, choice=button: self.answer(choice.text()))
            layout.addWidget(button)
            self.options.append(button)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName('muted')
        layout.addWidget(self.feedback)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        self.next_button = QPushButton('Следующий персонаж')
        self.next_button.setObjectName('primary')
        self.next_button.setAutoDefault(False)
        self.next_button.clicked.connect(self.advance)
        self.next_button.setHidden(iq_test)
        actions.addWidget(self.next_button)
        self.close_button = QPushButton('Выйти из игры')
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.shutdown_timer = QTimer(self)
        self.shutdown_timer.setSingleShot(True)
        self.shutdown_timer.timeout.connect(self.accept)
        self.finished.connect(self.shutdown_timer.stop)
        self.show_question()

    def show_question(self):
        self.answered = False
        question = self.questions[self.index]
        counter = f'{self.index + 1} из {len(self.questions)}'
        self.heading.setText(f'{question.series} · {counter}' if question.series else f'Вопрос {counter}')
        self.prompt.setText(question.prompt)
        for button, choice in zip(self.options, sample((question.answer, *question.distractors), len(self.options))):
            button.setText(choice)
            button.setEnabled(True)
        self.feedback.clear()
        self.next_button.setEnabled(False)
        self.options[0].setFocus()

    def answer(self, choice):
        if self.answered:
            return
        self.answered = True
        correct_answer = self.questions[self.index].answer
        correct = choice == correct_answer
        self.correct += int(correct)
        if self.iq_test:
            self.advance()
            return
        for button in self.options:
            button.setEnabled(False)
        self.feedback.setText(f'Верно! Это {correct_answer}.' if correct else f'Правильный ответ — {correct_answer}.')
        if self.index + 1 == len(self.questions):
            self.next_button.setText('Показать результат')
        self.next_button.setEnabled(True)
        self.next_button.setFocus()

    def advance(self):
        self.index += 1
        if self.index == len(self.questions):
            self.show_result()
        else:
            self.show_question()

    def show_result(self):
        for button in self.options:
            button.hide()
        self.next_button.hide()
        self.heading.setText('Результат')
        total = len(self.questions)
        if self.iq_test:
            score = 40 + 15 * self.correct
            self.prompt.setText(f'Игровой IQ — {score}\nПравильных ответов {self.correct} из {total}.')
            self.exit_after_quiz = score < IQ_CLOSE_THRESHOLD
        else:
            self.prompt.setText(f'Ты угадал {self.correct} из {total} персонажей.')
        self.feedback.clear()
        if self.exit_after_quiz:
            self.feedback.setText(f'TwitchTune закроется через {IQ_CLOSE_DELAY_SECONDS} секунд. Его можно запустить снова.')
            self.close_button.setText('Закрыть TwitchTune')
            self.shutdown_timer.start(IQ_CLOSE_DELAY_SECONDS * 1000)
        else:
            self.close_button.setText('Закрыть')
        self.close_button.setFocus()
