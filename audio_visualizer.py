"""Small PCM spectrum analyser and a themed, audio-driven display."""
import array
import math
import time
from PySide6.QtCore import QTimer, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPen, QFont, QLinearGradient
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtMultimedia import QAudioFormat
from visuals import Themed

BANDS = 28


def spectrum(data, sample_format, channels, sample_rate):
    """Log-spaced FFT magnitudes; bounded to 1024 frames, no extra decoder."""
    formats = {QAudioFormat.UInt8: ('B', 128, 128), QAudioFormat.Int16: ('h', 0, 32768),
               QAudioFormat.Int32: ('i', 0, 2147483648), QAudioFormat.Float: ('f', 0, 1)}
    if sample_format not in formats or channels < 1 or sample_rate <= 0:
        return [0.] * BANDS
    kind, offset, scale = formats[sample_format]
    samples = array.array(kind)
    size = samples.itemsize
    frames = min(1024, len(data) // (size * channels))
    if frames < 32:
        return [0.] * BANDS
    n = 1 << (frames.bit_length() - 1)
    samples.frombytes(bytes(data[:n * channels * size]))
    # RMS across channels would lose phase information; analyse a real channel
    # and select the loudest one so stereo phase cancellation cannot hide sound.
    channel = max(range(channels), key=lambda c: sum(float(samples[i]) ** 2 for i in range(c, n * channels, channels)))
    values = [complex(((samples[i * channels + channel] - offset) / scale)
                      * (.5 - .5 * math.cos(2 * math.pi * i / (n - 1)))) for i in range(n)]
    values = [v if math.isfinite(v.real) else 0j for v in values]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            values[i], values[j] = values[j], values[i]
    width = 2
    while width <= n:
        angle = -2 * math.pi / width
        step = complex(math.cos(angle), math.sin(angle))
        for start in range(0, n, width):
            weight = 1 + 0j
            for k in range(width // 2):
                even = values[start + k]
                odd = weight * values[start + k + width // 2]
                values[start + k], values[start + k + width // 2] = even + odd, even - odd
                weight *= step
        width *= 2
    low, high = max(40, sample_rate / n), min(18000, sample_rate / 2)
    if high <= low:
        return [0.] * BANDS
    result = []
    for band in range(BANDS):
        first = max(1, int(low * (high / low) ** (band / BANDS) * n / sample_rate))
        last = min(n // 2, max(first + 1, int(low * (high / low) ** ((band + 1) / BANDS) * n / sample_rate)))
        magnitude = max((abs(values[k]) * 4 / n for k in range(first, last)), default=0)
        # Fixed -60..0 dB range, so silence stays silent and quiet passages shrink.
        result.append(max(0., min(1., (20 * math.log10(max(1e-9, magnitude)) + 60) / 60)))
    return result


class AudioWaves(Themed, QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(0)
        self.setMaximumHeight(150)
        self.setAccessibleName('Спектр играющего трека')
        self.setToolTip('Волны реагируют на звук текущего трека')
        self.levels, self.target = [0.] * BANDS, [0.] * BANDS
        self.playing, self.last_buffer = False, 0.
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.tick)

    def sizeHint(self):
        return QSize(230, 110)

    def minimumSizeHint(self):
        return QSize(0, 0)

    def set_playing(self, playing):
        self.playing = playing
        self.target = [0.] * BANDS
        self.last_buffer = 0.
        self.timer.start()

    def reset(self, *_):
        self.target, self.levels = [0.] * BANDS, [0.] * BANDS
        self.last_buffer = 0.
        self.update()

    def receive_buffer(self, buffer):
        if not self.playing or not buffer.isValid():
            return
        fmt = buffer.format()
        self.target = spectrum(buffer.constData(), fmt.sampleFormat(), fmt.channelCount(), fmt.sampleRate())
        self.last_buffer = time.monotonic()
        if not self.timer.isActive():
            self.timer.start()

    def tick(self):
        live = self.playing and time.monotonic() - self.last_buffer < .25
        for i in range(BANDS):
            target = self.target[i] if live else 0.
            self.levels[i] += (target - self.levels[i]) * (.65 if target > self.levels[i] else .18)
        if max(self.levels) < .002 and not live:
            self.levels = [0.] * BANDS
            self.timer.stop()
        self.update()

    def paintEvent(self, event):
        if self.height() < 20:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setBrush(self.ink('#090e02'))
        p.setPen(QPen(self.ink('#38471a'), 1))
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 7, 7)
        top = 26 if h >= 65 else 8
        if h >= 65:
            p.setFont(QFont('Consolas', 7))
            p.setPen(self.ink('#8c9c70'))
            p.drawText(QRectF(10, 5, w - 20, 16), Qt.AlignVCenter, 'AUDIO / LIVE' if self.playing else 'AUDIO / STANDBY')
        bar_width = (w - 20) / BANDS
        available = max(2, h - top - 10)
        gradient = QLinearGradient(0, top, 0, h - 8)
        gradient.setColorAt(0, self.ink('#c6f52b'))
        gradient.setColorAt(1, self.ink('#354b17'))
        for i, level in enumerate(self.levels):
            height = max(2., available * level)
            p.fillRect(QRectF(10 + i * bar_width, h - 9 - height, max(2, bar_width - 2), height), gradient)
        p.setPen(QPen(self.ink('#090e02'), 1))
        for y in range(top, h - 9, 4):
            p.drawLine(9, y, w - 9, y)
