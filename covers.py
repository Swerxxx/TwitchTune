"""Bounded artwork downloads. QImage is safe in workers; QPixmap stays in GUI."""
import re
import time
from pathlib import Path
from urllib.parse import urlparse
import requests
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImageReader

MAX_BYTES = 5 * 1024 * 1024


def image_url(info):
    url = info.get('cover_url') or info.get('thumbnail')
    if not url:
        thumbnails = info.get('thumbnails') or []
        url = next((x.get('url') for x in reversed(thumbnails) if isinstance(x, dict) and x.get('url')), '')
    if not isinstance(url, str):
        return ''
    parsed = urlparse(url)
    return url if parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password else ''


def download_cover(info, folder, ident, cancelled=lambda: False):
    """Artwork failure must never prevent audio playback. Return a local PNG or ''."""
    url = image_url(info)
    if not url or not re.fullmatch(r'[a-f0-9]{32}', ident) or cancelled():
        return ''
    path = Path(folder)/(ident+'.cover.png')
    temporary = path.with_suffix('.tmp')
    try:
        deadline = time.monotonic()+10
        with requests.get(url, stream=True, timeout=(3, 5)) as response:
            response.raise_for_status()
            if int(response.headers.get('Content-Length') or 0) > MAX_BYTES:
                return ''
            data = bytearray()
            for chunk in response.iter_content(64*1024):
                if cancelled() or time.monotonic() > deadline:
                    return ''
                data.extend(chunk)
                if len(data) > MAX_BYTES:
                    return ''
        buffer = QBuffer()
        buffer.setData(QByteArray(bytes(data)))
        buffer.open(QIODevice.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        if not size.isValid() or size.width()*size.height() > 16_000_000:
            return ''
        if size.width() > 640 or size.height() > 640:
            reader.setScaledSize(size.scaled(640, 640, Qt.KeepAspectRatio))
        image = reader.read()
        if image.isNull() or cancelled():
            return ''
        if not image.save(str(temporary), 'PNG'):
            return ''
        if cancelled():
            return ''
        temporary.replace(path)
        return str(path)
    except Exception:
        return ''
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
