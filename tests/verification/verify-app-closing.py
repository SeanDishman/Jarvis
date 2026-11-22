from contextlib import ExitStack
import argparse
import ctypes
from ctypes import wintypes as wt
import importlib.util
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop import computer
from desktop.controller import Controller

helper_path = Path(__file__).with_name('verify-app-interaction.py')
spec = importlib.util.spec_from_file_location('app_interaction_verification', helper_path)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def main(reported_apps=False, already_closed=False):
    processes, targets = [], []
    names = (['Notepad', 'Spotify', 'Prism Launcher'] if reported_apps else ['Quartz Notes', 'Blue Music', 'Orbit Launcher']) + ['Keep Open Control']
    controller = None
    try:
        for index, name in enumerate(names):
            process = subprocess.Popen([sys.executable, str(helper_path), '--fixture-window'], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append(process)
            ready = queue.Queue()
            threading.Thread(target=lambda p=process, q=ready: q.put(p.stdout.readline()), daemon=True).start()
            record = json.loads(ready.get(timeout=10))
            hwnd = record['hwnd']
            computer.USER.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
            computer.USER.SetWindowTextW(hwnd, name)
            targets.append(computer.window_info(hwnd))
        if already_closed:
            computer.close_window(targets[2])
        identifiers = {row['hwnd'] for row in targets}
        real_windows = computer.visible_windows
        real_capture = computer.capture_window
        def listing():
            return [row for row in real_windows() if row['hwnd'] in identifiers]
        def fixture_frame(_rect):
            return real_capture(next(row for row in listing() if row['title'] == names[-1]))
        with helper.model_service() as request, ExitStack() as stack:
            for module in ('intent', 'acknowledgements', 'controller', 'research'):
                stack.enter_context(patch(f'desktop.{module}.local_request', side_effect=request))
            stack.enter_context(patch.object(computer, 'visible_windows', side_effect=listing))
            stack.enter_context(patch.object(computer, 'capture_rect', side_effect=fixture_frame))
            controller = Controller(queue.Queue())
            controller.preferences.read = lambda: {'offlineSpeech': 'auto'}
            controller.apps.apps = [{'Name': name, 'AppID': f'verify:{index}'} for index, name in enumerate(names)]
            controller.apps.launch = Mock(side_effect=AssertionError('No app should be launched during closing'))
            spoken = []
            controller.speak = spoken.append
            controller.voice_ready = True
            run = controller.computer.run
            def traced_run(job, update, check, ask):
                def traced_ask(*args):
                    action = ask(*args)
                    print('MODEL', json.dumps(action), flush=True)
                    return action
                return run(job, update, check, traced_ask)
            controller.computer.run = traced_run
            question = f'Close {names[2]}, {names[0]}, and {names[1]}. Leave {names[3]} alone.'
            started = controller.submit(question, speak=False)
            assert started.get('ok') and started.get('taskId'), started
            print('ACK', json.dumps(started['text']), flush=True)
            deadline = time.monotonic() + 210
            last_steps = 0
            while time.monotonic() < deadline:
                result = controller.activity_status(started['taskId'])
                steps = result.get('steps', [])
                for step in steps[last_steps:]:
                    print('STEP', step, flush=True)
                last_steps = len(steps)
                if result['status'] in ('completed', 'failed', 'cancelled'):
                    break
                time.sleep(.2)
            assert result['status'] == 'completed', result
            print('RESULT', json.dumps({key: result.get(key) for key in ('summary', 'model', 'reasoning', 'windowResults')}), flush=True)
            assert all(not computer.USER.IsWindow(row['hwnd']) for row in targets[:3]), listing()
            assert computer.USER.IsWindow(targets[-1]['hwnd']), 'Unrelated fixture was closed'
            assert len(result['windowResults']) == (2 if already_closed else 3) and all(row['verified'] for row in result['windowResults']), result
            controller.deliver_task_completion()
            assert spoken == [result['summary']], spoken
            controller.apps.launch.assert_not_called()
            print('PASS', json.dumps({'model': result['model'], 'reasoning': result.get('reasoning'),
                  'closed': [row['title'] for row in result['windowResults']], 'keptOpen': names[-1],
                  'completion': spoken[0]}), flush=True)
    finally:
        if controller:
            controller.tasks.close()
            controller.research.close()
            controller.browser.close()
        for row in targets:
            if computer.USER.IsWindow(row['hwnd']):
                computer.USER.PostMessageW(row['hwnd'], 0x0010, 0, 0)
        for process in processes:
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reported-apps', action='store_true')
    parser.add_argument('--already-closed', action='store_true')
    args = parser.parse_args()
    main(args.reported_apps, args.already_closed)
