"""Placing windows: where a window is or was, and moving one there with Jarvis's own pointer.

Every step leaves keyboard focus with the owner. Measured on classic Win32 and Chromium windows, with another app
in front:
  * SetWindowPos with SWP_NOACTIVATE moves and resizes a window without activating it;
  * the maximize system command and SW_SHOWNOACTIVATE change a visible window's state without activating it;
  * a minimized window comes back without activating only when it is first sent to the bottom of the window stack
    and restored through its placement. SW_SHOWNOACTIVATE or SC_RESTORE alone activate one that was maximized;
  * topmost and straight back lifts a window above the others without activating it. A plain move to the top is
    ignored for a window that is not the foreground one.
Restoring a window that was minimized, maximized ones especially, still activates it now and then: the app activates
itself while it comes back. So the window that had keyboard focus is noted first, and if the moved window takes it
without the owner clicking or typing in between, focus goes straight back.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import time

from desktop.computer import USER, Point, check_pointer_stop, manageable, require_desktop, window_display, window_info
from desktop.windows import MonitorInfo, Rect

DWM = ctypes.WinDLL('dwmapi', use_last_error=True)
DWM.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
DWM.DwmGetWindowAttribute.restype = ctypes.c_long
DWMWA_CAPTION_BUTTON_BOUNDS = 5
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_ASYNC = 0x0001, 0x0002, 0x0004, 0x0010, 0x4000
HWND_BOTTOM, HWND_TOPMOST, HWND_NOTOPMOST = 1, -1, -2
SW_SHOWNOACTIVATE = 4
WM_SYSCOMMAND, SC_MAXIMIZE = 0x0112, 0xF030
WPF_RESTORETOMAXIMIZED = 0x0002
GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x0008
# The drag takes as long as the pointer overlay's glide, so the window stays under the pointer.
DRAG_SECONDS, DRAG_FRAMES = .38, 16
MAXIMIZED_INSET = .1
FOCUS_WATCH_SECONDS = .4


class WindowPlacement(ctypes.Structure):
    _fields_ = [('length', wt.UINT), ('flags', wt.UINT), ('show', wt.UINT), ('minimized', wt.POINT),
                ('maximized', wt.POINT), ('normal', Rect)]


USER.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(WindowPlacement)]
USER.SetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(WindowPlacement)]
USER.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
USER.GetWindowLongPtrW.restype = ctypes.c_ssize_t
USER.MonitorFromRect.argtypes = [ctypes.POINTER(Rect), wt.DWORD]
USER.MonitorFromRect.restype = wt.HMONITOR


class LastInput(ctypes.Structure):
    _fields_ = [('size', wt.UINT), ('time', wt.DWORD)]


def last_input():
    """When the owner last pressed a key or moved or clicked the mouse, as a Windows tick count."""
    info = LastInput(ctypes.sizeof(LastInput), 0)
    return info.time if USER.GetLastInputInfo(ctypes.byref(info)) else None


class FocusKeeper:
    """Keyboard focus stays where the owner had it while Jarvis moves another window."""

    def __init__(self, moved):
        self.moved, self.returned = moved, False
        self.owner = int(USER.GetForegroundWindow() or 0)
        self.input = last_input()

    def keep(self):
        if not self.owner or self.owner == self.moved or int(USER.GetForegroundWindow() or 0) != self.moved:
            return
        if last_input() != self.input or not USER.IsWindow(self.owner):
            # The owner clicked or typed meanwhile, so the window in front may be the one he chose.
            return
        from desktop.computer import bring_to_front
        bring_to_front(self.owner)
        self.input = last_input()
        self.returned = True


def stored_placement(hwnd):
    data = WindowPlacement()
    data.length = ctypes.sizeof(data)
    if not USER.GetWindowPlacement(hwnd, ctypes.byref(data)):
        raise RuntimeError('Windows did not report where that window sits.')
    return data


def workspace_offset(rect):
    """Placements are in workspace coordinates: screen coordinates less the room a taskbar takes at the top or left."""
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    handle = USER.MonitorFromRect(ctypes.byref(Rect(*rect)), 2)
    if handle and USER.GetMonitorInfoW(handle, ctypes.byref(info)):
        return info.work.left - info.rect.left, info.work.top - info.rect.top
    return 0, 0


def resting_place(window, screens):
    """Where a window sits, or where a minimized one comes back to: its rectangle, monitor and whether maximized."""
    hwnd = window['hwnd']
    monitor = (window_display(window, screens) or {}).get('device')
    if not USER.IsIconic(hwnd):
        return {'rect': window['rect'][:], 'maximized': bool(USER.IsZoomed(hwnd)), 'monitor': monitor}
    data = stored_placement(hwnd)
    rect = [data.normal.left, data.normal.top, data.normal.right, data.normal.bottom]
    across, down = workspace_offset(rect)
    return {'rect': [rect[0] + across, rect[1] + down, rect[2] + across, rect[3] + down],
            'maximized': bool(data.flags & WPF_RESTORETOMAXIMIZED), 'monitor': monitor}


def caption_buttons(window):
    """The screen rectangle of a window's minimize, maximize and close buttons, as the window manager reports it."""
    area = Rect()
    if DWM.DwmGetWindowAttribute(wt.HWND(window['hwnd']), DWMWA_CAPTION_BUTTON_BOUNDS, ctypes.byref(area), ctypes.sizeof(area)):
        return None
    if area.right <= area.left or area.bottom <= area.top:
        return None
    left, top = window['rect'][:2]
    return [left + area.left, top + area.top, left + area.right, top + area.bottom]


