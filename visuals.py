from ratings import score_label
"""Code-drawn hardware surfaces and controls for the Acid Deck interface."""
import math
from pathlib import Path
from PySide6.QtCore import Qt, QRectF, QPointF, QSize, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QLinearGradient, QRadialGradient, QPolygonF, QPixmap, QPixmapCache
from PySide6.QtWidgets import QWidget, QPushButton, QLabel, QStyledItemDelegate, QStyle
from theme import THEMES, goth_button_image, theme_color, normalize_theme

class Themed:
    def theme_key(self):
        widget = self if isinstance(self, QWidget) else self.parent()
        return normalize_theme(getattr(widget.window(), 'theme_key', 'green'))
    def ink(self, value):
        return QColor(theme_color(value, self.theme_key()))


def screw(p, x, y, key='green'):
    ink = lambda value: QColor(theme_color(value, key))
    p.setPen(QPen(ink('#55594c'), .8))
    p.setBrush(ink('#131510'))
    p.drawEllipse(QPointF(x, y), 3, 3)
    p.setPen(QPen(ink('#727766'), .8))
    p.drawLine(QPointF(x-1.5, y+1), QPointF(x+1.5, y-1))


class Chassis(Themed, QWidget):
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), self.ink('#090b09'))
        w, h = self.width(), self.height()
        p.setPen(QPen(self.ink('#161c10'), 1))
        for offset in range(-300, 420, 26):
            p.drawLine(w - 260 + offset, 0, w + offset, 210)
            p.drawLine(offset, h, offset + 220, h - 140)
        p.setPen(QPen(self.ink('#38471a'), 1))
        p.drawLine(8, 28, 8, h-28)
        p.drawLine(w-9, 28, w-9, h-28)
        for x in (8, w-9):
            for y in (16, h-16): screw(p, x, y, self.theme_key())


class ElideLabel(QLabel):
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(0)
    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())
    def paintEvent(self, event):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        p.drawText(self.contentsRect(), self.alignment() | Qt.AlignVCenter,
                   self.fontMetrics().elidedText(self.text(), Qt.ElideRight, self.contentsRect().width()))


class TransportButton(Themed, QPushButton):
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind, self.playing = kind, False
        side = 48 if kind == 'play' else 36
        self.setFixedSize(side, side)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName({'play': 'Воспроизведение / пауза', 'prev': 'Предыдущий трек', 'next': 'Пропустить трек'}[kind])
        self.setToolTip(self.accessibleName())
    def set_playing(self, value):
        self.playing = value
        self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if getattr(self.window(), 'button_style', 'classic') == 'dark_goth':
            active = self.isEnabled() and (
                self.underMouse() or self.hasFocus() or self.isDown() or self.kind == 'play')
            path = goth_button_image(active)
            frame = QPixmapCache.find(path)
            if frame is None:
                frame = QPixmap(path)
                QPixmapCache.insert(path, frame)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(r, frame, QRectF(frame.rect()))
        else:
            g = QLinearGradient(0, 0, 0, self.height())
            g.setColorAt(0, self.ink('#4b5142'))
            g.setColorAt(.35, self.ink('#181c15'))
            g.setColorAt(.65, self.ink('#050705'))
            g.setColorAt(1, self.ink('#292f20'))
            p.setBrush(g)
            p.setPen(QPen(self.ink('#c6f52b') if self.underMouse() or self.kind == 'play' else self.ink('#626c48'), 1.3))
            p.drawEllipse(r)
            if self.hasFocus():
                p.setPen(QPen(self.ink('#c6f52b'), 1, Qt.DotLine))
                p.drawEllipse(r.adjusted(-1, -1, 1, 1))
        p.setPen(Qt.NoPen)
        p.setBrush(self.ink('#c6f52b'))
        cx, cy = self.width()/2, self.height()/2 + (1 if self.isDown() else 0)
        if self.kind == 'play' and self.playing:
            p.drawRoundedRect(QRectF(cx-7, cy-8, 5, 16), 1, 1)
            p.drawRoundedRect(QRectF(cx+2, cy-8, 5, 16), 1, 1)
        else:
            direction = -1 if self.kind == 'prev' else 1
            size = 8 if self.kind == 'play' else 6
            p.drawPolygon(QPolygonF([QPointF(cx-direction*size/2, cy-size),
                                    QPointF(cx+direction*size, cy), QPointF(cx-direction*size/2, cy+size)]))
            if self.kind != 'play':
                p.drawRect(QRectF(cx+direction*8-(2 if direction<0 else 0), cy-7, 2, 14))


