import argparse
import ctypes
import importlib.util
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop import computer
from desktop.accessibility import Accessibility


def cover_window(left, top):
    import tkinter as tk
    root = tk.Tk()
    root.title('Jarvis background control test cover')
    root.geometry('950x760+50+50')
    root.configure(bg='#17365d')
    root.attributes('-topmost', True)
    tk.Label(root, text='Testing background browser control', bg='#17365d', fg='white').pack(pady=40)
    root.update()
    hwnd = computer.USER.GetAncestor(root.winfo_id(), 2)
    computer.USER.SetWindowPos(hwnd, None, left, top, 950, 760, 0x14)
    computer.bring_to_front(hwnd)
    print(json.dumps({'hwnd': hwnd}), flush=True)
    previous = None
    def observe_focus():
        nonlocal previous
        active = computer.USER.GetForegroundWindow()
        if active != previous:
            print(json.dumps({'foreground': active}), flush=True)
            previous = active
        root.after(10, observe_focus)
    observe_focus()
    root.mainloop()


def start_cover(left, top):
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--cover',
        '--left=' + str(left), '--top=' + str(top)], cwd=ROOT,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
    lines = queue.Queue()
    process.foregrounds = []
    def read_lines():
        for line in process.stdout:
            value = json.loads(line)
            if 'foreground' in value:
                process.foregrounds.append(value['foreground'])
            else:
                lines.put(value)
    threading.Thread(target=read_lines, daemon=True).start()
    try:
        return process, lines.get(timeout=10)['hwnd']
    except Exception:
        process.terminate()
        process.wait(timeout=5)
        raise


