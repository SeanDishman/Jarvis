"""Check lock-screen detection against real windows without locking anything.

Puts up an invisible, click-through window titled "Alpenlock" over one monitor, the same title and size as Alpenlock's
lock screen, and checks that Jarvis sees the desktop as covered until it goes away, then waits out the settle time.
A small "Alpenlock" window, like its tray panel, must not count.
"""
import ctypes
import ctypes.wintypes as wt
import json
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from desktop.lock_screens import covered, input_desktop, wait_until_clear
from desktop.windows import monitors

HELPER = r"""
import ctypes, ctypes.wintypes as wt, sys, threading
user = ctypes.WinDLL('user32', use_last_error=True)
kernel = ctypes.WinDLL('kernel32')
user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user.DefWindowProcW.restype = ctypes.c_ssize_t
user.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user.CreateWindowExW.restype = wt.HWND
user.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.COLORREF, ctypes.c_ubyte, wt.DWORD]
user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel.GetModuleHandleW.restype = wt.HMODULE
def proc(hwnd, message, wparam, lparam):
    if message == 0x0002:
        user.PostQuitMessage(0)
    return user.DefWindowProcW(hwnd, message, wparam, lparam)
PROC = WNDPROC(proc)
class WindowClass(ctypes.Structure):
    _fields_ = [('style', wt.UINT), ('proc', WNDPROC), ('class_extra', ctypes.c_int), ('window_extra', ctypes.c_int),
                ('instance', wt.HINSTANCE), ('icon', wt.HICON), ('cursor', wt.HANDLE), ('background', wt.HBRUSH),
                ('menu', wt.LPCWSTR), ('name', wt.LPCWSTR)]
user.RegisterClassW(ctypes.byref(WindowClass(0, PROC, 0, 0, kernel.GetModuleHandleW(None), None, None, None, None, 'JarvisLockScreenCheck')))
title, left, top, right, bottom = sys.argv[1], *map(int, sys.argv[2:6])
# Layered at zero opacity, click-through, never activated: nobody can see or click it.
hwnd = user.CreateWindowExW(0x00080000 | 0x00000020 | 0x00000080 | 0x08000000 | 0x00000008, 'JarvisLockScreenCheck', title,
                            0x80000000, left, top, right - left, bottom - top, None, None, kernel.GetModuleHandleW(None), None)
user.SetLayeredWindowAttributes(hwnd, 0, 0, 2)
user.ShowWindow(hwnd, 4)
threading.Thread(target=lambda: (sys.stdin.readline(), user.PostMessageW(hwnd, 0x0010, 0, 0)), daemon=True).start()
print('ready', flush=True)
message = wt.MSG()
while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
    user.TranslateMessage(ctypes.byref(message))
    user.DispatchMessageW(ctypes.byref(message))
"""


class Ghost:
    """A stand-in window from another process, the way Alpenlock's own windows appear to Jarvis."""

    def __init__(self, title, rect):
        import subprocess
        self.process = subprocess.Popen([sys.executable, '-I', '-c', HELPER, title, *map(str, rect)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert self.process.stdout.readline().strip() == 'ready', 'The stand-in window did not open'

    def close(self):
        self.process.stdin.write('close' + chr(10))
        self.process.stdin.flush()
        self.process.wait(5)


def verify():
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    report = {'input_desktop': input_desktop()}
    started = time.perf_counter()
    report['now'] = covered()
    report['check_ms'] = round((time.perf_counter() - started) * 1000, 1)
    assert report['now'] is None, f'The desktop should be clear right now, got {report["now"]}'

    screen = next((row for row in monitors() if not row['primary']), monitors()[0])
    left, top, right, bottom = screen['rect']
    panel = Ghost('Alpenlock', [right - 400, bottom - 460, right - 20, bottom - 60])
    try:
        assert covered() is None, 'A small Alpenlock window (its tray panel) must not count as the lock screen'
    finally:
        panel.close()
    report['tray_panel_ignored'] = True

    lock = Ghost('Alpenlock', screen['rect'])
    try:
        assert covered() == 'alpenlock', 'A full-screen Alpenlock window was not seen'
        report['lock_window_seen_on'] = screen['device']
        waited = {}
        stop = threading.Event()

        def wait():
            begun = time.perf_counter()
            waited['clear'] = wait_until_clear(stop, poll=.1)
            waited['seconds'] = time.perf_counter() - begun

        waiter = threading.Thread(target=wait, daemon=True)
        waiter.start()
        time.sleep(1.0)
        assert waiter.is_alive(), 'The wait ended while Alpenlock was still up'
    finally:
        lock.close()
        unlocked = time.perf_counter()
    waiter.join(5)
    assert waited.get('clear') is True, waited
    after = time.perf_counter() - unlocked
    assert 1.4 <= after <= 2.2, f'The wait should end about 1.5s after the unlock, took {after:.2f}s'
    report['greeting_released_seconds_after_unlock'] = round(after, 2)
    assert covered() is None
    report['clear_again'] = True
    report['welcome'] = verify_welcome(screen)
    print(json.dumps(report, indent=1), flush=True)


def verify_welcome(screen):
    """The real welcome-back delivery, with Alpenlock up: nothing while it is up, the greeting right after."""
    import queue
    import tempfile
    from types import SimpleNamespace
    from desktop.welcome import PresenceLog, WelcomeBack

    controller = SimpleNamespace(closed=threading.Event(), breaks=SimpleNamespace(active=False), events=queue.Queue(),
                                 muted=True, voice_ready=True)
    with tempfile.TemporaryDirectory() as folder:
        guard = WelcomeBack(controller, writer=lambda hours, part, weekday: f'Good {part}, sir.',
                            presence=PresenceLog(pathlib.Path(folder) / 'presence.json'), work_area=lambda: [0, 0, 1920, 1032])
        lock = Ghost('Alpenlock', screen['rect'])
        try:
            worker = guard.greet(7 * 3600, 'return')
            time.sleep(2.5)
            assert controller.events.empty(), 'Jarvis greeted while Alpenlock was still up'
        finally:
            lock.close()
            unlocked = time.perf_counter()
        event = controller.events.get(timeout=5)
        after = time.perf_counter() - unlocked
        assert event[0] == 'welcome', event
        assert 1.3 <= after <= 2.5, f'The greeting came {after:.2f}s after the unlock'
        controller.closed.set()
        worker.join(10)
    return {'held_while_locked_seconds': 2.5, 'greeted_seconds_after_unlock': round(after, 2), 'card_text': event[1]['text']}


if __name__ == '__main__':
    verify()
