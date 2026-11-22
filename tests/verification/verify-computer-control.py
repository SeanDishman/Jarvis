import base64
import io
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
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop.common import local_request
from desktop import computer
from desktop.intent import classify_request
from desktop.research import ResearchJobs, response_text
from PIL import Image, ImageDraw, ImageFont


def fixture_image(state):
    canvas = Image.new('RGB', (1000, 700), '#18212f')
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 24)
    small = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 18)
    if not state['opened']:
        draw.text((35, 30), 'Windows desktop', font=font, fill='white')
    else:
        draw.rectangle((0, 0, 999, 699), fill='#f6f8fa')
        draw.rectangle((0, 0, 999, 49), fill='#233147')
        draw.text((20, 8), 'Nebula Shelf - Library', font=font, fill='white')
        draw.text((30, 76), 'Find notes in your library', font=font, fill='#121a24')
        draw.rectangle((30, 130, 750, 180), fill='white', outline='#2168d4' if state['focused'] else '#666666', width=3)
        draw.text((42, 140), state['text'] or 'Search notes...', font=small, fill='#222222')
        draw.rectangle((775, 130, 965, 180), fill='#2168d4')
        draw.text((827, 140), 'Search', font=small, fill='white')
        if state['results']:
            draw.text((30, 245), 'Search results for: ' + state['text'], font=font, fill='#152a48')
            draw.text((30, 305), 'quarterly notes.txt', font=font, fill='#153f90')
            draw.text((30, 350), 'Quarterly notes: product planning and project updates.', font=small, fill='#222222')
            draw.text((30, 395), '1 matching note', font=small, fill='#222222')
        else:
            draw.text((30, 245), 'Recent notes', font=font, fill='#152a48')
            draw.text((30, 310), 'Welcome to your library', font=small, fill='#222222')
    data = io.BytesIO()
    canvas.save(data, format='PNG')
    return {'image': base64.b64encode(data.getvalue()).decode('ascii'), 'width': 1000, 'height': 700,
            'rect': [0, 0, 1000, 700], 'captured': time.monotonic()}


def run_fixture(request):
    state = {'opened': False, 'foreground': 0, 'focused': False, 'text': '', 'results': False}
    apps = Mock()
    apps.apps = [{'Name': 'Nebula Shelf', 'AppID': 'fixture-entry'}]
    def launch(name):
        assert name == 'Nebula Shelf', name
        state['opened'] = True
        return {'ok': True}
    apps.launch.side_effect = launch
    target = {'hwnd': 41, 'pid': 99991, 'process': 'shelf-fixture.exe', 'title': 'Nebula Shelf - Library', 'rect': [0, 0, 1000, 700]}
    control = computer.ComputerControl(apps)
    jobs = ResearchJobs(queue.Queue(), Mock(), control)
    job = {'id': 'fixture-desktop', 'question': 'Open Nebula Shelf and find quarterly notes',
           'scope': 'computer', 'created': time.time(), 'cancel': threading.Event(),
           'deadline': time.monotonic() + 240, '_foreground': 0, 'model': request(18791, '/models')['conversationModel']}
    seen = []
    def apply(target, frame, action, owner, check):
        check()
        operation = action['action']
        if operation == 'click':
            if 30 <= action['x'] <= 750 and 130 <= action['y'] <= 180:
                state['focused'] = True
            elif 775 <= action['x'] <= 965 and 130 <= action['y'] <= 180:
                assert state['text'].casefold() == 'quarterly notes', state
                state['results'] = True
            else:
                raise AssertionError('Clicked outside the fixture controls: ' + str(action))
        elif operation == 'type':
            assert state['focused'], 'Typing without focusing the search field'
            state['text'] += action['text']
        elif operation == 'key' and action['key'] in ('ctrl+f', 'ctrl+l'):
            state['focused'] = True
        elif operation == 'key' and action['key'] == 'enter':
            assert state['focused'] and state['text'].casefold() == 'quarterly notes', state
            state['results'] = True
        elif operation == 'key' and action['key'] in ('ctrl+a', 'select_all'):
            assert state['focused']
            state['text'] = ''
        else:
            raise AssertionError('Unexpected fixture input: ' + str(action))
    def ask(job, frame, steps, readonly):
        assert len(seen) < 12, 'The model did not finish the fixture task'
        action = jobs._computer_step(job, frame, steps, readonly)
        seen.append(action['action'])
        print('MODEL STEP:', json.dumps({key: action[key] for key in ('action', 'target', 'key', 'text', 'message')}), flush=True)
        if action['action'] == 'done':
            assert state['results'], 'Model claimed success before viewing search results'
        return action
    with patch('desktop.research.local_request', side_effect=request), \
         patch.object(computer, 'require_desktop'), patch.object(computer, 'USER') as user, \
         patch.object(computer, 'window_info', side_effect=lambda hwnd: target if hwnd == 41 and state['opened'] else None), \
         patch.object(computer, 'visible_windows', side_effect=lambda: [target] if state['opened'] else []), \
         patch.object(computer, 'bring_to_front', side_effect=lambda hwnd: state.update(foreground=hwnd)), \
         patch.object(computer, 'keep_on_top'), \
         patch.object(computer, 'monitors', return_value=[{'device': 'fixture', 'rect': [0, 0, 1000, 700], 'primary': True, 'internal': True}]), \
         patch.object(computer, 'capture_rect', side_effect=lambda rect: fixture_image(state)), \
         patch.object(computer, 'capture_window', side_effect=lambda target: fixture_image(state)), \
         patch.object(computer, 'apply_action', side_effect=apply):
        user.GetForegroundWindow.side_effect = lambda: state['foreground']
        user.SetThreadDpiAwarenessContext.return_value = 0
        user.IsIconic.return_value = False
        control.run(job, jobs._update, jobs._check, ask)
    assert job['status'] == 'completed' and state['results'], job
    assert all(action in seen for action in ('open_app', 'type', 'done')), seen
    assert any(action in seen for action in ('inspect_window', 'focus_window')), seen
    print('PASS: real model discovered, opened, selected, typed in and verified an unfamiliar app on fixture screens; no real input sent', flush=True)


