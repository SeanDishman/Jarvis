import base64
import ctypes
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
from desktop import computer
from desktop.common import local_request
from desktop.displays import display_bounds, monitor_number, select_display
from desktop.intent import classify_request
from desktop.research import ResearchJobs
from desktop.screen import capture_screen
from desktop.windows import desktop_name, monitors
from PIL import Image, ImageDraw


def fixture(output):
    import tkinter as tk
    computer.USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    screens = monitors()
    primary = next(s for s in screens if s['primary'])
    secondary = select_display('2', screens)
    root = tk.Tk()
    root.withdraw()
    handles = []
    computer.USER.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    computer.USER.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    computer.USER.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    rows = [('Primary fixture', primary, '#00dd77', 0),
            ('Secondary fixture A', secondary, '#dd2277', 0),
            ('Secondary fixture B', secondary, '#2277dd', 1)]
    for title, display, color, offset in rows:
        win = tk.Toplevel(root)
        win.withdraw()
        win.title('Jarvis monitor test - ' + title)
        win.geometry('300x190')
        tk.Frame(win, bg=color, width=300, height=190).pack(fill='both', expand=True)
        win.update_idletasks()
        hwnd = computer.USER.GetAncestor(win.winfo_id(), 2)
        style = computer.USER.GetWindowLongPtrW(hwnd, -20)
        computer.USER.SetWindowLongPtrW(hwnd, -20, style | 0x08000000)
        win.deiconify()
        x, y = display['work'][:2]
        computer.USER.SetWindowPos(hwnd, -1, x + 80 + offset * 340, y + 120, 300, 220, 0x0010)
        handles.append({'hwnd': int(hwnd), 'display': display['device'], 'color': color,
                        'secondary': display == secondary})
    root.update()
    Path(output).write_text(json.dumps(handles), encoding='utf-8')
    stopped = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.readline(), stopped.set()), daemon=True).start()
    def close_when_done():
        if stopped.is_set():
            root.destroy()
        else:
            root.after(50, close_when_done)
    root.after(50, close_when_done)
    root.mainloop()


def image_pixel(frame, x, y):
    left, top, right, bottom = frame['rect']
    col = int((x - left) * frame['width'] / (right - left))
    row = int((y - top) * frame['height'] / (bottom - top))
    with Image.open(io.BytesIO(base64.b64decode(frame['image']))) as image:
        return image.convert('RGB').getpixel((col, row))


def native_checks(rows, screens):
    overview = capture_screen('all')
    assert overview['rect'] == display_bounds(screens), overview['rect']
    for item in rows:
        window = computer.window_info(item['hwnd'])
        target = select_display(item['display'], screens)
        frame = capture_screen(str(monitor_number(target, screens.index(target))))
        assert frame['rect'] == target['rect'], frame['rect']
        x = (window['rect'][0] + window['rect'][2]) // 2
        y = (window['rect'][1] + window['rect'][3]) // 2
        expected = tuple(bytes.fromhex(item['color'][1:]))
        for view in (overview, frame):
            actual = image_pixel(view, x, y)
            assert all(abs(a - b) < 12 for a, b in zip(actual, expected)), (actual, expected)
    print('PASS: both physical monitors captured, including negative origins; fixture colors verified', flush=True)
    secondary = next(item for item in rows if item['secondary'])
    before = int(computer.USER.GetForegroundWindow() or 0)
    computer.minimize_window(computer.window_info(secondary['hwnd']))
    assert computer.USER.IsIconic(secondary['hwnd'])
    assert int(computer.USER.GetForegroundWindow() or 0) == before, 'Background minimization moved keyboard focus'
    for item in rows:
        if item != secondary:
            assert not computer.USER.IsIconic(item['hwnd']), 'An unrelated fixture was minimized'
    assert computer.window_display(computer.window_info(secondary['hwnd']), screens)['device'] == secondary['display']
    computer.USER.ShowWindowAsync(secondary['hwnd'], 4)
    deadline = time.monotonic() + 3
    while computer.USER.IsIconic(secondary['hwnd']) and time.monotonic() < deadline:
        time.sleep(.05)
    assert not computer.USER.IsIconic(secondary['hwnd'])
    print('PASS: second-monitor fixture minimized and verified without moving focus; other windows unchanged', flush=True)


