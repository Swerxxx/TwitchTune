"""Bootstrap body embedded by tools/make_quick_update.py in the small updater."""
import io
import os
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path


def install(payload):
    data=Path.home()/'TwitchTune';data.mkdir(parents=True,exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='TwitchTune-bootstrap-') as temporary:
            stage=Path(temporary)
            # This archive is generated locally from the reviewed source tree.
            with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
                for name in ('desktop_runtime.py','desktop_launcher.py'):
                    (stage/name).write_bytes(zipped.read('TwitchTune/'+name))
            sys.path.insert(0,str(stage))
            from desktop_runtime import update_source
            from desktop_launcher import ensure_dependencies,choose_location
            remembered=data/'source-folder.txt'
            root=Path(remembered.read_text(encoding='utf-8').strip()) if remembered.exists() else None
            if root is None or not (root/'app.py').is_file():
                selected=choose_location(directory=True)
                if not selected:return 0
                root=Path(selected)
            if not (root/'app.py').is_file():
                raise ValueError('В этой папке нет app.py. Выбери папку исходников TwitchTune, где находится build_exe.bat.')
            archive=stage/'update.zip';archive.write_bytes(payload)
            try:update_source(archive,root,data)
            except BlockingIOError:
                raise RuntimeError('Сначала закрой TwitchTune и дождись завершения фоновых операций. Затем открой обновление ещё раз.')
            ensure_dependencies(root,data)
            remembered.write_text(str(root.resolve()),encoding='utf-8')
            with (data/'launcher.log').open('a',encoding='utf-8') as log:
                result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'setup_shortcuts.ps1')],
                                      cwd=root,stdout=log,stderr=subprocess.STDOUT,creationflags=0x08000000 if os.name=='nt' else 0)
            if result.returncode:raise RuntimeError('Файлы обновлены, но ярлыки не созданы. Запусти run_windows.bat. Подробности: '+str(data/'launcher.log'))
        return 0
    except Exception as exc:
        (data/'quick-update-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(exc)+'\n\nПодробности: '+str(data/'quick-update-error.txt'),'TwitchTune',0x10)
        else:print(exc,file=sys.stderr)
        return 1
