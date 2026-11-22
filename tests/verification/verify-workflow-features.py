from contextlib import ExitStack
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
sys.path.insert(0, str(ROOT))
from desktop.common import local_request
from desktop.controller import Controller
from desktop.projects import Projects
from desktop.preferences import Preferences
from desktop.breaks import BreakState
from desktop.acknowledgements import acknowledge, FALLBACK_ACKNOWLEDGEMENT


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def completed(controller, identifier, timeout=100):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        row = controller.activity_status(identifier)
        if row['status'] in ('completed','failed','cancelled'):
            assert row['status'] == 'completed', row
            return row
        time.sleep(.1)
    raise AssertionError('Task did not finish: ' + json.dumps(controller.activity_status(identifier)))


def main():
    with tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='workflow-live-') as folder:
        folder = Path(folder)
        model_port, desktop_port = port(), port()
        while model_port == desktop_port:
            desktop_port = port()
        controller = Controller(queue.Queue())
        controller.projects = Projects(folder / 'projects')
        controller.preferences = Preferences(folder / 'settings.json')
        controller.breaks = BreakState(folder / 'break.json')
        threading.Thread(target=controller.serve, args=(desktop_port,), daemon=True).start()
        env = {**os.environ, 'JARVIS_MODEL_PORT':str(model_port), 'JARVIS_DESKTOP_PORT':str(desktop_port)}
        log = (folder / 'model.log').open('w', encoding='utf-8')
        process = subprocess.Popen([str(ROOT / '.runtime/node-v24.21.0-win-x64/node.exe'), str(ROOT / 'phone/luna-server.mjs')],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        action_started, release = threading.Event(), threading.Event()
        def request(destination, route, payload=None, timeout=10):
            return local_request(model_port if destination == 18791 else destination, route, payload, timeout)
        def browser(_action, _arguments, owner, _cancelled=None):
            assert 'Opera GX' in owner
            action_started.set()
            assert release.wait(60), 'Fixture action release timed out'
            return {'ok':True, 'title':'YouTube', 'url':'https://youtube.com/', 'text':'YouTube home page', 'elements':[]}
        def computer(job, update, check, ask):
            assert 'Opera GX' in job['question']
            action_started.set()
            assert release.wait(60), 'Fixture action release timed out'
            check(job)
            update(job, status='completed', stage='completed', summary='YouTube is open in Opera GX, sir.', finished=time.time())
        try:
            deadline = time.monotonic()+20
            while True:
                if process.poll() is not None:
                    raise RuntimeError('The isolated model service exited')
                try:
                    local_request(model_port, '/health', timeout=1)
                    local_request(desktop_port, '/health', timeout=1)
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise RuntimeError('The isolated services did not start') from None
                    time.sleep(.2)
            with ExitStack() as stack:
                for module in ('desktop.controller','desktop.intent','desktop.research','desktop.acknowledgements'):
                    stack.enter_context(patch(module+'.local_request', side_effect=request))
                if '--ack-only' in sys.argv:
                    for index, (utterance, targets) in enumerate((
                        ('Jarvis can you see the dirvers on my pc right', ('driver',)),
                        ('Jarvis start up YouTube on Opera GX', ('youtube','opera')),
                        ('Put Chrome on the left and my notes on the right', ('chrome','notes')),
                    )):
                        began = time.monotonic()
                        answer = acknowledge(utterance, 'verify-ack-' + str(index))
                        elapsed = time.monotonic()-began
                        assert answer != FALLBACK_ACKNOWLEDGEMENT, (utterance,answer,elapsed)
                        assert all(target in answer.lower() for target in targets), (utterance,answer)
                        assert 'sir' in answer.lower() and len(answer) <= 180 and elapsed < 6, (answer,elapsed)
                        if index == 0:
                            assert any(word in answer.lower() for word in ('check','whether','see if')), answer
                        print(f'PASS: generated in {elapsed:.3f}s: {answer}', flush=True)
                    return
                if '--layout-only' in sys.argv:
                    windows = {'ok':True, 'windows':[{'id':'chrome-window','app':'chrome.exe','title':'Chrome'},
                               {'id':'notes-window','app':'notepad.exe','title':'Notes'}],
                               'monitors':[{'id':'display-1','number':1,'primary':True,'laptop':True,'work':[0,0,1920,1040]}]}
                    placed = []
                    def arrange(_context, placements, _cancelled=None):
                        placed.extend(placements)
                        return {'ok':True, 'windows':[{'app':item['id'], 'verified':True} for item in placements]}
                    stack.enter_context(patch.object(controller.layouts, 'listing', return_value=windows))
                    stack.enter_context(patch.object(controller.layouts, 'arrange', side_effect=arrange))
                    result = controller.submit('Put Chrome on the left and my notes on the right.', speak=False)
                    done = completed(controller, result['taskId'])
                    actual = {item['id']:item['position'] for item in placed}
                    assert actual == {'chrome-window':'left','notes-window':'right'}, (actual,done)
                    print('PASS: real model mapped the voice layout request to both window references and correct positions', flush=True)
                    return
                stack.enter_context(patch.object(controller.browser, '_perform', side_effect=browser))
                stack.enter_context(patch.object(controller.computer, 'run', side_effect=computer))
                began = time.monotonic()
                result = controller.submit('Start up YouTube on Opera GX.', speak=False)
                elapsed = time.monotonic()-began
                assert result.get('taskId') and elapsed < 6, result
                print(f'PASS: acknowledgement returned in {elapsed:.3f}s before live classification', flush=True)
                assert action_started.wait(60), controller.activity_status(result['taskId'])
                chat = controller.submit('What is two plus two?', speak=False)
                assert chat.get('ok') and ('four' in chat['text'].lower() or '4' in chat['text']), chat
                assert controller.activity_status(result['taskId'])['status'] == 'running'
                print('PASS: live foreground model answered while the separate task held a fixture action', flush=True)
                release.set()
                task = completed(controller, result['taskId'])
                print('PASS: live routing completed the Opera GX fixture through ' + str(task['model']), flush=True)

                fixture = {'id':'saved-research-fixture','question':'Compare laptops for battery life and portability', 'scope':'web',
                    'provider':'all','status':'completed','stage':'completed','note':'Complete','summary':'Atlas lasts twelve hours and weighs 1.2 kilograms. Birch lasts eight hours and weighs 1.5 kilograms.',
                    'sources':[{'title':'Manufacturer comparison','url':'https://example.com/laptops'}], 'created':time.time(), 'finished':time.time(),
                    'keywords':[], 'queries':[], 'sourcesFound':1, 'pagesRead':1, 'warnings':[], 'model':'fixture', 'cancel':threading.Event()}
                with controller.research.lock:
                    controller.research.jobs[fixture['id']] = fixture
                result = controller.submit('Save my latest research as a project called Laptop research. My decision is to prioritize battery life. The next step is to compare warranties.', speak=False)
                completed(controller, result['taskId'])
                saved = Projects(folder / 'projects').list('laptop')['projects']
                assert len(saved) == 1, saved
                row = Projects(folder / 'projects').read(saved[0]['id'])
                assert row['sources'] == fixture['sources'] and 'Atlas' in row['findings'], row
                assert row['next_steps'] and row['decisions'], row
                print('PASS: real model saved source-backed findings, decisions and next steps; reopened after store restart', flush=True)

                stack.enter_context(patch('desktop.research.search_web', return_value={'ok':True, 'results':[{'title':'Laptop warranties', 'url':'https://example.com/laptops', 'snippet':'Atlas offers a two year warranty. Birch offers one year.'}]}))
                stack.enter_context(patch('desktop.research.read_webpage', return_value={'ok':True,'text':'Atlas has a two year manufacturer warranty. Birch has a one year warranty.'}))
                result = controller.submit('Continue yesterday’s laptop research.', speak=False)
                resumed = completed(controller, result['taskId'])
                assert resumed['sources'] and resumed.get('childId'), resumed
                assert Projects(folder / 'projects').read(row['id'])['revision'] == row['revision']
                print('PASS: real model resumed saved public research with a new independent job and preserved the saved revision', flush=True)
        finally:
            release.set()
            controller.tasks.close()
            controller.research.close()
            controller.browser.close()
            if controller.server:
                controller.server.shutdown(); controller.server.server_close()
            try:
                local_request(model_port, '/shutdown', {}, timeout=2)
                process.wait(timeout=3)
            except Exception:
                process.terminate(); process.wait(timeout=3)
            log.close()


if __name__ == '__main__':
    main()
