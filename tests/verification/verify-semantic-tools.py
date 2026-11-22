from contextlib import ExitStack
import ctypes
from ctypes import wintypes as wt
import importlib.util
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop import computer
from desktop.controller import Controller
from desktop.displays import monitor_number, containing_display
from desktop.windows import monitors

spec = importlib.util.spec_from_file_location('verification_helper', Path(__file__).with_name('verify-app-interaction.py'))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def main():
    processes, targets = [], []
    controller = Controller(queue.Queue())
    controller.tasks = None
    controller.task_cancel = threading.Event()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    server_thread = threading.Thread(target=controller.serve, args=(port,), daemon=True)
    server_thread.start()
    try:
        for name in ('Prism Launcher', 'Task Manager', 'Keep Open Control'):
            process = subprocess.Popen([sys.executable, str(helper.__file__), '--fixture-window'], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append(process)
            ready = queue.Queue()
            threading.Thread(target=lambda p=process,q=ready:q.put(p.stdout.readline()),daemon=True).start()
            hwnd = json.loads(ready.get(timeout=10))['hwnd']
            computer.USER.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
            computer.USER.SetWindowTextW(hwnd, name)
            targets.append(computer.window_info(hwnd))
        screens = monitors()
        assert len(screens) >= 2, 'This verification needs two connected displays.'
        destination = next(s for s in screens if s != containing_display(targets[0]['rect'], screens))
        number = monitor_number(destination, screens.index(destination))
        with patch.dict(os.environ, {'JARVIS_DESKTOP_PORT':str(port)}), helper.model_service() as request, ExitStack() as stack:
            for module in ('intent','controller','research'):
                stack.enter_context(patch(f'desktop.{module}.local_request', side_effect=request))
            stack.enter_context(patch('desktop.window_layouts.enumerate_windows', side_effect=lambda:[computer.window_info(row['hwnd']) for row in targets]))
            audio = stack.enter_context(patch('desktop.system_tools.audio_control', return_value={'ok':True,'volume':34,'muted':False}))
            controller.apps.apps = [{'Name':'Quartz Editor','AppID':'verify:quartz'}, {'Name':'Blue Player','AppID':'verify:blue'}]
            controller.apps.launch = Mock(return_value={'ok':True,'app':'Quartz Editor','status':'launch_requested'})
            result = controller.respond(f'Move Prism Launcher to the left half of monitor {number} and Task Manager to the right half of the same monitor. Leave Keep Open Control alone.', speak=False)
            print('LAYOUT', json.dumps(result), flush=True)
            assert result['ok'] and 'arrange_windows' in result['tools'], result
            for index, row in enumerate(targets[:2]):
                actual = computer.window_info(row['hwnd'])
                assert containing_display(actual['rect'], screens) == destination, actual
                middle = (destination['work'][0]+destination['work'][2])//2
                assert abs(actual['rect'][2 if index == 0 else 0]-middle) <= 4, actual
            assert computer.window_info(targets[2]['hwnd'])['rect'] == targets[2]['rect'], 'Unrelated window moved'
            result = controller.respond('Raise the audio a bit', speak=False)
            print('AUDIO', json.dumps(result), flush=True)
            assert result['ok'] and 'windows_control' in result['tools'], result
            assert audio.call_args.args[0] == 'volume_up', audio.call_args
            result = controller.respond('Can you bring up Quartz Editor?', speak=False)
            print('LAUNCH', json.dumps(result), flush=True)
            assert result['ok'] and 'launch_app' in result['tools'], result
            controller.apps.launch.assert_called_once_with('Quartz Editor')
            audio.side_effect = RuntimeError('The verification audio adapter is unavailable.')
            result = controller.respond('Bring the audio down a little', speak=False)
            print('FAILED_TOOL', json.dumps(result), flush=True)
            assert not result['ok'] and result['outcome'] == 'failed', result
            print('PASS: real AI tool calls moved only disposable windows on the requested display; audio and launch adapters were mocked, and a tool refusal was reported as failed.', flush=True)
    finally:
        controller.research.close()
        controller.browser.close()
        if controller.server:
            controller.server.shutdown()
            controller.server.server_close()
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
    main()