def synthetic_frame(rect, inventory, screens):
    width, height = computer.scaled_size(rect[2] - rect[0], rect[3] - rect[1])
    image = Image.new('RGB', (width, height), '#18212f')
    draw = ImageDraw.Draw(image)
    def box(bounds):
        return [(bounds[0]-rect[0])*width/(rect[2]-rect[0]), (bounds[1]-rect[1])*height/(rect[3]-rect[1]),
                (bounds[2]-rect[0])*width/(rect[2]-rect[0]), (bounds[3]-rect[1])*height/(rect[3]-rect[1])]
    for index, display in enumerate(screens):
        left, top, right, bottom = box(display['rect'])
        draw.rectangle((left, top, right-1, bottom-1), outline='#3edcff', width=2)
        draw.text((left+20, top+20), 'MONITOR ' + str(monitor_number(display, index)), fill='white', font_size=22)
    for window in inventory():
        if computer.USER.IsIconic(window['hwnd']):
            continue
        left, top, right, bottom = box(window['rect'])
        draw.rectangle((left, top, right, bottom), fill='#dee8f8', outline='#5588bb')
        draw.text((left+5, top+5), window['title'].replace('Jarvis monitor test - ', ''), fill='#101820')
    data = io.BytesIO()
    image.save(data, format='PNG')
    return {'image': base64.b64encode(data.getvalue()).decode('ascii'), 'width': width, 'height': height,
            'rect': rect, 'captured': time.monotonic()}