class DeckDisplay(Themed, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(230, 178)
        self.setMaximumHeight(196)
        self.title, self.viewer = 'NO TRACK LOADED', 'Ожидание трека'
        self.playing, self.phase, self.position = False, 0, 0
        self.timer = QTimer(self)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.tick)
    def set_track(self, title, viewer):
        self.title, self.viewer = title, viewer
        self.update()
    def set_playing(self, value):
        self.playing = value
        self.timer.start() if value else self.timer.stop()
        self.update()
    def tick(self):
        self.phase += .3
        self.update()
    def set_position(self, value):
        self.position = value
        self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        shell = QLinearGradient(0, 0, w, h)
        for at, color in ((0, '#45483e'), (.07, '#0d100b'), (.5, '#191d14'), (.93, '#080b06'), (1, '#505a36')):
            shell.setColorAt(at, self.ink(color))
        p.setBrush(shell); p.setPen(QPen(self.ink('#68754c'), 1))
        p.drawRoundedRect(QRectF(1, 1, w-2, h-2), 15, 15)
        for x in (9, w-9):
            for y in (10, h-10): screw(p, x, y, self.theme_key())
        p.setPen(self.ink('#d2d6c8')); p.setFont(QFont('Consolas', 9, QFont.Bold))
        p.drawText(QRectF(18, 9, w-36, 18), Qt.AlignVCenter, 'TWITCHTUNE  /  DIGITAL AUDIO')
        screen = QRectF(13, 33, w-26, h-48)
        p.setBrush(self.ink('#090e02')); p.setPen(QPen(self.ink('#c6f52b'), 1))
        p.drawRoundedRect(screen, 5, 5)
        p.setPen(QPen(self.ink('#101b03'), .5))
        for y in range(38, h-15, 3): p.drawLine(16, y, w-16, y)
        p.setPen(self.ink('#c6f52b')); p.setFont(QFont('Consolas', 8))
        p.drawText(23, 52, 'PLAY >' if self.playing else 'STANDBY')
        p.drawText(w-67, 52, 'STEREO')
        p.setFont(QFont('Bahnschrift', 12, QFont.Bold))
        title = p.fontMetrics().elidedText(self.title, Qt.ElideRight, w-48)
        p.drawText(QRectF(23, 62, w-46, 24), Qt.AlignVCenter, title)
        p.setFont(QFont('Consolas', 8))
        p.drawText(QRectF(23, 86, w-46, 17), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.viewer, Qt.ElideRight, w-48))
        # Playback visual only, not a measured frequency spectrum.
        count = 28
        bw = max(3, (w-48)/count - 2)
        for i in range(count):
            height = 3 if not self.playing else 5 + (math.sin(i*.62+self.phase)+1)*10 + (math.sin(i*1.7-self.phase)+1)*3
            p.fillRect(QRectF(23+i*(bw+2), h-37-height, bw, height), self.ink('#c6f52b'))
        p.setPen(self.ink('#7b9834')); p.setFont(QFont('Consolas', 7))
        secs = self.position//1000
        p.drawText(23, h-24, f'{secs//60:02}:{secs%60:02}  /  LOCAL PLAYBACK')


