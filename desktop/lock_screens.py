"""Whether a lock screen is covering the desktop, so Jarvis knows the owner has not really got back to it yet.

Alpenlock is the owner's own password screensaver. After a few idle minutes it puts a window titled "Alpenlock" over
every display, always on top, and destroys those windows once he types his password. Windows' own lock screen (and any
other secure desktop, such as a UAC prompt) takes the input away from the normal desktop. While either is up, moving
the mouse only means he is reaching for his password, so anything that greets him must wait until the cover is gone.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from desktop.computer import OWN_PROCESSES, USER, window_info

ALPENLOCK_TITLE = 'Alpenlock'
# A lock window covers its whole display; the Alpenlock tray panel and preview window do not.
COVERED_SHARE = .9
SETTLE_SECONDS = 1.5
POLL_SECONDS = .5
DESKTOP_READOBJECTS = 0x0001
UOI_NAME = 2

_user = ctypes.WinDLL('user32', use_last_error=True)
_user.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_user.OpenInputDesktop.restype = wt.HANDLE
_user.CloseDesktop.argtypes = [wt.HANDLE]
_user.CloseDesktop.restype = wt.BOOL
_user.GetUserObjectInformationW.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
_user.GetUserObjectInformationW.restype = wt.BOOL
_user.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
_user.IsWindowVisible.argtypes = [wt.HWND]
_user.IsIconic.argtypes = [wt.HWND]
_user.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
_user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user.GetWindowThreadProcessId.restype = wt.DWORD


def input_desktop():
    """The name of the desktop that has the keyboard and mouse, or None when Windows will not tell a normal app,
    which is what happens while the secure desktop (lock screen, UAC) has them."""
    handle = _user.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
    if not handle:
        return None
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = wt.DWORD()
        if _user.GetUserObjectInformationW(handle, UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed)):
            return name.value
        return None
    finally:
        _user.CloseDesktop(handle)


def titled(title):
    """Visible, non-minimized top-level windows of other apps with exactly this title."""
    rows = []
    buffer = ctypes.create_unicode_buffer(64)
    process = wt.DWORD()

    def visit(hwnd, _data):
        if not _user.IsWindowVisible(hwnd) or _user.IsIconic(hwnd):
            return True
        # Reading the title of one of Jarvis's own windows would wait on the thread that owns it; other apps' titles
        # are read without asking them anything.
        _user.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
        if process.value in OWN_PROCESSES:
            return True
        if _user.GetWindowTextW(hwnd, buffer, len(buffer)):
            if buffer.value.strip() == title:
                row = window_info(hwnd)
                if row:
                    rows.append(row)
        return True

    callback = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(visit)
    _user.EnumWindows(callback, 0)
    return rows


def overlap(a, b):
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0


def alpenlock_up(windows, screens):
    """True when an Alpenlock window covers nearly all of any monitor: that is its lock screen, not its tray panel."""
    for window in windows:
        if window.get('title', '').strip() != ALPENLOCK_TITLE or window.get('pid') in OWN_PROCESSES:
            continue
        for screen in screens:
            rect = screen['rect']
            if overlap(window['rect'], rect) >= COVERED_SHARE * (rect[2] - rect[0]) * (rect[3] - rect[1]):
                return True
    return False


def covered():
    """'windows' while Windows' lock screen or another secure desktop has the input, 'alpenlock' while Alpenlock's
    lock screen is up, otherwise None."""
    from desktop.windows import monitors
    previous = USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        name = input_desktop()
        if not name or name.casefold() != 'default':
            return 'windows'
        if alpenlock_up(titled(ALPENLOCK_TITLE), monitors()):
            return 'alpenlock'
        return None
    finally:
        if previous:
            USER.SetThreadDpiAwarenessContext(previous)


def wait_until_clear(stop=None, settle=SETTLE_SECONDS, poll=POLL_SECONDS, check=None, clock=time.monotonic,
                     sleep=None):
    """Block until no lock screen has covered the desktop for `settle` seconds. Returns False if `stop` (an Event) is
    set first. The short settle rides out Alpenlock rebuilding its windows when a display changes. A check that cannot
    run counts as clear, so a broken check can never hold a greeting back for good."""
    check = check or covered
    if sleep is None:
        sleep = stop.wait if stop is not None else time.sleep
    clear_since = None
    while stop is None or not stop.is_set():
        try:
            cover = check()
        except Exception:
            cover = None
        now = clock()
        if cover:
            clear_since = None
        elif clear_since is None:
            clear_since = now
        if clear_since is not None and now - clear_since >= settle:
            return True
        sleep(poll)
    return False
