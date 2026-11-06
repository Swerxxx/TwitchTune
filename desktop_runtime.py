"""Local source updates and a process lock shared with the desktop app."""
import os
import errno
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from pathlib import Path,PurePosixPath


def acquire_lock(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    if stream.tell()==0:stream.write(b'0');stream.flush()
    stream.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        if exc.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):
            raise BlockingIOError(errno.EAGAIN,'TwitchTune is running or updating.') from exc
        raise
    return stream


def update_source(archive,target,data_folder):
    target=Path(target).resolve();data_folder=Path(data_folder)
    if not all((target/name).is_file() for name in ('app.py','core.py','requirements.txt')):
        raise ValueError('Нужна папка исходников TwitchTune с app.py, core.py и requirements.txt.')
    with acquire_lock(data_folder/'runtime.lock'):
        return _update_source(archive,target,data_folder)


def _update_source(archive,target,data_folder):
    required={'app.py','core.py','requirements.txt','desktop_launcher.py','desktop_runtime.py'}
    with zipfile.ZipFile(archive) as zipped:
        members=zipped.infolist()
        if len(members)>3000 or sum(m.file_size for m in members)>100*1024*1024:
            raise ValueError('Архив слишком большой для обновления исходников.')
        names={m.filename for m in members}
        prefix=next((p for p in ('TwitchTune/','TwitchTune-Clean/TwitchTune/','')
                     if all(p+n in names for n in required)),None)
        if prefix is None:raise ValueError('В архиве нет новой версии TwitchTune с быстрым запуском.')
        selected={};seen=set()
        for member in members:
            name=member.filename;path=PurePosixPath(name)
            if (path.is_absolute() or '..' in path.parts or '\\' in name or
                any(':' in p or p.rstrip(' .')!=p for p in path.parts) or
                stat.S_ISLNK(member.external_attr>>16)):
                raise ValueError('Небезопасный путь в архиве обновления.')
            if member.is_dir() or not name.startswith(prefix):continue
            relative=PurePosixPath(name[len(prefix):])
            if not relative.parts:continue
            if relative.parts[0] in ('assets','hooks'):
                allowed=True
            else:
                allowed=len(relative.parts)==1 and (relative.suffix in ('.py','.bat','.ps1','.md') or relative.name=='requirements.txt')
            if not allowed:continue
            key=str(relative).casefold()
            if key in seen:raise ValueError('В архиве повторяется имя файла.')
            seen.add(key);selected[str(relative)]=member
        # Never follow existing links outside the installation directory.
        for relative in selected:
            destination=target/relative
            if destination.is_symlink() or not destination.resolve().is_relative_to(target):
                raise ValueError('Путь установки выходит за пределы папки TwitchTune.')
        backup=data_folder/'update-backups'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        backup.mkdir(parents=True)
        existed=set();changed=[]
        with tempfile.TemporaryDirectory(prefix='.twichtune-update-',dir=target) as staging:
            staging=Path(staging)
            for relative,member in selected.items():
                staged=staging/relative;staged.parent.mkdir(parents=True,exist_ok=True)
                with zipped.open(member) as source,staged.open('wb') as output:shutil.copyfileobj(source,output)
                destination=target/relative
                if destination.exists():
                    saved=backup/relative;saved.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(destination,saved);existed.add(relative)
            try:
                for relative in selected:
                    destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                    os.replace(staging/relative,destination);changed.append(relative)
            except Exception:
                for relative in reversed(changed):
                    destination=target/relative
                    if relative in existed:shutil.copy2(backup/relative,destination)
                    else:destination.unlink(missing_ok=True)
                raise
        return backup
