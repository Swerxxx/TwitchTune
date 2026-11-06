"""Fast Windows launch and local ZIP update; no executable build required."""
import argparse
import ctypes
import hashlib
import os
import subprocess
import sys
import traceback
from pathlib import Path
from desktop_runtime import update_source

ROOT=Path(__file__).resolve().parent
DATA=Path.home()/'TwitchTune'


def notify(message):
    if os.name=='nt':ctypes.windll.user32.MessageBoxW(None,str(message),'TwitchTune',0x10)
    else:print(message,file=sys.stderr)


def ensure_dependencies(root=ROOT,data=DATA):
    requirements=root/'requirements.txt'
    digest=hashlib.sha256(requirements.read_bytes()).hexdigest()
    key=hashlib.sha256((str(root)+sys.executable).encode()).hexdigest()[:20]
    marker=data/'runtime'/(key+'.sha256');marker.parent.mkdir(parents=True,exist_ok=True)
    if marker.exists() and marker.read_text()==digest:return
    with (data/'launcher.log').open('a',encoding='utf-8') as log:
        result=subprocess.run([sys.executable,'-m','pip','install','-r',str(requirements)],cwd=root,
                              stdout=log,stderr=subprocess.STDOUT,creationflags=0x08000000 if os.name=='nt' else 0)
    if result.returncode:raise RuntimeError('Не удалось установить зависимости. Проверь интернет. Подробности: '+str(data/'launcher.log'))
    marker.write_text(digest)


def choose_location(directory=False):
    if os.name=='nt':
        common="Add-Type -AssemblyName System.Windows.Forms; [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
        if directory:
            script=common+"$d=New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description='TwitchTune: choose the source folder containing app.py (not dist)'; if($d.ShowDialog() -eq 'OK'){[Console]::Write($d.SelectedPath)}"
        else:
            script=common+"$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Title='TwitchTune: choose the new ZIP archive'; $d.Filter='TwitchTune ZIP (*.zip)|*.zip'; if($d.ShowDialog() -eq 'OK'){[Console]::Write($d.FileName)}"
        result=subprocess.run(['powershell','-NoProfile','-STA','-Command',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              encoding='utf-8',creationflags=0x08000000)
        if result.returncode:raise RuntimeError('Не удалось открыть окно выбора файла или папки.')
        return result.stdout.strip()
    from PySide6.QtWidgets import QApplication,QFileDialog
    application=QApplication.instance() or QApplication([])
    if directory:return QFileDialog.getExistingDirectory(None,'Выбери папку TwitchTune с app.py (не dist)',str(Path.home()))
    name,_=QFileDialog.getOpenFileName(None,'Выбери новый архив TwitchTune',str(Path.home()/'Downloads'),'TwitchTune ZIP (*.zip)')
    return name


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--update',nargs='?',const='')
    parser.add_argument('--repair',action='store_true')
    args=parser.parse_args();DATA.mkdir(parents=True,exist_ok=True)
    try:
        if args.repair:
            for marker in (DATA/'runtime').glob('*.sha256'):marker.unlink()
        if args.update is not None:
            archive=args.update or choose_location()
            if not archive:return 0
            try:update_source(archive,ROOT,DATA)
            except BlockingIOError:
                raise RuntimeError('Сначала закрой TwitchTune и дождись завершения фоновых операций, затем повтори обновление.')
        ensure_dependencies()
        with (DATA/'launcher.log').open('a',encoding='utf-8') as log:
            result=subprocess.run([sys.executable,str(ROOT/'app.py')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                                  creationflags=0x08000000 if os.name=='nt' else 0)
        if result.returncode:raise RuntimeError('Приложение завершилось с ошибкой. Подробности: '+str(DATA/'launcher.log'))
        return 0
    except Exception as exc:
        with (DATA/'launcher.log').open('a',encoding='utf-8') as log:log.write(traceback.format_exc()+'\n')
        notify(str(exc));return 1


if __name__=='__main__':sys.exit(main())
