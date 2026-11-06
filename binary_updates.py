"""HTTPS release checking, verified staging and a Python-free Windows handoff."""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path,PurePosixPath
from urllib.parse import urlparse,urljoin
import requests

MAX_ARCHIVE=1024*1024*1024
MAX_EXTRACT=2*1024*1024*1024
UNITS={'TwitchTune.exe','_internal','release-info.json','run_debug.bat'}


class UpdateError(RuntimeError):pass


def version_tuple(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)',value):
        raise UpdateError('Неверный номер версии обновления.')
    return tuple(map(int,value.split('.')))


def https_url(value):
    if not isinstance(value,str) or len(value)>4096:raise UpdateError('Неверный адрес обновлений.')
    parsed=urlparse(value)
    try:valid=parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and parsed.port in (None,443)
    except ValueError:valid=False
    if not valid:raise UpdateError('Для обновлений нужен адрес HTTPS без пароля.')
    return value


def open_https(url):
    for _ in range(6):
        response=requests.get(https_url(url),stream=True,allow_redirects=False,timeout=(5,15),headers={'User-Agent':'TwitchTune updater','Accept':'application/json,application/zip'})
        if response.status_code in (301,302,303,307,308):
            location=response.headers.get('Location','');response.close()
            url=https_url(urljoin(url,location));continue
        if response.status_code!=200:
            response.close();raise UpdateError('Сервер обновлений недоступен. Попробуй позже.')
        return response
    raise UpdateError('Слишком много перенаправлений сервера обновлений.')


def read_manifest(url,cancelled=lambda:False):
    try:
        with open_https(url) as response:
            body=bytearray()
            for chunk in response.iter_content(16384):
                if cancelled():raise UpdateError('Проверка обновления отменена.')
                body.extend(chunk)
                if len(body)>65536:raise UpdateError('Некорректный ответ сервера обновлений.')
        data=json.loads(body.decode('utf-8-sig'))
        if not isinstance(data,dict) or data.get('app')!='TwitchTune' or data.get('platform')!='windows-x64':
            raise UpdateError('Обновление не подходит для TwitchTune Windows x64.')
        version_tuple(data.get('version'));https_url(data.get('url'))
        if not re.fullmatch(r'[a-fA-F0-9]{64}',str(data.get('sha256',''))):raise UpdateError('Нет контрольной суммы обновления.')
        if type(data.get('size')) is not int or not 0<data['size']<=MAX_ARCHIVE:raise UpdateError('Неподдерживаемый размер обновления.')
        if data.get('updater',1)!=1:raise UpdateError('Нужна новая версия установщика. Обратись к автору программы.')
        # A GitHub release must stay within the configured repository.
        channel=urlparse(url);asset=urlparse(data['url'])
        if channel.hostname=='github.com':
            pieces=channel.path.strip('/').split('/')
            if len(pieces)<2 or asset.hostname!='github.com' or not asset.path.startswith('/'+'/'.join(pieces[:2])+'/releases/download/'):
                raise UpdateError('Файл обновления находится вне настроенного репозитория.')
        elif asset.hostname!=channel.hostname:raise UpdateError('Адрес файла не относится к серверу обновлений.')
        return {**data,'sha256':data['sha256'].lower(),'notes':str(data.get('notes') or '')[:5000]}
    except UpdateError:raise
    except Exception:raise UpdateError('Не удалось проверить обновление. Проверь интернет или повтори позже.') from None