def model_checks(rows, screens, temp):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, JARVIS_MODEL_PORT=str(port))
    with (temp / 'model.log').open('w', encoding='utf-8') as output:
        server = subprocess.Popen([str(ROOT / '.runtime/node-v24.21.0-win-x64/node.exe'), str(ROOT / 'phone/luna-server.mjs')],
                                  cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
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
                    raise RuntimeError('Isolated model service did not start') from None
                time.sleep(.2)
        with patch('desktop.intent.local_request', side_effect=request):
            for index, (utterance, action, display) in enumerate([
                ('Look at my second monitor and tell me what is there', 'screen', '2'),
                ('What is visible on both of my screens?', 'screen', 'all'),
                ('Minimize everything on the second monitor', 'computer', None),
            ]):
                decision = classify_request(utterance, 'monitor-fixture-intent-' + str(index))
                assert decision['action'] == action and not decision['needs_clarification'], decision
                if display:
                    assert decision['display'] == display, decision
                print('PASS: real intent model selected ' + action + (('/' + display) if display else ''), flush=True)
        handles = {row['hwnd'] for row in rows}
        def inventory():
            return [window for hwnd in handles if (window := computer.window_info(hwnd))]
        apps = Mock(apps=[{'Name': 'Jarvis monitor verification fixture'}])
        apps.launch.side_effect = AssertionError('The fixture must not launch other applications')
        control = computer.ComputerControl(apps)
        jobs = ResearchJobs(queue.Queue(), Mock(), control)
        job = {'id': 'multimonitor-fixture', 'question': 'Minimize all windows on the second monitor. Leave the first monitor alone.',
               'scope': 'computer', 'created': time.time(), 'cancel': threading.Event(),
               'deadline': time.monotonic() + 240, '_foreground': 0, '_modelTier': decision['model_tier'],
               'model': request(18791, '/models')['conversationModel']}
        allowed = {f'window:{row["hwnd"]}' for row in rows if row['secondary']}
        def ask(job, frame, steps, readonly):
            action = jobs._computer_step(job, frame, steps, readonly)
            assert action['action'] in ('view_display', 'minimize_window', 'done'), action
            if action['action'] == 'minimize_window':
                assert action['target'] in allowed, action
            if action['action'] == 'done':
                assert all(bool(computer.USER.IsIconic(row['hwnd'])) == row['secondary'] for row in rows), 'Premature success'
            print('MODEL STEP:', action['action'], action['target'], flush=True)
            return action
        with patch('desktop.research.local_request', side_effect=request), \
             patch.object(computer, 'visible_windows', side_effect=inventory), \
             patch.object(computer, 'capture_rect', side_effect=lambda rect: synthetic_frame(rect, inventory, screens)), \
             patch.object(computer, 'apply_action', side_effect=AssertionError('No pointer or keyboard input is permitted')):
            control.run(job, jobs._update, jobs._check, ask)
        assert job['status'] == 'completed'
        print('PASS: real model minimized both second-monitor fixtures; first-monitor fixture remained visible', flush=True)
        secondary = [row for row in rows if row['secondary']]
        for row in secondary:
            computer.USER.ShowWindowAsync(row['hwnd'], 4)
        time.sleep(.3)
        blocked = secondary[0]['hwnd']
        partial = {'id': 'multimonitor-partial-fixture', 'question': job['question'], 'scope': 'computer',
                   'created': time.time(), 'cancel': threading.Event(), 'deadline': time.monotonic() + 240,
                   '_foreground': 0, '_modelTier': 'default', 'model': job['model']}
        native_minimize = computer.minimize_window
        def refuse_one(target, check=None):
            if target['hwnd'] == blocked:
                raise RuntimeError('Could not minimize ' + target['title'] + '. The test window refused the command.')
            return native_minimize(target, check)
        def partial_step(job, frame, steps, readonly):
            action = jobs._computer_step(job, frame, steps, readonly)
            assert action['action'] in ('view_display', 'minimize_window', 'done'), action
            if action['action'] == 'minimize_window':
                assert action['target'] in allowed, action
            if action['action'] == 'done':
                assert all(bool(computer.USER.IsIconic(row['hwnd'])) == (row['secondary'] and row['hwnd'] != blocked) for row in rows), 'Skipped another window after refusal'
            print('PARTIAL STEP:', action['action'], flush=True)
            return action
        with patch('desktop.research.local_request', side_effect=request), \
             patch.object(computer, 'visible_windows', side_effect=inventory), \
             patch.object(computer, 'capture_rect', side_effect=lambda rect: synthetic_frame(rect, inventory, screens)), \
             patch.object(computer, 'minimize_window', side_effect=refuse_one), \
             patch.object(computer, 'apply_action', side_effect=AssertionError('No pointer or keyboard input is permitted')):
            control.run(partial, jobs._update, jobs._check, partial_step)
        assert partial['status'] == 'failed', partial['status']
        assert sum(row['verified'] for row in partial['windowResults']) == 1
        print('PASS: a refused window did not stop other windows; partial result retained', flush=True)

        from desktop.controller import Controller
        from types import SimpleNamespace
        controller = Controller(queue.Queue())
        controller.breaks = SimpleNamespace(active=False)
        controller.preferences.read = lambda: {'offlineSpeech': 'automatic'}
        controller.research = jobs
        jobs.jobs[partial['id']] = partial
        controller.last_task_id = partial['id']
        try:
            for utterance in ('Can I get an update?', 'What happened with minimizing everything?'):
                controller.last_domain = 'web'
                controller.last_answer_text = 'Four, sir.'
                controller.last_command_time = time.monotonic() - 120
                with patch('desktop.intent.local_request', side_effect=request), \
                     patch.object(jobs, 'start', side_effect=AssertionError('An update must not repeat the task')), \
                     patch.object(controller, 'ask_model', side_effect=AssertionError('Read the actual task result')):
                    result = controller.submit(utterance, speak=False)
                assert result['ok'] and result['taskReference'] == partial['id'], result
                assert 'Minimized 1 window' in result['text'] and 'refused' in result['text'], result
                print('PASS: real model follow-up resolved partial failure after another topic and timeout', flush=True)
        finally:
            controller.browser.close()
            controller.tasks.close()
            jobs.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


def main():
    if '--fixture' in sys.argv:
        fixture(sys.argv[sys.argv.index('--fixture') + 1])
        return
    if desktop_name().lower() != 'default':
        raise RuntimeError('Run this check on the normal Windows desktop')
    computer.USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    screens = monitors()
    if len(screens) < 2:
        raise RuntimeError('Two connected monitors are required for native verification')
    print('Displays:', json.dumps([{'device': s['device'], 'rect': s['rect']} for s in screens]), flush=True)
    with tempfile.TemporaryDirectory(prefix='jarvis-monitor-check-') as folder:
        temp = Path(folder)
        output = temp / 'windows.json'
        child = subprocess.Popen([sys.executable, __file__, '--fixture', str(output)], cwd=ROOT,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=subprocess.CREATE_NO_WINDOW, text=True)
        try:
            deadline = time.monotonic() + 10
            while not output.exists():
                if child.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Native fixture windows did not open')
                time.sleep(.1)
            rows = json.loads(output.read_text(encoding='utf-8'))
            time.sleep(.4)
            native_checks(rows, screens)
            if '--model' in sys.argv:
                model_checks(rows, screens, temp)
        finally:
            try:
                _, error = child.communicate('\n', timeout=5)
                if child.returncode:
                    raise RuntimeError('Fixture process failed: ' + error[-1200:])
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == '__main__':
    main()
