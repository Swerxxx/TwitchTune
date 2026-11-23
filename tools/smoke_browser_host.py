"""Windows CI: exchange real native-messaging frames with the packaged host."""
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from browser_bridge import BrowserBridge, register_native_host, CHROMIUM_ID, HOST_NAME
from browser_native_host import read_message
from core import QueueStore
from twitch_completion import TwitchCompletions
import io


def main():
    if sys.platform!='win32':raise RuntimeError('Windows check only')
    import winreg
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);store=QueueStore(root/'queue.sqlite3');TwitchCompletions(store)
        service=BrowserBridge(root);service.heartbeat(True,SimpleNamespace(client_id='app',user={'user_id':'123','login':'smoke'}))
        manifest=register_native_host(root,'chrome')
        content=json.loads(manifest.read_text(encoding='utf8'));content['path']=str(Path(sys.argv[1]).resolve())
        manifest.write_text(json.dumps(content),encoding='utf8')
        payload=json.dumps({'action':'status','requestId':7}).encode()
        try:
            result=subprocess.run([content['path'],'chrome-extension://'+CHROMIUM_ID+'/','0'],
                input=struct.pack('=I',len(payload))+payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40,check=True)
            response=read_message(io.BytesIO(result.stdout))
            assert response['ok'] is True and response['channel']=='smoke' and response['requestId']==7,response
            print('Compiled native host framing and profile lookup passed')
        finally:
            service.close();store.db.close()
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,'Software\\Google\\Chrome\\NativeMessagingHosts\\'+HOST_NAME)


if __name__=='__main__':main()