def verify(browser, with_model):
    import psutil
    original = int(computer.USER.GetForegroundWindow() or 0)
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    screens = computer.monitors()
    work = next((row['work'] for row in screens if not row['primary']), screens[0]['work'])
    left, top = work[0] + 50, work[1] + 50
    with tempfile.TemporaryDirectory(prefix='covered-browser-', dir=cache) as directory:
        folder = Path(directory)
        profile = folder / 'profile'
        pages = []
        for label in ('Close Me', 'Keep Me', 'Close Also'):
            page = folder / (label.replace(' ', '-') + '.html')
            page.write_text(f'<title>Jarvis Background {label}</title><h1>{label}</h1>', encoding='utf-8')
            pages.append(page.as_uri())
        process, cover, target, foreground = None, None, None, None
        owners = []
        try:
            process = subprocess.Popen([browser, '--user-data-dir=' + str(profile), '--no-first-run',
                '--no-default-browser-check', '--disable-default-apps', '--window-size=850,650',
                f'--window-position={left + 25},{top + 25}', *pages], cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 25
            target = None
            while time.monotonic() < deadline:
                owners = [p for p in psutil.process_iter(['pid', 'cmdline'])
                          if '--user-data-dir=' + str(profile) in (p.info['cmdline'] or [])]
                pids = {p.pid for p in owners}
                target = next((row for row in computer.visible_windows() if row['pid'] in pids), None)
                if target:
                    snapshot = Accessibility().inspect(target)
                    if all(any(label in row['name'] for row in snapshot['controls'])
                           for label in ('Close Me', 'Keep Me', 'Close Also')):
                        break
                time.sleep(.3)
            else:
                raise AssertionError('The isolated browser did not expose the three fixture tabs.')
            computer.USER.SetWindowPos(target['hwnd'], None, left + 25, top + 25, 850, 650, 0x14)
            target = computer.window_info(target['hwnd'])
            cover, foreground = start_cover(left, top)
            assert computer.USER.GetForegroundWindow() != target['hwnd']
            cover_bounds = computer.window_info(foreground)['rect']
            bounds = target['rect']
            assert cover_bounds[0] <= bounds[0] and cover_bounds[1] <= bounds[1]
            assert cover_bounds[2] >= bounds[2] and cover_bounds[3] >= bounds[3], 'The browser is not fully covered'
            surface = Accessibility()
            snapshot = surface.inspect(target)
            assert snapshot['available'], snapshot
            capture = computer.capture_window(computer.window_info(target['hwnd']))
            assert capture['source'] == 'window', capture.get('source')
            assert computer.USER.GetForegroundWindow() != target['hwnd'], 'Inspection brought the browser forward'
            print('OBSERVED', json.dumps({'controls': len(snapshot['controls']), 'capture': capture['source'],
                'foregroundPreserved': True}), flush=True)

            def assert_result():
                current = computer.window_info(target['hwnd'])
                assert current is not None, 'The fixture browser closed before verification'
                fresh = Accessibility().inspect(current)
                names = [row['name'] for row in fresh['controls']]
                assert any('Keep Me' in name for name in names), 'The unrelated fixture tab disappeared'
                assert not any('Close Me' in name or 'Close Also' in name for name in names), 'A target tab remains'
                assert computer.USER.GetForegroundWindow() != target['hwnd'], 'Closing a tab brought the browser forward'
                assert target['hwnd'] not in cover.foregrounds, 'The browser briefly took foreground focus'

            if with_model:
                spec = importlib.util.spec_from_file_location('interaction_check', Path(__file__).with_name('verify-app-interaction.py'))
                helper = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(helper)
                real_windows = computer.visible_windows
                def listing():
                    return [row for row in real_windows() if row['hwnd'] in (target['hwnd'], foreground)]
                apps = Mock(apps=[{'Name': 'Browser test fixture', 'AppID': 'fixture'}])
                real_step = helper.ResearchJobs._computer_step
                def step(jobs, job, frame, steps, readonly):
                    if job['_desktop'].get('lastControlAction'):
                        print('RESULT', json.dumps(job['_desktop']['lastControlAction']), flush=True)
                    assert computer.USER.GetForegroundWindow() != target['hwnd'], 'An action brought the covered browser forward'
                    action = real_step(jobs, job, frame, steps, readonly)
                    chosen = next((row for row in job['_desktop'].get('accessibility', {}).get('controls', [])
                                   if row['id'] == action.get('target')), None)
                    if chosen:
                        print('CHOSEN', json.dumps(chosen), flush=True)
                    return action
                with helper.model_service() as request, patch.object(computer, 'visible_windows', side_effect=listing), \
                     patch.object(helper.ResearchJobs, '_computer_step', step), \
                     patch.object(computer, 'bring_to_front', side_effect=AssertionError('The worker tried to steal focus')), \
                     patch.object(computer, 'apply_action', side_effect=AssertionError('The worker tried foreground input')):
                    helper.run('In the browser test window, close the tabs Jarvis Background Close Me and '
                        'Jarvis Background Close Also. Keep Jarvis Background Keep Me. Do this in the background '
                        'while I use the covering window; do not bring the browser forward.', request, apps, verify=assert_result)
            else:
                for label in ('Close Me', 'Close Also'):
                    snapshot = surface.inspect(computer.window_info(target['hwnd']))
                    tabs = [row for row in snapshot['controls'] if row['role'] == 'TabItemControl' and label in row['name']
                            and 'click_control' in row['actions']]
                    assert len(tabs) == 1, (label, len(tabs))
                    surface.perform(computer.window_info(target['hwnd']), tabs[0]['id'], 'click_control', button='middle')
                    time.sleep(.3)
                    actual = computer.USER.GetForegroundWindow()
                    assert actual != target['hwnd'], 'A native action brought the browser forward: ' + label
                assert_result()
            print('PASS: two covered browser tabs closed; unrelated tab and foreground preserved.', flush=True)
        finally:
            restore = computer.USER.GetForegroundWindow() in (foreground if cover else None, target['hwnd'] if target else None)
            if cover:
                cover.terminate()
                cover.communicate(timeout=5)
            for owner in owners:
                try:
                    children = owner.children(recursive=True)
                    owner.terminate()
                    for child in children:
                        try:
                            child.terminate()
                        except psutil.NoSuchProcess:
                            pass
                    psutil.wait_procs([owner, *children], timeout=5)
                except psutil.NoSuchProcess:
                    pass
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            if restore and original and computer.USER.IsWindow(original):
                computer.USER.SetForegroundWindow(original)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cover', action='store_true')
    parser.add_argument('--browser')
    parser.add_argument('--model', action='store_true')
    parser.add_argument('--left', type=int, default=50)
    parser.add_argument('--top', type=int, default=50)
    args = parser.parse_args()
    computer.USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if args.cover:
        cover_window(args.left, args.top)
    elif args.browser:
        verify(args.browser, args.model)
    else:
        parser.error('--browser is required')
