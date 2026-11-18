"""Small, console-enabled native host for the packaged browser extension."""
import json
import os
from pathlib import Path
import struct
import sys
from browser_bridge import BrowserBridge, HOST_NAME, FIREFOX_ID, CHROMIUM_ID, MAX_MESSAGE


def read_message(stream):
    header=stream.read(4)
    if not header:return None
    if len(header)!=4:raise ValueError('Truncated header')
    size=struct.unpack('=I',header)[0]
    if not 0<size<=MAX_MESSAGE:raise ValueError('Invalid frame size')
    chunks=bytearray()
    while len(chunks)<size:
        part=stream.read(size-len(chunks))
        if not part:raise ValueError('Truncated frame')
        chunks.extend(part)
    return json.loads(chunks.decode('utf-8'))


def write_message(stream,value):
    data=json.dumps(value,ensure_ascii=False).encode('utf-8')
    if len(data)>MAX_MESSAGE:raise ValueError('Oversized response')
    stream.write(struct.pack('=I',len(data))+data);stream.flush()


def caller_profile(args):
    if sys.platform!='win32':raise ValueError('Windows host required')
    import winreg
    chrome='chrome-extension://'+CHROMIUM_ID+'/'
    if args and args[0]==chrome:
        vendors=(r'Google\Chrome',r'Microsoft\Edge')
    elif len(args)>=2 and args[1]==FIREFOX_ID:
        vendors=(r'Mozilla',)
    else:raise ValueError('Extension not allowed')
    for vendor in vendors:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Software\\'+vendor+'\\NativeMessagingHosts\\'+HOST_NAME) as key:
                manifest=Path(winreg.QueryValueEx(key,'')[0])
            config=json.loads((manifest.parent/'profile.json').read_text(encoding='utf-8'))
            return Path(config['profile'])
        except (OSError,ValueError,KeyError):continue
    raise ValueError('Host not installed')


def main():
    if os.name=='nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(),os.O_BINARY);msvcrt.setmode(sys.stdout.fileno(),os.O_BINARY)
    try:bridge=BrowserBridge(caller_profile(sys.argv[1:]))
    except Exception:
        write_message(sys.stdout.buffer,dict(ok=False,error='Связь не установлена. Нажми «Подключить расширение» в TwitchTune.'));return
    try:
        while True:
            message=read_message(sys.stdin.buffer)
            if message is None:return
            try:
                if isinstance(message,dict) and message.get('action')=='check':
                    response=dict(ok=True,allowed=bridge.actionable(message.get('id'),message.get('lease'),message.get('channel')))
                else:response=bridge.handle(message)
            except (ValueError,KeyError):response=dict(ok=False,error='Действие недоступно: проверь канал, подключение TwitchTune и актуальность задания.')
            except Exception:response=dict(ok=False,error='Ошибка локального соединения с TwitchTune.')
            # Correlation ID is bounded and is not interpreted as code or a path.
            if isinstance(message,dict) and isinstance(message.get('requestId'),int):response['requestId']=message['requestId']
            write_message(sys.stdout.buffer,response)
    finally:bridge.close()


if __name__=='__main__':main()