class QueueDelegate(Themed, QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(200, 78)
    def paint(self, painter, option, index):
        row = index.data(Qt.UserRole)
        if not isinstance(row, dict):
            return super().paint(painter, option, index)
        p = painter
        p.save(); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(1, 3, -2, -3)
        selected = bool(option.state & QStyle.State_Selected)
        playing = row['status'] == 'Играет'
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, self.ink('#263018' if selected else '#1a1e17'))
        g.setColorAt(1, self.ink('#10150a' if selected else '#10130e'))
        p.setBrush(g); p.setPen(QPen(self.ink('#c6f52b') if selected else self.ink('#343c2a'), 1))
        p.drawRoundedRect(r, 9, 9)
        if playing:
            p.fillRect(QRectF(r.left()+1, r.top()+12, 3, r.height()-24), self.ink('#c6f52b'))
        x, y = r.left(), r.top()
        p.setPen(self.ink('#c6f52b') if playing or selected else self.ink('#66714f'))
        p.setFont(QFont('Consolas', 11))
        cover = None
        cover_path = row.get('cover_path') or ''
        if cover_path:
            try:
                key = cover_path + ':' + str(Path(cover_path).stat().st_mtime_ns)
                cover = QPixmapCache.find(key)
                if cover is None:
                    cover = QPixmap(cover_path)
                    if not cover.isNull():
                        cover = cover.scaled(36, 36, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                        cover = cover.copy((cover.width()-36)//2, (cover.height()-36)//2, 36, 36)
                        QPixmapCache.insert(key, cover)
            except OSError:
                pass
        if cover is not None and not cover.isNull():
            p.drawPixmap(int(x+10), int(y+(r.height()-36)/2), cover)
        else:
            p.drawText(QRectF(x+10, y, 36, r.height()), Qt.AlignCenter, f'{index.row()+1:02}')
        p.setPen(self.ink('#e0e4d4')); p.setFont(QFont('Segoe UI', 10, QFont.DemiBold))
        width = max(50, int(r.width()-195))
        p.drawText(QRectF(x+55, y+10, width, 23), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(row['title'], Qt.ElideRight, width))
        p.setPen(self.ink('#9ca58a')); p.setFont(QFont('Segoe UI', 9))
        subtitle = row['viewer'] + '  /  ' + row['source']
        if row['source'] == 'Spotify' and row.get('replacement'):
            subtitle += ' → YouTube'
        if row.get('redemption'):
            subtitle += f"  /  до {int(row.get('max_duration') or 5400)//60} мин"
        if row.get('donation_provider'):
            provider='DonationAlerts' if row['donation_provider']=='donationalerts' else 'DonateX'
            subtitle=provider+' · '+str(row.get('donation_amount',''))+' '+str(row.get('donation_currency',''))+' / '+subtitle
        if row.get('vip_priority'):
            subtitle = 'VIP ★  /  ' + subtitle
        p.drawText(QRectF(x+55, y+36, width, 21), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(subtitle, Qt.ElideRight, width))
        p.setFont(QFont('Consolas', 8)); p.setPen(self.ink('#c6f52b') if playing else self.ink('#8c9c70'))
        p.drawText(QRectF(r.right()-136, y+12, 123, 21), Qt.AlignRight | Qt.AlignVCenter, row['status'])
        p.setPen(self.ink('#d2d7bf') if row['rating'] else self.ink('#68715a'))
        score = score_label(row) if row['rating'] is not None else '— / 66'
        p.drawText(QRectF(r.right()-136, y+37, 123, 19), Qt.AlignRight | Qt.AlignVCenter, score)
        p.restore()


class ThemeCard(QPushButton):
    """Clickable preview whose palette stays visible even when another is active."""
    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.setCheckable(True)
        self.setMinimumWidth(180)
        self.setFixedHeight(118)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName('Тема: ' + THEMES[key]['name'])
        self.setToolTip(THEMES[key]['description'])
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        theme = THEMES[self.key]
        ink = lambda value: QColor(theme_color(value, self.key))
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        gradient = QLinearGradient(r.topLeft(), r.bottomLeft())
        gradient.setColorAt(0, ink('#252a20'))
        gradient.setColorAt(1, ink('#0d100c'))
        p.setBrush(gradient)
        p.setPen(QPen(QColor(theme['accent']) if self.isChecked() or self.underMouse() else ink('#454d36'), 2 if self.isChecked() else 1))
        p.drawRoundedRect(r, 10, 10)
        p.setPen(ink('#e4e9d5')); p.setFont(QFont('Segoe UI', 10, QFont.DemiBold))
        p.drawText(QRectF(15, 12, self.width()-48, 22), Qt.AlignVCenter, theme['name'])
        p.setPen(QColor(theme['accent'])); p.setFont(QFont('Consolas', 7))
        p.drawText(15, 47, theme['tag'])
        p.setBrush(QColor(theme['accent']) if self.isChecked() else Qt.NoBrush)
        p.setPen(QPen(QColor(theme['accent']), 1))
        p.drawEllipse(QPointF(self.width()-22, 24), 5, 5)
        screen = QRectF(15, 60, self.width()-30, 28)
        p.setBrush(ink('#090e02')); p.setPen(QPen(ink('#3e4c2a'), 1))
        p.drawRoundedRect(screen, 4, 4)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme['accent']))
        for i in range(18):
            height = 4 + (i*7 % 13)
            p.drawRect(QRectF(24+i*4.5, 82-height, 3, height))
        p.drawEllipse(QPointF(self.width()-30, 74), 6, 6)
        p.setPen(ink('#879477')); p.setFont(QFont('Consolas', 7))
        p.drawText(15, 105, 'ВЫБРАНА' if self.isChecked() else 'НАЖМИ ДЛЯ ВЫБОРА')
