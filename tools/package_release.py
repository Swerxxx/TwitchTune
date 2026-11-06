"""Package source and clean distributions without runtime or personal data."""
import shutil
import zipfile
from pathlib import Path


def package(workspace):
    root=Path(workspace);source=root/'TwitchTune';clean=root/'TwitchTune-Clean/TwitchTune'
    clean.mkdir(parents=True,exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and (path.suffix in ('.py','.bat','.ps1','.md') or path.name=='requirements.txt'):
            shutil.copy2(path,clean/path.name)
    for name in ('assets','hooks'):
        shutil.copytree(source/name,clean/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    def allowed(path):
        return path.is_file() and not any(p in ('__pycache__','build','dist','release','.venv','.git') for p in path.parts) and path.suffix not in ('.pyc','.pyo')
    main=root/'TwitchTune-transfer.zip';fresh=root/'TwitchTune-Clean.zip'
    with zipfile.ZipFile(main,'w',zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(source.rglob('*')):
            if allowed(path):zipped.write(path,path.relative_to(root))
        for name in ('TwitchTune-continuation.md','TwitchTune-transfer-prompt.txt'):zipped.write(root/name,name)
        preview=root/'TwitchTune-Acid-Deck.png'
        if preview.exists():zipped.write(preview,'TwitchTune/PREVIEW.png')
    with zipfile.ZipFile(fresh,'w',zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(clean.rglob('*')):
            if allowed(path):zipped.write(path,path.relative_to(clean.parent))
    for archive in (main,fresh):
        with zipfile.ZipFile(archive) as zipped:
            assert zipped.testzip() is None
            assert all('TwitchTune/'+name in zipped.namelist() for name in ('app.py','desktop_launcher.py','core.py','donations.py'))
            assert not any(Path(name).suffix in ('.json','.db','.sqlite3','.pyc') for name in zipped.namelist())
            if archive==fresh:assert not any('/tests/' in name or Path(name).suffix.lower() in ('.mp3','.flac','.wav') for name in zipped.namelist())
            print(archive.name,len(zipped.namelist()),archive.stat().st_size)


if __name__=='__main__':package(Path(__file__).resolve().parents[2])
