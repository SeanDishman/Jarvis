import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
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
from desktop import computer
from desktop.common import local_request
from desktop.intent import classify_request
from desktop.research import ResearchJobs
from desktop.windows import AppCatalog


def fixture_window():
    user, kernel = computer.USER, computer.KERNEL
    result_type = ctypes.c_ssize_t
    callback_type = ctypes.WINFUNCTYPE(result_type, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
    user.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user.DefWindowProcW.restype = result_type
    user.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    user.CreateWindowExW.restype = wt.HWND
    user.SetWindowTextW.argtypes = [wt.HWND, wt.LPCWSTR]
    user.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user.SendMessageW.restype = result_type
    kernel.GetModuleHandleW.restype = wt.HMODULE
    controls = {}
    def read(hwnd):
        text = ctypes.create_unicode_buffer(1024)
        user.GetWindowTextW(hwnd, text, len(text))
        return text.value
    def handler(hwnd, message, wparam, lparam):
        if message == 0x0111 and wparam & 0xFFFF == 103:
            details = bool(user.SendMessageW(controls['details'], 0x00F0, 0, 0))
            value = 'Preview: ' + read(controls['name']) + '. Details: ' + ('included' if details else 'excluded')
            user.SetWindowTextW(controls['result'], value)
            print(json.dumps({'preview': value}), flush=True)
            return 0
        if message == 0x0002:
            user.PostQuitMessage(0)
            return 0
        return user.DefWindowProcW(hwnd, message, wparam, lparam)
    callback = callback_type(handler)
    class WindowClass(ctypes.Structure):
        _fields_ = [('style', wt.UINT), ('proc', callback_type), ('classExtra', ctypes.c_int),
            ('windowExtra', ctypes.c_int), ('instance', wt.HINSTANCE), ('icon', wt.HICON),
            ('cursor', wt.HANDLE), ('background', wt.HBRUSH), ('menu', wt.LPCWSTR), ('name', wt.LPCWSTR)]
    window_class = WindowClass()
    window_class.proc = callback
    window_class.instance = kernel.GetModuleHandleW(None)
    window_class.background = 6
    window_class.name = 'JarvisControlWorkshopFixture'
    if not user.RegisterClassW(ctypes.byref(window_class)):
        raise ctypes.WinError()
    hwnd = user.CreateWindowExW(0, window_class.name, 'Control Workshop', 0x10CF0000,
        100, 100, 760, 420, None, None, window_class.instance, None)
    def add(kind, label, style, x, y, width, height, identifier):
        return user.CreateWindowExW(0, kind, label, 0x50000000 | style, x, y, width, height,
            hwnd, identifier, window_class.instance, None)
    add('STATIC', 'Name', 0, 25, 25, 100, 25, 100)
    controls['name'] = add('EDIT', '', 0x00810080, 25, 55, 500, 30, 101)
    controls['details'] = add('BUTTON', 'Include details', 0x10003, 25, 110, 230, 30, 102)
    add('BUTTON', 'Preview', 0x10000, 25, 165, 140, 35, 103)
    controls['result'] = add('STATIC', 'Nothing previewed', 0, 25, 235, 670, 50, 104)
    print(json.dumps({'hwnd': hwnd, 'controls': controls}), flush=True)
    message = wt.MSG()
    while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user.TranslateMessage(ctypes.byref(message))
        user.DispatchMessageW(ctypes.byref(message))


@contextmanager
def model_service():
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix='app-interaction-') as temporary:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        environment = {**os.environ, 'JARVIS_MODEL_PORT': str(port)}
        with open(Path(temporary) / 'model.log', 'w', encoding='utf-8') as output:
            server = subprocess.Popen([str(ROOT / '.runtime/node-v24.21.0-win-x64/node.exe'),
                str(ROOT / 'phone/luna-server.mjs')], cwd=ROOT, env=environment,
                stdout=output, stderr=output, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            def request(service, route, payload=None, timeout=10):
                return local_request(port if service == 18791 else service, route, payload, timeout)
            try:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        request(18791, '/health', timeout=1)
                        break
                    except Exception:
                        if server.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('Isolated model service failed to start') from None
                        time.sleep(.2)
                yield request
            finally:
                server.terminate()
                server.wait(timeout=10)


def run(question, request, apps, **checks):
    with patch('desktop.intent.local_request', side_effect=request):
        intent = classify_request(question, 'verify-generic-ui-' + str(time.time_ns()))
    assert intent['action'] == 'computer' and not intent['needs_clarification'], intent
    print('INTENT', json.dumps(intent), flush=True)
    engine = computer.ComputerControl(apps)
    jobs = ResearchJobs(queue.Queue(), Mock(), engine)
    job = {'id': 'verify-generic-ui-' + str(time.time_ns()), 'question': question,
        'scope': 'computer', 'created': time.time(), 'cancel': threading.Event(),
        'deadline': time.monotonic() + 240, '_foreground': int(computer.USER.GetForegroundWindow() or 0),
        'model': request(18791, '/models')['conversationModel']}
    actions = []
    def ask(job, frame, steps, readonly):
        action = jobs._computer_step(job, frame, steps, readonly)
        actions.append(action['action'])
        print('STEP', json.dumps(action), flush=True)
        if action['action'] == 'done' and checks.get('verify'):
            checks['verify']()
        return action
    with patch('desktop.research.local_request', side_effect=request):
        engine.run(job, jobs._update, jobs._check, ask)
    assert job['status'] == 'completed', job.get('summary')
    print('COMPLETED', json.dumps({'summary': job['summary'], 'actions': actions}), flush=True)
    return actions


def run_fixture(request):
    process, windows = None, []
    apps = Mock(apps=[{'Name': 'Control Workshop', 'AppID': 'disposable-fixture'}])
    fixture_pid = None
    def launch(name):
        nonlocal process, windows, fixture_pid
        assert name == 'Control Workshop'
        assert process is None, 'Duplicate process launch'
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--fixture-window'],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
        value = ready.get(timeout=8)
        if not value:
            raise RuntimeError('Fixture window did not appear: ' + process.stderr.read()[:800])
        record = json.loads(value)
        windows = [computer.window_info(record['hwnd'])]
        fixture_pid = windows[0]['pid']
        return {'ok': True}
    apps.launch.side_effect = launch
    real_windows = computer.visible_windows
    def listing():
        return [w for w in real_windows() if fixture_pid and w['pid'] == fixture_pid]
    def verify():
        from desktop.accessibility import Accessibility
        snapshot = Accessibility().inspect(listing()[0])
        assert any('Preview: Quarterly notes. Details: included' in row['name'] for row in snapshot['controls']), snapshot
    try:
        with patch.object(computer, 'visible_windows', side_effect=listing):
            actions = run('Open Control Workshop, enter Quarterly notes in Name, enable Include details, '
                'click Preview, and verify that the preview includes the name and details.', request, apps, verify=verify)
        assert 'open_app' in actions and 'set_value' in actions and 'invoke' in actions, actions
        verify()
        print('PASS: real app launched; field edited, checkbox enabled, button invoked, resulting preview verified.', flush=True)
    finally:
        if process:
            computer.USER.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
            for window in windows:
                computer.USER.PostMessageW(window['hwnd'], 0x0010, 0, 0)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                import psutil
                for child in psutil.Process(process.pid).children(recursive=True):
                    child.terminate()
                process.terminate()
                process.communicate(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture-window', action='store_true')
    parser.add_argument('--live')
    args = parser.parse_args()
    if args.fixture_window:
        fixture_window()
    else:
        with model_service() as request:
            if args.live:
                apps = AppCatalog()
                apps.refresh()
                run(args.live, request, apps)
            else:
                run_fixture(request)
