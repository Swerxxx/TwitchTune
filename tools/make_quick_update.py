"""Generate the small self-contained Windows updater from this source tree."""
import base64
import io
import sys
import textwrap
import zipfile
import zlib
from pathlib import Path


def build(root,output):
    root=Path(root);stream=io.BytesIO()
    files=[p for p in root.iterdir() if p.is_file() and (p.suffix in ('.py','.bat','.ps1') or p.name in ('requirements.txt','QUICK_START.md','AUTO_UPDATE_SETUP.md'))]
    files += [p for p in (root/'hooks').rglob('*.py') if '__pycache__' not in p.parts]
    files += [p for p in (root/'assets').iterdir() if p.suffix in ('.ico','.svg')]
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(files):zipped.write(path,'TwitchTune/'+str(path.relative_to(root)).replace('\\','/'))
    bootstrap=(root/'quick_install.py').read_text(encoding='utf-8')
    bootstrap+='\nimport base64\nsys.exit(install(base64.b64decode('+repr(base64.b64encode(stream.getvalue()).decode())+')))\n'
    payload=base64.b64encode(zlib.compress(bootstrap.encode('utf-8'),9)).decode()
    header='''@echo off
setlocal
py -c "import base64,pathlib,sys,zlib; raw=pathlib.Path(sys.argv[1]).read_bytes().split(b'::'+b'TWITCHTUNE_PAYLOAD::',1)[1]; exec(compile(zlib.decompress(base64.b64decode(raw)), 'TwitchTune updater', 'exec'))" "%~f0"
set TT_UPDATE_EXIT=%errorlevel%
if not "%TT_UPDATE_EXIT%"=="0" pause
exit /b %TT_UPDATE_EXIT%
::TWITCHTUNE_PAYLOAD::
'''
    Path(output).write_bytes((header+textwrap.fill(payload,76)+'\n').replace('\n','\r\n').encode('ascii'))
    return len(files)


if __name__=='__main__':
    destination=Path(sys.argv[1]) if len(sys.argv)>1 else Path.cwd()/'TwitchTune-Update.bat'
    count=build(Path(__file__).resolve().parents[1],destination)
    print(str(destination),destination.stat().st_size,'bytes;',count,'files')
