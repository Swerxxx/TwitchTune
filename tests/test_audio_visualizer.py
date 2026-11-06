import array
import math
import os
import sys
import unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QByteArray
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication
from audio_visualizer import spectrum, AudioWaves, BANDS


class AudioVisualizerTests(unittest.TestCase):
    def test_tones_move_frequency_peak_and_silence_has_no_energy(self):
        def tone(frequency, amplitude=0.5):
            return array.array('f', (amplitude * math.sin(2 * math.pi * frequency * i / 44100) for i in range(1024))).tobytes()
        low = spectrum(tone(220), QAudioFormat.Float, 1, 44100)
        high = spectrum(tone(4000), QAudioFormat.Float, 1, 44100)
        quiet = spectrum(tone(220, .005), QAudioFormat.Float, 1, 44100)
        self.assertLess(low.index(max(low)), high.index(max(high)))
        self.assertGreater(max(low), max(quiet))
        self.assertEqual(spectrum(bytes(4096), QAudioFormat.Float, 1, 44100), [0.] * BANDS)

    def test_pcm_formats_and_antiphase_stereo(self):
        for fmt, kind, scale, offset in ((QAudioFormat.UInt8, 'B', 100, 128),
                (QAudioFormat.Int16, 'h', 20000, 0), (QAudioFormat.Int32, 'i', 1000000000, 0),
                (QAudioFormat.Float, 'f', .5, 0)):
            samples = []
            for i in range(1024):
                a = scale * math.sin(2 * math.pi * 440 * i / 44100)
                samples.extend((a + offset, -a + offset))
            pcm = array.array(kind, samples if kind == 'f' else map(int, samples)).tobytes()
            values = spectrum(pcm, fmt, 2, 44100)
            self.assertGreater(max(values), .5)
            self.assertTrue(all(0 <= x <= 1 for x in values))
        self.assertEqual(spectrum(b'', QAudioFormat.Float, 0, 0), [0.] * BANDS)

    def test_pause_stale_buffers_and_reset(self):
        app = QApplication.instance() or QApplication([])
        widget = AudioWaves()
        fmt = QAudioFormat()
        fmt.setSampleRate(44100); fmt.setChannelCount(1); fmt.setSampleFormat(QAudioFormat.Float)
        pcm = array.array('f', (.5 * math.sin(2 * math.pi * 440 * i / 44100) for i in range(1024))).tobytes()
        buffer = QAudioBuffer(QByteArray(pcm), fmt)
        widget.set_playing(True)
        widget.receive_buffer(buffer)
        widget.tick()
        self.assertGreater(max(widget.levels), .4)
        widget.set_playing(False)
        widget.receive_buffer(buffer)
        self.assertEqual(widget.target, [0.] * BANDS)
        for _ in range(60): widget.tick()
        self.assertEqual(widget.levels, [0.] * BANDS)
        self.assertFalse(widget.timer.isActive())
        widget.set_playing(True); widget.receive_buffer(buffer); widget.tick()
        widget.last_buffer = 0
        for _ in range(60): widget.tick()
        self.assertEqual(widget.levels, [0.] * BANDS)
        widget.reset()
        widget.close(); widget.deleteLater(); app.processEvents()