def download_package(manifest,folder,cancelled=lambda:False,progress=lambda *_:None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    partial=folder/'package.zip.part';complete=folder/'package.zip'
    try:
        digest=hashlib.sha256();total=0
        with open_https(manifest['url']) as response,partial.open('wb') as output:
            for chunk in response.iter_content(256*1024):
                if cancelled():raise UpdateError('Загрузка обновления отменена.')
                total+=len(chunk)
                if total>manifest['size'] or total>MAX_ARCHIVE:raise UpdateError('Размер загруженного файла не совпал с описанием.')
                output.write(chunk);digest.update(chunk);progress(total,manifest['size'])
        if total!=manifest['size'] or digest.hexdigest()!=manifest['sha256']:raise UpdateError('Файл обновления повреждён: контрольная сумма не совпала.')
        partial.replace(complete);return complete
    except UpdateError:raise
    except Exception:raise UpdateError('Не удалось скачать обновление. Текущая версия не изменена.') from None
    finally:partial.unlink(missing_ok=True)


def stage_package(archive,target,version,cancelled=lambda:False):
    target=Path(target).resolve();version_tuple(version)
    if target==target.parent:raise UpdateError('Помести TwitchTune в отдельную папку перед обновлением.')
    for name in UNITS:
        existing=target/name
        if existing.is_symlink() or getattr(existing,'is_junction',lambda:False)():
            raise UpdateError('В папке приложения есть ссылки на другие папки. Автоматическое обновление остановлено.')
    stage=Path(tempfile.mkdtemp(prefix='.TwitchTune-update-',dir=target.parent))
    try:
        with zipfile.ZipFile(archive) as zipped:
            members=zipped.infolist();seen=set()
            if len(members)>15000 or sum(m.file_size for m in members)>MAX_EXTRACT:raise UpdateError('Сборка обновления слишком большая.')
            for member in members:
                path=PurePosixPath(member.filename)
                reserved={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
                if (path.is_absolute() or '..' in path.parts or '\\' in member.filename or
                    any(':' in part or part.rstrip(' .')!=part or part.split('.')[0].upper() in reserved for part in path.parts) or
                    stat.S_ISLNK(member.external_attr>>16)):
                    raise UpdateError('Небезопасный путь в архиве обновления.')
                if not path.parts or path.parts[0]!='TwitchTune':raise UpdateError('Неверная структура Windows-сборки.')
                if len(path.parts)==1:continue
                relative=PurePosixPath(*path.parts[1:])
                if relative.parts[0] not in UNITS:raise UpdateError('В сборке обнаружены посторонние файлы.')
                if member.is_dir():continue
                if relative.name=='release-info.json' and member.file_size>65536:raise UpdateError('Некорректное описание сборки.')
                key=str(relative).casefold()
                if key in seen:raise UpdateError('В архиве повторяется имя файла.')
                seen.add(key)
                if cancelled():raise UpdateError('Обновление отменено.')
                destination=stage/relative;destination.parent.mkdir(parents=True,exist_ok=True)
                with zipped.open(member) as source,destination.open('wb') as output:shutil.copyfileobj(source,output)
        if not (stage/'TwitchTune.exe').is_file() or not (stage/'_internal').is_dir():raise UpdateError('В обновлении нет готовой EXE-сборки.')
        with (stage/'TwitchTune.exe').open('rb') as executable:
            if executable.read(2)!=b'MZ':raise UpdateError('В обновлении нет Windows-приложения.')
        info=json.loads((stage/'release-info.json').read_text(encoding='utf-8'))
        if info.get('version')!=version or info.get('updater')!=1:raise UpdateError('Версия внутри архива не совпала с описанием.')
        return stage
    except Exception:
        shutil.rmtree(stage,ignore_errors=True);raise


def launch_installer(stage,target,data,version,job):
    if sys.platform!='win32' or not getattr(sys,'frozen',False):raise UpdateError('Этот обновлятор устанавливает готовую Windows EXE-сборку.')
    target=Path(target).resolve();stage=Path(stage).resolve();data=Path(data).resolve();job=Path(job).resolve()
    if stage.parent!=target.parent or not stage.name.startswith('.TwitchTune-update-') or not (target/'TwitchTune.exe').is_file():
        raise UpdateError('Не удалось определить папку установленной программы.')
    script=job/'apply_binary_update.ps1'
    shutil.copy2(Path(__file__).resolve().parent/'apply_binary_update.ps1',script)
    command=['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
             '-ParentProcessId',str(os.getpid()),'-InstallDir',str(target),'-StageDir',str(stage),'-DataDir',str(data),'-Version',version,
             '-ReadyFile',str(job/'installer.ready')]
    with (job/'installer.log').open('wb') as log:
        process=subprocess.Popen(command,cwd=job,creationflags=0x08000000,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        if (job/'installer.ready').is_file():return
        if process.poll() is not None:break
        time.sleep(.1)
    if process.poll() is None:process.terminate()
    raise UpdateError('Не удалось запустить установку. Программа не закрыта. Журнал: '+str(job/'installer.log'))


def write_health_marker(path,data,version):
    path=Path(path).resolve();updates=(Path(data)/'updates').resolve()
    if path.suffix!='.ready' or not path.is_relative_to(updates):return False
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(version,encoding='utf-8');return True
