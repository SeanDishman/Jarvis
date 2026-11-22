import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from desktop.common import local_request
from desktop.controller import Controller
from desktop.preferences import Preferences
from desktop.files import FileTools
from desktop.personal import PersonalTools
from desktop.breaks import BreakState


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def main():
    with tempfile.TemporaryDirectory(dir=ROOT/'.cache',prefix='personal-live-') as temporary:
        folder = Path(temporary)
        model_port,desktop_port = port(),port()
        while desktop_port == model_port:
            desktop_port = port()
        controller = Controller(queue.Queue())
        controller.breaks = BreakState(folder/'break.json')
        controller.preferences = Preferences(folder/'settings.json')
        controller.preferences.save({'memoryVault':str(folder/'Brain'),'fileWriteRoots':[str(folder/'Jarvis Files')],
                                     'monitorFolders':[],'monitorEnabled':False,'offlineSpeech':'auto'})
        controller.files = FileTools(controller.preferences)
        controller.personal = PersonalTools(controller.preferences,controller.files,controller.events)
        controller.personal.documents.backup_root = folder/'backups'
        controller.personal.search.database = folder/'index.sqlite3'
        threading.Thread(target=controller.serve,args=(desktop_port,),daemon=True).start()
        env = os.environ.copy()
        env.update(JARVIS_MODEL_PORT=str(model_port),JARVIS_DESKTOP_PORT=str(desktop_port))
        log = (folder/'model.log').open('w',encoding='utf-8')
        process = subprocess.Popen([str(ROOT/'.runtime/node-v24.21.0-win-x64/node.exe'),str(ROOT/'phone/luna-server.mjs')],
            cwd=ROOT,env=env,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        def request(target,route,payload=None,timeout=10):
            return local_request(model_port if target == 18791 else target,route,payload,timeout)
        try:
            deadline = time.monotonic()+25
            while True:
                try:
                    local_request(model_port,'/health',timeout=1)
                    break
                except Exception:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Isolated model service failed to start')
                    time.sleep(.2)
            cases = [
                ('Remember that the test project codeword is cedar lantern.','save_memory'),
                ('Recall the test project codeword.','search_memory'),
                ('Create a text file named integration-note.txt containing exactly alpha in Jarvis Files.','create_document'),
                ('Edit integration-note.txt and replace alpha with beta.','edit_document'),
                ('Find the document containing beta.','search_document_content'),
            ]
            with patch('desktop.controller.local_request',side_effect=request),patch('desktop.intent.local_request',side_effect=request):
                for command,expected in cases:
                    print('Checking ' + expected + '...',flush=True)
                    result = controller.respond(command,speak=False)
                    assert result.get('ok') and expected in result.get('tools',[]),result
                    print(json.dumps({'tool':expected,'tools':result['tools'],'answer':result['text']}),flush=True)
            assert (folder/'Jarvis Files/integration-note.txt').read_text() == 'beta'
            assert controller.personal.memory.search('cedar')['memories']
        finally:
            controller.closed.set()
            controller.browser.close()
            controller.research.close()
            if controller.server:
                controller.server.shutdown()
                controller.server.server_close()
            try:
                local_request(model_port,'/shutdown',{},timeout=2)
                process.wait(timeout=3)
            except Exception:
                process.terminate()
                process.wait(timeout=3)
            log.close()


if __name__ == '__main__':
    main()