def title_spot(window):
    """Where a pointer holds a window to drag it: the middle of its title bar, clear of the buttons."""
    if USER.IsIconic(window['hwnd']):
        return None
    left, top, right, _ = window['rect']
    buttons = caption_buttons(window)
    scale = max(1.0, (USER.GetDpiForWindow(window['hwnd']) or 96) / 96)
    height = buttons[3] - buttons[1] if buttons else round(30 * scale)
    return (left + (buttons[0] if buttons else right)) // 2, top + max(6, height // 2)


def button_spot(window, name):
    """The middle of a window's minimize or close button, or its title bar when it has no standard buttons."""
    if USER.IsIconic(window['hwnd']):
        return None
    buttons = caption_buttons(window)
    if not buttons:
        return title_spot(window)
    left, top, right, bottom = buttons
    middle = (top + bottom) // 2
    if right - left < 2 * (bottom - top):
        # A lone close button, as on a dialog.
        return ((left + right) // 2, middle) if name == 'close' else title_spot(window)
    slot = (right - left) / 3
    return round(left + slot * ({'minimize': 0, 'maximize': 1, 'close': 2}[name] + .5)), middle


def lift(hwnd):
    """Above the other ordinary windows, without activating it."""
    if USER.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST:
        return
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    USER.SetWindowPos(hwnd, wt.HWND(HWND_TOPMOST), 0, 0, 0, 0, flags)
    USER.SetWindowPos(hwnd, wt.HWND(HWND_NOTOPMOST), 0, 0, 0, 0, flags)


def settle(condition, seconds, check=None):
    deadline = time.monotonic() + seconds
    while True:
        if check:
            check()
        if condition():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.03)


def to_normal_state(hwnd, check=None):
    """A minimized or maximized window back to an ordinary one, still without keyboard focus."""
    if USER.IsIconic(hwnd):
        data = stored_placement(hwnd)
        data.flags, data.show = 0, SW_SHOWNOACTIVATE
        bottom = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        USER.SetWindowPos(hwnd, wt.HWND(HWND_BOTTOM), 0, 0, 0, 0, bottom)
        USER.SetWindowPlacement(hwnd, ctypes.byref(data))
        USER.SetWindowPos(hwnd, wt.HWND(HWND_BOTTOM), 0, 0, 0, 0, bottom)
    elif USER.IsZoomed(hwnd):
        USER.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
    else:
        return
    if not settle(lambda: not USER.IsIconic(hwnd) and not USER.IsZoomed(hwnd), 2, check):
        raise RuntimeError('The window did not return to its ordinary size, so it was not moved.')


def shared_area(rect, screens):
    def overlap(other):
        return max(0, min(rect[2], other[2]) - max(rect[0], other[0])) * max(0, min(rect[3], other[3]) - max(rect[1], other[1]))
    return sum(overlap(screen['rect']) for screen in screens)


def requested_place(place, screens):
    rect = [place.get(key) for key in ('left', 'top', 'right', 'bottom')]
    maximize = place.get('maximized')
    if any(type(value) is not int for value in rect) or type(maximize) is not bool:
        raise RuntimeError('Give the place as left, top, right and bottom screen pixels, and whether to maximize the window there.')
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if not (120 <= width <= 16000 and 80 <= height <= 16000):
        raise RuntimeError('That place is too small or too large for a window.')
    if shared_area(rect, screens) * 2 < width * height:
        raise RuntimeError('Most of that place is off the connected displays. Use the rect of a monitor or of another window.')
    return rect, maximize


