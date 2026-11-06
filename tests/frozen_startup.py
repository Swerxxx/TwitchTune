"""Offline smoke entry point for PyInstaller; run the built binary directly."""
import tempfile
from pathlib import Path
import app as twitchtune
from yandex_direct import FullYandexTrackIE


if __name__ == '__main__':
    # Isolate the real window from user settings, requests and credentials.
    with tempfile.TemporaryDirectory(prefix='twitchtune-smoke-') as folder:
        twitchtune.APP = Path(folder)
        application = twitchtune.QApplication([])
        window = twitchtune.Main()
        window.show()
        assert FullYandexTrackIE.ie_key() == 'FullYandexTrack'
        twitchtune.QTimer.singleShot(250, window.close)
        twitchtune.QTimer.singleShot(5000, application.quit)
        result = application.exec()
        assert window.closing, 'Window did not close normally'
        print('TWITCHTUNE_FROZEN_STARTUP_OK', flush=True)
        raise SystemExit(result)
