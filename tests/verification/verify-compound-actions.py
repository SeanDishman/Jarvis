"""Real model routing plus disposable Windows processes and a native UI fixture."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop import computer, process_control as pc
from desktop.accessibility import Accessibility
from desktop.controller import Controller
from desktop.intent import classify_request

spec = importlib.util.spec_from_file_location('helper', Path(__file__).with_name('verify-app-interaction.py'))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def semantic_cases():
    cases = [
        ('Force Quit. Telegram. And Discord. And on my Opera GX. Open YouTube.', {'Telegram', 'Discord'}, 'Opera GX'),
        ('Force quit Discord and Telegram, then open YouTube in Opera GX.', {'Discord', 'Telegram'}, 'Opera GX'),
        ('Open example.com in Quartz Browser, and force quit Nebula and Lark.', {'Nebula', 'Lark'}, 'Quartz Browser'),
        ('Force quit Nebula and Lark. Leave Quartz Browser running.', {'Nebula', 'Lark'}, None),
    ]
    for text, names, browser in cases:
        selected = classify_request(text, 'verify-' + uuid.uuid4().hex)
        steps = selected.get('steps') if selected['action'] == 'compound' else [selected]
        termination = [step for step in steps if 'terminate_processes' in step['capabilities']]
        assert len(termination) == 1 and set(termination[0]['process_names']) == names, selected
        if browser:
            ui = [step for step in steps if step['action'] == 'computer']
            assert len(ui) == 1 and ui[0]['browser'] == browser and not ui[0]['process_names'], selected
            assert not ui[0]['depends_on'], selected
        print('ROUTED', json.dumps({'request': text, 'steps': steps}), flush=True)
    prior = {'topic': 'processes', 'lastAnswer': 'Telegram and Discord could not be stopped.'}
    followup = classify_request('On my Opera GX, open YouTube.', 'verify-' + uuid.uuid4().hex, prior)
    assert followup['action'] == 'computer' and 'terminate_processes' not in followup['capabilities'], followup
    print('FOLLOWUP', json.dumps(followup), flush=True)


def verify():
    children, window = [], None
    controller = Controller(queue.Queue())
    original = int(computer.USER.GetForegroundWindow() or 0)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    threading.Thread(target=controller.serve, args=(port,), daemon=True).start()
    try:
        with ExitStack() as fixture:
            temporary = fixture.enter_context(tempfile.TemporaryDirectory(prefix='compound-', dir=ROOT / '.cache'))
            def stop_children():
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)
            fixture.callback(stop_children)
            for name, count in [('ProbeChat', 8), ('ProbeMessage', 1), ('ProbeGuard', 1)]:
                exe = Path(temporary) / (name + '.exe')
                shutil.copy2(Path(os.environ['SystemRoot']) / 'System32/ping.exe', exe)
                for _ in range(count):
                    children.append(subprocess.Popen([str(exe), '-t', '127.0.0.1'],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW))
            window = subprocess.Popen([sys.executable, str(helper.__file__), '--fixture-window'], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(window.stdout.readline()), daemon=True).start()
            hwnd = json.loads(ready.get(timeout=10))['hwnd']
            screens = computer.monitors()
            work = next((screen['work'] for screen in screens if not screen['primary']), screens[0]['work'])
            computer.USER.SetWindowPos(hwnd, None, work[0] + 80, work[1] + 80, 760, 420, 0x14)
            real_windows, protected = computer.visible_windows, pc.protected_pids
            inventory = pc.enumerate_processes
            def processes():
                rows = inventory()
                allowed = pc.descendants(rows, {child.pid for child in children})
                return [row for row in rows if row['pid'] in allowed]
            def windows():
                return [row for row in real_windows() if row['hwnd'] == hwnd]
            real_execute = Controller.execute_tool
            def trace_worker(worker, context, name, args):
                if name == 'terminate_processes':
                    assert set(context['processNames']) == {'ProbeChat', 'ProbeMessage'}, context['processNames']
                    assert 'Workshop' not in context['ownerText'], context['ownerText']
                result = real_execute(worker, context, name, args)
                print('TOOL', json.dumps({'name': name, 'arguments': args, 'ok': result.get('ok'),
                     'error': result.get('error')}), flush=True)
                return result
            with patch.dict(os.environ, {'JARVIS_DESKTOP_PORT': str(port)}), helper.model_service() as request, ExitStack() as stack:
                for module in ('intent', 'controller', 'research', 'acknowledgements'):
                    stack.enter_context(patch('desktop.' + module + '.local_request', side_effect=request))
                stack.enter_context(patch.object(computer, 'visible_windows', side_effect=windows))
                stack.enter_context(patch.object(pc, 'enumerate_processes', side_effect=processes))
                stack.enter_context(patch.object(pc, 'protected_pids', side_effect=lambda rows: protected(rows) - pc.descendants(rows, {p.pid for p in children[:-1]})))
                stack.enter_context(patch.object(Controller, 'execute_tool', trace_worker))
                stack.enter_context(patch.object(controller.apps, 'launch', side_effect=AssertionError('Unexpected app launch')))
                semantic_cases()
                question = ('Force Quit. ProbeChat. And ProbeMessage. And in Control Workshop. '
                            'Set Name to Mixed task verified, click Preview, and verify the preview. Leave ProbeGuard alone.')
                result = controller.respond(question, speak=False)
                assert result.get('ok') and result.get('taskId'), result
                deadline = time.monotonic() + 160
                while time.monotonic() < deadline:
                    job = controller.activity_status(result['taskId'])
                    if job['status'] in ('completed', 'failed', 'cancelled'):
                        break
                    time.sleep(.2)
                print('RESULT', json.dumps(job), flush=True)
                assert job['status'] == 'completed', job
                assert all(child.poll() is not None for child in children[:-1]), 'A requested process survived'
                assert children[-1].poll() is None, 'The unrelated process was terminated'
                assert window.poll() is None, 'The UI action target was terminated'
                snapshot = Accessibility().inspect(computer.window_info(hwnd))
                assert any('Preview: Mixed task verified' in row['name'] for row in snapshot['controls']), snapshot
                assert len(job['actionResults']) == 2 and all(row['ok'] for row in job['actionResults']), job
                print('PASS: nine processes exited, unrelated process and UI app survived, native UI action verified.', flush=True)
    finally:
        controller.tasks.close()
        controller.research.close()
        controller.browser.close()
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        if window:
            restore = int(computer.USER.GetForegroundWindow() or 0) == hwnd
            window.terminate()
            window.communicate(timeout=5)
            if restore and original and computer.USER.IsWindow(original):
                computer.USER.SetForegroundWindow(original)
        if controller.server:
            controller.server.shutdown()
            controller.server.server_close()


if __name__ == '__main__':
    verify()