def place_window(target, place, screens, check=None, point=None):
    """Move a window to a screen rectangle, or maximize it on that rectangle's monitor, dragging it by its title bar
    with Jarvis's pointer. The window is restored and lifted above the others, and never given keyboard focus."""
    from desktop.displays import containing_display
    require_desktop()
    if check:
        check()
    rect, maximize = requested_place(place, screens)
    hwnd = target['hwnd']
    current = window_info(hwnd)
    if not manageable(current) or any(current[key] != target[key] for key in ('pid', 'process')):
        raise RuntimeError('The window changed or is protected. Look again before moving it.')
    check_pointer_stop()
    screen = containing_display(rect, screens)
    if maximize:
        left, top, right, bottom = screen.get('work') or screen['rect']
        across, down = round((right - left) * MAXIMIZED_INSET), round((bottom - top) * MAXIMIZED_INSET)
        # An ordinary size on that monitor first, which is also what the window returns to when un-maximized.
        goal = [left + across, top + down, right - across, bottom - down]
    else:
        goal = rect
    already = (maximize and USER.IsZoomed(hwnd) and not USER.IsIconic(hwnd)
               and (window_display(current, screens) or {}).get('device') == screen['device'])
    focus = FocusKeeper(hwnd)
    if not already:
        to_normal_state(hwnd, check)
        focus.keep()
        start = window_info(hwnd)
        if start is None:
            raise RuntimeError('The window closed before it could be moved.')
        hold = title_spot(start)
        if point and hold:
            point(hold, 'grab')
        lift(hwnd)
        if point and hold:
            # The pointer keeps its hold on the title bar as the window changes size under it.
            share = (hold[0] - start['rect'][0]) / max(1, start['rect'][2] - start['rect'][0])
            point((round(goal[0] + (goal[2] - goal[0]) * share), goal[1] + hold[1] - start['rect'][1]), 'drop', wait=False)
        flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNC
        for frame in range(1, DRAG_FRAMES + 1):
            if check:
                check()
            eased = 1 - (1 - frame / DRAG_FRAMES) ** 3
            box = [round(a + (b - a) * eased) for a, b in zip(start['rect'], goal)]
            ctypes.set_last_error(0)
            if not USER.SetWindowPos(hwnd, None, box[0], box[1], box[2] - box[0], box[3] - box[1], flags):
                denied = ctypes.get_last_error() == 5
                raise RuntimeError('Windows denied access to move that window. Apps running as administrator cannot be moved.'
                                   if denied else 'Windows did not accept the move.')
            time.sleep(DRAG_SECONDS / DRAG_FRAMES)
        settle(lambda: (window_info(hwnd) or {}).get('rect') == goal, 1.2, check)
        if maximize:
            USER.PostMessageW(hwnd, WM_SYSCOMMAND, SC_MAXIMIZE, 0)
            settle(lambda: bool(USER.IsZoomed(hwnd)), 2, check)
    # An app can activate itself a moment after it was restored or maximized, so keep watching briefly.
    settle(lambda: focus.keep() and False, FOCUS_WATCH_SECONDS, check)
    # Last, so it is in front of the rest even when handing focus back raised the owner's window.
    lift(hwnd)
    final = window_info(hwnd)
    if final is None:
        raise RuntimeError('The window closed before the move could be checked.')
    monitor = (window_display(final, screens) or {}).get('device')
    result = {'rect': final['rect'], 'monitor': monitor, 'maximized': bool(USER.IsZoomed(hwnd)), 'error': ''}
    if focus.returned:
        result['focusReturned'] = True
    if maximize:
        result['verified'] = result['maximized'] and monitor == screen['device']
        result['state'] = 'maximized' if result['verified'] else 'not_maximized'
        if not result['verified']:
            result['error'] = ('The window is on that monitor but the app did not maximize it.' if monitor == screen['device']
                               else 'The window did not reach that monitor.')
    else:
        result['verified'] = abs(final['rect'][0] - goal[0]) <= 2 and abs(final['rect'][1] - goal[1]) <= 2
        result['state'] = 'moved' if result['verified'] else 'misplaced'
        if not result['verified']:
            result['error'] = 'The app put the window somewhere else than asked.'
        elif any(abs(final['rect'][index] - goal[index]) > 2 for index in (2, 3)):
            result['note'] = 'The window is in place, but the app keeps its own size limits, so it is not exactly the size asked for.'
    spot = title_spot(final)
    if spot and int(USER.GetAncestor(USER.WindowFromPoint(Point(*spot)), 2) or 0) != hwnd:
        result['note'] = (result.get('note', '') + ' An always-on-top window covers part of it.').strip()
    return result
