"""Jarvis's own pointer on real apps, through the real PC-control loop.

The owner keeps working in a window that covers the apps. A scripted planner, standing in for the model, drives
ComputerControl.run:
  * a classic Win32 app: two button clicks and a field click at screenshot points, then typing into that field;
  * an isolated Chrome profile, fully covered: the page is reported paused, a page button and field are used through
    the control tree (done with Jarvis's own pointer and typing), and point clicks on the stale page are refused;
  * the same Chrome window visible but not focused: a click, typing, Enter and a wheel scroll at screenshot points.
No action may inject real input (checked with low-level input hooks, so the owner may keep using the PC) or bring a
target app to the front. Jarvis's pointer overlay must reach every spot he acts on and hide when each task ends.

    python tests/verification/verify-agent-pointer.py [--browser PATH] [--model]

With --model the real model (through an isolated model service) handles a covered-browser request instead.
"""
import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes as wt
from pathlib import Path
from unittest.mock import Mock

from ui_harness import ROOT, Interface, wait
from desktop import computer
from desktop.accessibility import Accessibility
from desktop.computer import KERNEL, USER

BROWSERS = [r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']
PAGE = '''<!doctype html><title>C0 E0 S0 ||</title>
<body style="margin:0;font:20px Segoe UI;height:5000px;background:linear-gradient(#fff,#9bd)">
<input id=f aria-label="Check field" style="position:fixed;left:40px;top:40px;width:400px;height:40px;font-size:20px">
<button id=b style="position:fixed;left:40px;top:120px;width:220px;height:60px;font-size:20px">Press me</button>
<script>
let clicks = 0, entered = 0;
const show = () => document.title = `C${clicks} E${entered} S${Math.round(scrollY)} |${f.value}|`;
b.onclick = () => { clicks++; b.textContent = 'Pressed ' + clicks; show(); };
f.oninput = show;
f.onkeydown = event => { if (event.key === 'Enter') { entered++; show(); } };
addEventListener('scroll', show);
</script>'''


# ----------------------------------------------------------------------------------------------- fixture windows
def native_app(state):
    """A classic Win32 window with a standard EDIT and BUTTON. Writes its state ten times a second."""
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class WindowClass(ctypes.Structure):
        _fields_ = [('style', wt.UINT), ('proc', WNDPROC), ('classExtra', ctypes.c_int), ('windowExtra', ctypes.c_int),
                    ('instance', wt.HINSTANCE), ('icon', wt.HICON), ('cursor', wt.HANDLE), ('background', wt.HBRUSH),
                    ('menu', wt.LPCWSTR), ('name', wt.LPCWSTR)]

    USER.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    USER.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    USER.DefWindowProcW.restype = LRESULT
    USER.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
    USER.CreateWindowExW.restype = wt.HWND
    KERNEL.GetModuleHandleW.restype = wt.HMODULE
    controls, clicks = {}, [0]

    def rect(hwnd):
        box = computer.Rect()
        USER.GetWindowRect(hwnd, ctypes.byref(box))
        return [box.left, box.top, box.right, box.bottom]

    def procedure(hwnd, message, wparam, lparam):
        if message == 0x0111 and wparam & 0xffff == 1 and wparam >> 16 == 0:  # BN_CLICKED from the button
            clicks[0] += 1
            return 0
        if message == 0x0113:  # WM_TIMER
            text = ctypes.create_unicode_buffer(512)
            USER.GetWindowTextW(controls['edit'], text, 512)
            Path(state + '.tmp').write_text(json.dumps({'hwnd': int(hwnd), 'clicks': clicks[0], 'text': text.value,
                                                        'button': rect(controls['button']), 'field': rect(controls['edit'])}))
            os.replace(state + '.tmp', state)
            return 0
        if message == 0x0002:
            USER.PostQuitMessage(0)
            return 0
        return USER.DefWindowProcW(hwnd, message, wparam, lparam)

    callback = WNDPROC(procedure)
    kind = WindowClass(0, callback, 0, 0, KERNEL.GetModuleHandleW(None), None, None, wt.HBRUSH(6), None, 'JarvisPointerCheck')
    if not USER.RegisterClassW(ctypes.byref(kind)):
        raise ctypes.WinError()
    main = USER.CreateWindowExW(0, kind.name, 'Jarvis pointer check', 0x10CF0000, 320, 320, 460, 220, None, None, kind.instance, None)
    controls['edit'] = USER.CreateWindowExW(0x200, 'EDIT', '', 0x50010080, 20, 20, 400, 32, main, wt.HMENU(2), kind.instance, None)
    controls['button'] = USER.CreateWindowExW(0, 'BUTTON', 'Press me', 0x50010000, 20, 80, 160, 40, main, wt.HMENU(1), kind.instance, None)
    USER.SetTimer(main, 1, 100, None)
    message = wt.MSG()
    while USER.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        USER.TranslateMessage(ctypes.byref(message))
        USER.DispatchMessageW(ctypes.byref(message))


def cover_window():
    """The owner's window, on top of the apps Jarvis works in."""
    import tkinter as tk
    root = tk.Tk()
    root.title('Owner window covering the apps')
    root.geometry('820x640+250+250')
    root.configure(bg='#17365d')
    root.attributes('-topmost', True)
    tk.Label(root, text='The owner keeps working here', bg='#17365d', fg='white', font=('Segoe UI', 16)).pack(expand=True)
    root.update()
    print(json.dumps({'hwnd': int(USER.GetAncestor(root.winfo_id(), 2))}), flush=True)
    root.after(200, root.focus_force)
    root.mainloop()


def log(*parts):
    print(time.strftime('%H:%M:%S'), *parts, file=sys.stderr, flush=True)


# ------------------------------------------------------------------------------------------------------ watchers
class Watch:
    """Foreground changes, plus mouse and keyboard events injected with SendInput (real hardware input is not
    counted, so the owner may keep using the PC during the check)."""

    class MouseHook(ctypes.Structure):
        _fields_ = [('x', wt.LONG), ('y', wt.LONG), ('data', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD),
                    ('extra', ctypes.c_size_t)]

    class KeyHook(ctypes.Structure):
        _fields_ = [('vk', wt.DWORD), ('scan', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', ctypes.c_size_t)]

    def __init__(self):
        self.injected, self.error = {'mouse': 0, 'keyboard': 0}, None
        # The owner's own clicks and key presses, and when the foreground changed, so a window the owner clicked
        # is never blamed on Jarvis.
        self.presses, self.changes, self.names = [], [], {}
        self.foregrounds, self.running, self.ready = [], True, threading.Event()
        self.threads = [threading.Thread(target=self.hooks, daemon=True), threading.Thread(target=self.poll, daemon=True)]
        for thread in self.threads:
            thread.start()
        assert self.ready.wait(5), 'Input hooks did not start'
        assert not self.error, self.error
        # Prove the hooks see injected input with a one-pixel nudge and back (Windows drops zero-distance moves).
        computer.send_inputs(computer.mouse(0x0001, 1, 0), computer.mouse(0x0001, -1, 0))
        wait(lambda: self.injected['mouse'] >= 2, 2, 'The input hooks do not see injected input')
        self.injected['mouse'] = 0

    def hooks(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
        user.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
        user.SetWindowsHookExW.restype = wt.HHOOK
        user.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [wt.HHOOK]

        def mouse(code, wparam, lparam):
            if code >= 0:
                if ctypes.cast(lparam, ctypes.POINTER(self.MouseHook)).contents.flags & 0x01:
                    self.injected['mouse'] += 1
                elif wparam in (0x0201, 0x0204, 0x0207, 0x020B):
                    self.presses.append(time.monotonic())
            return user.CallNextHookEx(None, code, wparam, lparam)

        def keyboard(code, wparam, lparam):
            if code >= 0:
                if ctypes.cast(lparam, ctypes.POINTER(self.KeyHook)).contents.flags & 0x10:
                    self.injected['keyboard'] += 1
                elif wparam in (0x0100, 0x0104):
                    self.presses.append(time.monotonic())
            return user.CallNextHookEx(None, code, wparam, lparam)

        self.procedures = (HOOKPROC(mouse), HOOKPROC(keyboard))
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetModuleHandleW.restype = wt.HMODULE
        module = kernel.GetModuleHandleW(None)
        hooks = [user.SetWindowsHookExW(14, self.procedures[0], module, 0), user.SetWindowsHookExW(13, self.procedures[1], module, 0)]
        if not all(hooks):
            self.error = f'Could not install the input hooks: Windows error {ctypes.get_last_error()}'
            self.ready.set()
            return
        self.thread_id = KERNEL.GetCurrentThreadId()
        self.ready.set()
        message = wt.MSG()
        while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            pass
        for hook in hooks:
            user.UnhookWindowsHookEx(hook)

    def poll(self):
        last = None
        while self.running:
            current = int(USER.GetForegroundWindow() or 0)
            if current != last:
                self.foregrounds.append(current)
                self.changes.append((time.monotonic(), current))
                if current not in self.names:
                    info = computer.window_info(current)
                    self.names[current] = f"{info['process']}: {info['title'][:40]}" if info else 'none'
                last = current
            time.sleep(.005)

    def stop(self):
        self.running = False
        USER.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        for thread in self.threads:
            thread.join(3)
        return {'injected': dict(self.injected), 'foregrounds': self.foregrounds, 'presses': self.presses, 'changes': self.changes,
                'names': self.names, 'stopped': time.monotonic()}

    @staticmethod
    def held(watched, hwnd):
        """How long, in seconds, each spell of the window in front lasted during a watch."""
        changes = watched['changes'] + [(watched['stopped'], None)]
        return [round(after[0] - moment, 3) for (moment, current), after in zip(changes, changes[1:]) if current == hwnd]

    @staticmethod
    def taken_by(watched, hwnd, grace=1.0):
        """Times the window came to the front during a watch with no click or key press from the owner just before."""
        return [moment for moment, current in watched['changes'][1:]
                if current == hwnd and not any(0 <= moment - press <= grace for press in watched['presses'])]


def overlay_pointer(ui, moves, problems):
    """Forwards Jarvis's pointer to the interface and checks that the overlay tip reaches each spot."""
    def tip():
        row = ui.windows().get('pointer')
        if not row or not row['visible']:
            return None
        box = computer.Rect()
        USER.GetWindowRect(row['handle'], ctypes.byref(box))
        scale = USER.GetDpiForWindow(row['handle']) / 96
        return box.left + round(18 * scale), box.top + round(14 * scale)

    def pointer(**row):
        # ComputerControl ignores pointer failures so a task never depends on the overlay; record them here instead.
        log('pointer', row)
        try:
            ui.emit('agent_pointer', row)
            if row.get('hide'):
                hidden = ui.wait(lambda: not (ui.windows().get('pointer') or {}).get('visible') or None, 3, 'hide')
                moves.append({'hide': bool(hidden)})
                return
            try:
                at = ui.wait(lambda: (lambda spot: spot if spot and abs(spot[0] - row['x']) <= 2 and abs(spot[1] - row['y']) <= 2 else None)(tip()),
                             3, 'reach')
                moves.append({'action': row['action'], 'spot': (row['x'], row['y']), 'tip': at})
            except AssertionError:
                problems.append(f'Jarvis pointer did not reach {row}: tip at {tip()}')
        except Exception as error:
            problems.append(f'Pointer check failed for {row}: {type(error).__name__}: {error}'[:400])
    return pointer


# ------------------------------------------------------------------------------------------------- task driver
def run_task(engine, question, plan, cover, watches):
    """Runs ComputerControl.run with a scripted planner while the owner's window has the foreground. Each plan step
    returns an action, or None to wait. Input and focus are watched only while Jarvis works."""
    computer.bring_to_front(cover)
    time.sleep(.3)
    job = {'id': 'pointer-check-' + str(time.time_ns()), 'question': question, 'created': time.time(),
           '_foreground': int(USER.GetForegroundWindow() or 0)}
    steps, results, scenes = iter(plan), [], []
    pending = [next(steps)]

    def ask(job, frame, _steps, _readonly):
        scene = job['_desktop']
        scenes.append(scene)
        if scene.get('lastAction') and scene['lastAction']['action'] != 'wait':
            results.append(scene['lastAction'])
        action = pending[0](scene, frame)
        log('step', json.dumps(action)[:160], '| last result:', json.dumps((scene.get('lastAction') or {}).get('result'))[:200])
        if action is None:
            return {'action': 'wait', 'message': 'Waiting for the app'}
        pending[0] = next(steps, lambda *_: {'action': 'done', 'outcome': 'completed', 'message': 'Checked'})
        return {'message': action.get('action', ''), **action}

    log('task:', question)
    watch = Watch()
    try:
        engine.run(job, lambda job, **fields: job.update(fields), lambda job: None, ask)
    finally:
        watches.append(watch.stop())
        log('task ended:', job.get('status'), job.get('error', ''))
    assert job.get('status') == 'completed', job
    return results, scenes


def control_id(scene, name):
    return next((row['id'] for row in (scene.get('accessibility') or {}).get('controls', []) if row['name'].startswith(name)), None)


def control_value(scene, name):
    """A field's value, or the text Jarvis typed there that a paused page has not reported yet."""
    row = next((row for row in (scene.get('accessibility') or {}).get('controls', []) if row['name'].startswith(name)), {})
    return row.get('typedByJarvis', row.get('value'))


def control_rect(scene, name):
    return next((row['rect'] for row in (scene.get('accessibility') or {}).get('controls', []) if row['name'].startswith(name)), None)


def shot(frame, x, y):
    left, top, right, bottom = frame['rect']
    return {'x': round((x - left) * frame['width'] / (right - left)), 'y': round((y - top) * frame['height'] / (bottom - top))}


def centre(rect):
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2


# ------------------------------------------------------------------------------------------------------- checks
def check_native(engine, folder, cover, report, watches):
    state = folder / 'native.json'
    app = subprocess.Popen([sys.executable, __file__, '--native-app', str(state)], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        read = lambda: json.loads(state.read_text()) if state.exists() else None
        data = wait(read, 10, 'The Win32 test app did not start')
        window = f'window:{data["hwnd"]}'
        assert computer.fully_covered(computer.window_info(data['hwnd'])), 'The owner window does not cover the Win32 app'
        button, field = centre(data['button']), (data['field'][0] + 30, centre(data['field'])[1])
        plan = [lambda scene, frame: {'action': 'inspect_window', 'target': window},
                lambda scene, frame: {'action': 'click', **shot(frame, *button)},
                lambda scene, frame: {'action': 'click', **shot(frame, *button)},
                lambda scene, frame: {'action': 'click', **shot(frame, *field)},
                lambda scene, frame: {'action': 'type', 'text': 'Jarvis typed this'}]
        results, scenes = run_task(engine, 'Press the button twice and type in the field', plan, cover, watches)
        final = wait(lambda: (lambda d: d if d and d['clicks'] == 2 and d['text'] else None)(read()), 5, f'Win32 app state: {read()}')
        report['win32'] = {'clicks': final['clicks'], 'text': final['text'], 'source': scenes[-1]['view']['source'],
                           'results': [row['result']['status'] for row in results]}
        assert final['text'] == 'Jarvis typed this', final
        assert all(row['result']['status'] == 'sent_in_background' for row in results[1:]), results
        return data['hwnd']
    finally:
        app.terminate()


def check_browser(engine, browser, folder, cover, report, watches):
    import psutil
    page = folder / 'page.html'
    page.write_text(PAGE, encoding='utf-8')
    profile = folder / 'profile'
    def step_aside(x):
        USER.SetWindowPos(cover, None, x, 250, 0, 0, 0x0001 | 0x0004 | 0x0010)
        time.sleep(.8)
    # Chromium builds a page's accessibility tree only once the page has been on screen, as the owner's browser
    # normally has been. The owner's window steps aside while the page first renders, then covers it again.
    step_aside(1100)
    process = subprocess.Popen([browser, '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check',
                                '--disable-default-apps', '--window-size=700,520', '--window-position=320,320', page.as_uri()],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owners = []
    try:
        def find():
            nonlocal owners
            owners = [p for p in psutil.process_iter(['pid', 'cmdline']) if '--user-data-dir=' + str(profile) in (p.info['cmdline'] or [])]
            return next((row for row in computer.visible_windows() if row['pid'] in {p.pid for p in owners} and row['title'].startswith('C0 ')), None)
        target = wait(find, 25, 'The isolated browser did not open the test page')
        # Chromium exposes page content to UI Automation once a client asks for it.
        wait(lambda: any(row['name'] == 'Check field' for row in Accessibility().inspect(computer.window_info(target['hwnd']))['controls']),
             15, 'The browser did not expose the page controls', interval=.5)
        step_aside(250)
        title = lambda: computer.window_info(target['hwnd'])['title']
        window = f'window:{target["hwnd"]}'
        assert computer.fully_covered(computer.window_info(target['hwnd'])), 'The owner window does not cover the browser'

        # Fully covered: the page is paused, so it is used through the control tree.
        plan = [lambda scene, frame: {'action': 'inspect_window', 'target': window},
                lambda scene, frame: control_id(scene, 'Press me') and {'action': 'invoke', 'target': control_id(scene, 'Press me')},
                lambda scene, frame: control_id(scene, 'Check field') and {'action': 'set_value', 'target': control_id(scene, 'Check field'), 'text': 'first draft'},
                # Replacing text that is already there: the deletions must not be overtaken by the new text, and a
                # paused page that still reports the old value must not stop the typed text from being replaced.
                lambda scene, frame: control_value(scene, 'Check field') == 'first draft' and {
                    'action': 'set_value', 'target': control_id(scene, 'Check field'), 'text': 'hello from Jarvis'} or None,
                lambda scene, frame: {'action': 'click', **shot(frame, *centre(control_rect(scene, 'Press')))},
                lambda scene, frame: {'action': 'scroll', **shot(frame, *centre(control_rect(scene, 'Press'))), 'scroll': -3}]
        results, scenes = run_task(engine, 'Press the page button and fill the field', plan, cover, watches)
        report['browser_covered'] = {'pagePaused': scenes[-1]['view']['pagePaused'], 'title': title(),
                                     'results': [(row['action'], row['result'].get('status'), row['result'].get('error', '')[:70]) for row in results]}
        assert scenes[-1]['view']['pagePaused'], 'A fully covered browser page was not reported as paused'
        wait(lambda: title().startswith('C1 E0 S0 |hello from Jarvis|'), 8, f'Covered page title: {title()}')
        assert [row['result']['status'] for row in results[1:4]] == ['sent_in_background'] * 3, results
        assert all(row['result']['status'] == 'input_not_sent' and 'paused painting' in row['result']['error'] for row in results[4:]), results

        # Visible but not focused: point actions on the live screenshot, typing, Enter and the wheel.
        step_aside(1100)
        plan = [lambda scene, frame: {'action': 'inspect_window', 'target': window},
                lambda scene, frame: {'action': 'click', **shot(frame, *centre(control_rect(scene, 'Press')))},
                lambda scene, frame: {'action': 'click', **shot(frame, control_rect(scene, 'Check field')[2] - 15, centre(control_rect(scene, 'Check field'))[1])},
                lambda scene, frame: {'action': 'type', 'text': ' and more'},
                lambda scene, frame: {'action': 'key', 'key': 'enter'},
                lambda scene, frame: {'action': 'scroll', **shot(frame, *centre(control_rect(scene, 'Press'))), 'scroll': -3}]
        results, scenes = run_task(engine, 'Click, type, press enter and scroll in the page', plan, cover, watches)
        final = wait(lambda: (lambda t: t if t.startswith('C2 E1 ') and not t.startswith('C2 E1 S0 ') else None)(title()), 5, f'Browser title: {title()}')
        report['browser_visible'] = {'pagePaused': scenes[-1]['view']['pagePaused'], 'title': final,
                                     'results': [(row['action'], row['result'].get('status')) for row in results]}
        assert not scenes[-1]['view']['pagePaused']
        assert '|hello from Jarvis and more|' in final, final
        assert all(row['result']['status'] == 'sent_in_background' for row in results[1:]), results
        return target['hwnd']
    finally:
        for owner in owners:
            try:
                owner.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        psutil.wait_procs(owners, timeout=5)


def check_browser_with_model(browser, folder, cover, report, watches, pointer):
    """The real model gets a covered-browser request and must finish it in the background."""
    import importlib.util
    import psutil
    from unittest.mock import patch
    spec = importlib.util.spec_from_file_location('interaction_check', Path(__file__).with_name('verify-app-interaction.py'))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    page = folder / 'page.html'
    page.write_text(PAGE, encoding='utf-8')
    profile = folder / 'profile'
    USER.SetWindowPos(cover, None, 1100, 250, 0, 0, 0x0001 | 0x0004 | 0x0010)
    process = subprocess.Popen([browser, '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check',
                                '--disable-default-apps', '--window-size=700,520', '--window-position=320,320', page.as_uri()],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owners = []
    try:
        def find():
            nonlocal owners
            owners = [p for p in psutil.process_iter(['pid', 'cmdline']) if '--user-data-dir=' + str(profile) in (p.info['cmdline'] or [])]
            return next((row for row in computer.visible_windows() if row['pid'] in {p.pid for p in owners} and row['title'].startswith('C0 ')), None)
        target = wait(find, 25, 'The isolated browser did not open the test page')
        wait(lambda: any(row['name'] == 'Check field' for row in Accessibility().inspect(computer.window_info(target['hwnd']))['controls']),
             15, 'The browser did not expose the page controls', interval=.5)
        USER.SetWindowPos(cover, None, 250, 250, 0, 0, 0x0001 | 0x0004 | 0x0010)
        time.sleep(.8)
        assert computer.fully_covered(computer.window_info(target['hwnd'])), 'The owner window does not cover the browser'
        title = lambda: computer.window_info(target['hwnd'])['title']
        real_windows = computer.visible_windows
        def verify_page():
            assert title().startswith('C1 ') and '|hello from Jarvis|' in title(), title()
        computer.bring_to_front(cover)
        time.sleep(.3)
        apps = Mock(apps=[{'Name': 'Browser test fixture', 'AppID': 'fixture'}])
        question = ('In the browser test window, press the Press me button on the page once and enter hello from Jarvis '
                    'in the Check field. Do it in the background: I am working in another window, so do not bring the '
                    'browser forward.')
        with helper.model_service() as request,              patch.object(computer, 'visible_windows', side_effect=lambda: [row for row in real_windows() if row['hwnd'] in (target['hwnd'], cover)]),              patch.object(computer.ComputerControl, 'pointer', staticmethod(pointer)),              patch.object(computer, 'bring_to_front', side_effect=AssertionError('The model brought the browser forward')),              patch.object(computer, 'apply_action', side_effect=AssertionError('The model used foreground input')):
            watch = Watch()
            try:
                actions = helper.run(question, request, apps, verify=verify_page)
            finally:
                watches.append(watch.stop())
        verify_page()
        report['browser_model'] = {'actions': actions, 'title': title()}
        return target['hwnd']
    finally:
        for owner in owners:
            try:
                owner.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        psutil.wait_procs(owners, timeout=5)


def verify(browser, model=False):
    folder = Path(tempfile.mkdtemp(prefix='agent-pointer-', dir=ROOT / '.cache'))
    cover_process = subprocess.Popen([sys.executable, __file__, '--cover'], stdout=subprocess.PIPE, text=True,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
    report, moves, problems, targets, watches = {}, [], [], [], []
    try:
        cover = json.loads(cover_process.stdout.readline())['hwnd']
        time.sleep(.8)
        with Interface() as ui:
            engine = computer.ComputerControl(Mock(apps=[{'Name': 'Pointer check', 'AppID': 'fixture'}]))
            engine.pointer = overlay_pointer(ui, moves, problems)
            if model:
                assert browser, 'The model check needs Chrome or Edge (pass --browser)'
                targets.append(check_browser_with_model(browser, folder, cover, report, watches, engine.pointer))
            else:
                targets.append(check_native(engine, folder, cover, report, watches))
                if browser:
                    targets.append(check_browser(engine, browser, folder, cover, report, watches))
                else:
                    report['browser'] = 'skipped: no Chrome or Edge found (pass --browser)'
        report['injected_input'] = {kind: sum(row['injected'][kind] for row in watches) for kind in ('mouse', 'keyboard')}
        report['targets_took_focus'] = [hex(hwnd) for row in watches for hwnd in row['foregrounds'] if hwnd in targets]
        report['pointer'] = {'spots_reached': sum('tip' in row for row in moves), 'hidden_after_tasks': sum(row.get('hide', False) for row in moves),
                             'problems': problems}
        print(json.dumps(report, indent=1))
        assert report['injected_input'] == {'mouse': 0, 'keyboard': 0}, 'Jarvis injected real input'
        assert not report['targets_took_focus'], 'A target app took the foreground'
        assert not problems, problems
        assert report['pointer']['hidden_after_tasks'] == (1 if model or not browser else 3), moves
        print('PASS: background clicks, typing, keys and scrolling reached the apps; the owner kept focus and pointer; '
              "Jarvis's pointer reached every spot and hid after each task.")
    finally:
        cover_process.terminate()
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--native-app')
    parser.add_argument('--cover', action='store_true')
    parser.add_argument('--browser', default=next((path for path in BROWSERS if Path(path).exists()), None))
    parser.add_argument('--model', action='store_true', help='let the real model drive a covered-browser request')
    args = parser.parse_args()
    USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    if args.native_app:
        native_app(args.native_app)
    elif args.cover:
        cover_window()
    else:
        verify(args.browser, args.model)
