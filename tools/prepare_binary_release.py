"""Stamp and package the Windows build; publish only artifacts produced here."""
import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from binary_updates import version_tuple,https_url,UNITS


def validate_repository(repository):
    if not isinstance(repository,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+',repository) or repository.split('/')[1] in ('.','..'):
        raise ValueError('Укажи репозиторий owner/name.')


def stamp(root,version,repository):
    version_tuple(version);validate_repository(repository)
    channel='https://github.com/'+repository+'/releases/latest/download/latest.json'
    (Path(root)/'release_version.py').write_text('VERSION='+repr(version)+'\nUPDATE_URL='+repr(channel)+'\n',encoding='utf-8')
    return channel


def package_build(folder,output,version,repository):
    version_tuple(version);validate_repository(repository);stamp_path='https://github.com/'+repository+'/releases/download/v'+version+'/TwitchTune-Windows-x64.zip'
    https_url(stamp_path)
    folder=Path(folder);output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if not (folder/'TwitchTune.exe').is_file() or not (folder/'_internal').is_dir():raise ValueError('Сначала собери Windows onedir-приложение.')
    info=dict(app='TwitchTune',version=version,updater=1)
    (folder/'release-info.json').write_text(json.dumps(info),encoding='utf-8')
    if any(path.name not in UNITS for path in folder.iterdir()):raise ValueError('В папке сборки есть посторонние файлы.')
    archive=output/'TwitchTune-Windows-x64.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(folder.rglob('*')):
            if path.is_symlink():raise ValueError('Сборка содержит символьную ссылку.')
            if path.is_file():zipped.write(path,'TwitchTune/'+str(path.relative_to(folder)).replace('\\','/'))
    digest=hashlib.sha256()
    with archive.open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    manifest=dict(info,platform='windows-x64',url=stamp_path,size=archive.stat().st_size,sha256=digest.hexdigest(),notes='Обновление TwitchTune '+version)
    (output/'latest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('stamp','package'))
    parser.add_argument('--version',required=True);parser.add_argument('--repository',required=True)
    parser.add_argument('--folder',default='dist/TwitchTune');parser.add_argument('--output',default='release')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    if args.action=='stamp':print(stamp(root,args.version,args.repository))
    else:print(package_build(root/args.folder,root/args.output,args.version,args.repository))