def main():
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix='desktop-model-check-') as temporary:
        folder = Path(temporary).resolve()
        assert folder.is_relative_to(cache.resolve())
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        environment = os.environ.copy()
        environment['JARVIS_MODEL_PORT'] = str(port)
        with (folder / 'model.log').open('w', encoding='utf-8') as output:
            server = subprocess.Popen([str(ROOT / '.runtime/node-v24.21.0-win-x64/node.exe'), str(ROOT / 'phone/luna-server.mjs')],
                                      cwd=ROOT, env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            def request(service, route, payload=None, timeout=10):
                response = local_request(port if service == 18791 else service, route, payload, timeout)
                if route == '/v1/responses' and payload.get('model') == 'openclaw/computer':
                    try:
                        json.loads(response_text(response))
                    except ValueError:
                        print('UNREADABLE FIXTURE RESPONSE:', repr(response_text(response)), flush=True)
                return response
            try:
                deadline = time.monotonic() + 20
                while True:
                    if server.poll() is not None:
                        raise RuntimeError('Isolated model service exited')
                    try:
                        health = request(18791, '/health', timeout=1)
                        assert health['generalDesktopControl']
                        break
                    except Exception:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Isolated model service did not start') from None
                        time.sleep(.2)
                with patch('desktop.intent.local_request', side_effect=request):
                    cases = [('Open Opera GX and search for gaming laptops', 'computer'),
                             ('Open Dropbox and find my Tax 2025 folder', 'computer'),
                             ('Bring up Nebula Shelf and find quarterly notes', 'computer'),
                             ('How do I search for a folder in Dropbox?', 'normal'),
                             ('Remind me tomorrow to open Dropbox', 'normal')]
                    for index, (utterance, expected) in enumerate([] if '--fixture-only' in sys.argv else cases):
                        decision = classify_request(utterance, 'desktop-route-fixture-' + str(index))
                        assert decision['action'] == expected, (utterance, decision)
                        if expected == 'computer':
                            assert not decision['needs_clarification'], (utterance, decision)
                        print('PASS: model classified ' + repr(utterance) + ' as ' + expected, flush=True)
                run_fixture(request)
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == '__main__':
    main()
