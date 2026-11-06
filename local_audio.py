"""Import a private copy of a local MP3; never move/delete the original."""
import math
from limits import MAX_TRACK_SECONDS,MAX_TRACK_BYTES
import uuid
from pathlib import Path
from mutagen.mp3 import MP3
from PySide6.QtGui import QImage


def import_mp3(store, folder, source, viewer='Вручную', cancelled=lambda: False):
    source, folder = Path(source), Path(folder)
    if source.suffix.lower() != '.mp3' or not source.is_file():
        raise ValueError('Выбери существующий файл MP3.')
    if source.stat().st_size>MAX_TRACK_BYTES:
        raise ValueError('Файл больше 512 МБ.')
    folder.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    path, temporary = folder / (ident + '.mp3'), folder / (ident + '.importing')
    cover = folder / (ident + '.cover.png')
    committed = False
    try:
        with source.open('rb') as reader, temporary.open('xb') as writer:
            while chunk := reader.read(1024 * 1024):
                if cancelled():
                    raise ValueError('Импорт отменён.')
                writer.write(chunk)
        try:
            audio = MP3(temporary)
            duration = float(audio.info.length)
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError()
        except Exception as exc:
            raise ValueError('Не удалось прочитать MP3. Файл повреждён или не содержит MP3-аудио.') from exc
        if duration>MAX_TRACK_SECONDS:
            raise ValueError('Трек длиннее лимита 90 минут.')
        tags = audio.tags
        def tag(name):
            frame = tags.get(name) if tags else None
            return str(frame.text[0]).strip()[:300] if frame and frame.text else ''
        artist, track = tag('TPE1'), tag('TIT2')
        # A filename remains useful for lyrics lookup when no ID3 title exists.
        title = (artist + ' — ' + track if artist else track) if track else source.stem
        if tags:
            pictures = tags.getall('APIC')
            pictures.sort(key=lambda p: p.type != 3)
            for picture in pictures:
                image = QImage.fromData(picture.data)
                if not image.isNull():
                    image.scaled(512, 512).save(str(cover), 'PNG')
                    break
        if cancelled():
            raise ValueError('Импорт отменён.')
        temporary.replace(path)
        result = store.add_local(dict(id=ident, title=title, artist=artist, track=track,
                                      duration=duration, path=str(path),
                                      cover_path=str(cover) if cover.is_file() else ''), viewer)
        committed = True
        return result
    finally:
        temporary.unlink(missing_ok=True)
        if not committed:
            path.unlink(missing_ok=True)
            cover.unlink(missing_ok=True)
