from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as wt
import os
from pathlib import Path
import re
import struct
import threading
import time
import zlib

from desktop.windows import desktop_name, normalized, monitors, Rect, MonitorInfo
from desktop.app_identity import role_app
from desktop.app_targets import app_windows, named_apps, window_app, window_score
from desktop.displays import containing_display, display_bounds, display_inventory, select_display
from desktop.accessibility import Accessibility, CONTROL_ACTIONS

USER = ctypes.WinDLL('user32', use_last_error=True)
GDI = ctypes.WinDLL('gdi32', use_last_error=True)
KERNEL = ctypes.WinDLL('kernel32', use_last_error=True)
SHCORE = ctypes.WinDLL('shcore', use_last_error=True)
PTR = ctypes.c_size_t
USER.GetForegroundWindow.restype = wt.HWND
USER.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
USER.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
USER.GetAncestor.argtypes = [wt.HWND, wt.UINT]
USER.GetAncestor.restype = wt.HWND
USER.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(Rect)]
USER.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
USER.GetWindowTextLengthW.argtypes = [wt.HWND]
USER.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
USER.IsWindowVisible.argtypes = [wt.HWND]
USER.IsWindow.argtypes = [wt.HWND]
USER.IsIconic.argtypes = [wt.HWND]
USER.IsZoomed.argtypes = [wt.HWND]
USER.IsZoomed.restype = wt.BOOL
USER.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
USER.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]
USER.ShowWindowAsync.restype = wt.BOOL
USER.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.POINTER(PTR)]
USER.SendMessageTimeoutW.restype = ctypes.c_ssize_t
USER.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
USER.PostMessageW.restype = wt.BOOL
USER.SetForegroundWindow.argtypes = [wt.HWND]
USER.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
USER.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
USER.GetDpiForWindow.argtypes = [wt.HWND]
USER.GetDpiForWindow.restype = wt.UINT
USER.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
USER.MonitorFromWindow.restype = wt.HMONITOR
USER.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MonitorInfo)]
USER.GetShellWindow.restype = wt.HWND
USER.GetDesktopWindow.restype = wt.HWND
SHCORE.GetDpiForMonitor.argtypes = [wt.HMONITOR, ctypes.c_int, ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT)]
SHCORE.GetDpiForMonitor.restype = ctypes.c_long
KERNEL.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
KERNEL.OpenProcess.restype = wt.HANDLE
KERNEL.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
KERNEL.CloseHandle.argtypes = [wt.HANDLE]
USER.GetDC.argtypes = [wt.HWND]
USER.GetDC.restype = wt.HDC
USER.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
GDI.CreateCompatibleDC.argtypes = [wt.HDC]
GDI.CreateCompatibleDC.restype = wt.HDC
GDI.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
GDI.CreateCompatibleBitmap.restype = wt.HBITMAP
GDI.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
GDI.SelectObject.restype = wt.HGDIOBJ
GDI.DeleteObject.argtypes = [wt.HGDIOBJ]
GDI.DeleteDC.argtypes = [wt.HDC]
GDI.StretchBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD]
GDI.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
GDI.SetBrushOrgEx.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, wt.LPVOID]
GDI.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, wt.LPVOID, wt.LPVOID, wt.UINT]

OWN_PROCESSES = {os.getpid()}
NEW_WINDOW = re.compile(r'\b(?:new|another|second|separate|extra)\s+(?:\w+\s+){0,2}(?:window|instance|copy)\b', re.I)
# Addresses that carry their own content or code. Typed into a browser they show or run whatever they hold.
CONTENT_ADDRESS = re.compile(r'^\s*(?:data|javascript|vbscript)\s*:', re.I)
CONTENT_REFUSED = ('Typing a data: or script address into an app is not allowed, and it is no way to show a drawing or a page. '
                   'Report that the goal needs a drawn page instead.')
BLOCKED_APPS = {'outlook.exe', 'olk.exe', 'hxoutlook.exe', 'thunderbird.exe', 'cmd.exe',
                'powershell.exe', 'pwsh.exe', 'windowsterminal.exe', 'conhost.exe', 'credentialuibroker.exe',
                'consent.exe', 'vibecode.exe', 'keepass.exe', '1password.exe', 'bitwarden.exe'}
MAIL_WINDOW = re.compile(r'\b(gmail|outlook|thunderbird|inbox|compose|new message)\b', re.I)



class Point(ctypes.Structure):
    _fields_ = [('x', wt.LONG), ('y', wt.LONG)]


class MouseInput(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
               ('dwFlags', wt.DWORD), ('time', wt.DWORD), ('dwExtraInfo', PTR)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD), ('time', wt.DWORD), ('dwExtraInfo', PTR)]


class InputUnion(ctypes.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyboardInput)]


class Input(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('data', InputUnion)]


USER.SendInput.argtypes = [wt.UINT, ctypes.POINTER(Input), ctypes.c_int]
USER.WindowFromPoint.argtypes = [Point]
USER.WindowFromPoint.restype = wt.HWND
USER.GetCursorPos.argtypes = [ctypes.POINTER(Point)]
KEYS = {'enter': 13, 'escape': 27, 'tab': 9, 'space': 32, 'up': 38, 'down': 40, 'left': 37, 'right': 39,
        'backspace': 8, 'delete': 46, 'home': 36, 'end': 35, 'pageup': 33, 'pagedown': 34, 'f2': 113, 'f5': 116}
EDIT_KEYS = {'select_all':0x41, 'copy':0x43, 'paste':0x56, 'undo':0x5A, 'redo':0x59, 'save':0x53}
KEY_ALIASES = {'select_all': 'ctrl+a', 'copy': 'ctrl+c', 'paste': 'ctrl+v', 'cut': 'ctrl+x',
               'undo': 'ctrl+z', 'redo': 'ctrl+y', 'save': 'ctrl+s'}
MODIFIERS = {'ctrl': 17, 'shift': 16, 'alt': 18}
SHORTCUTS = {*(f'ctrl+{letter}' for letter in 'acvxyzsfltwron'),
             'ctrl+shift+t', 'ctrl+shift+n', 'ctrl+shift+s', 'ctrl+tab', 'ctrl+shift+tab',
             'alt+left', 'alt+right', 'alt+up', 'alt+f4', 'shift+tab',
             'shift+left', 'shift+right', 'shift+up', 'shift+down', 'shift+home', 'shift+end'}


def stop_requested(text):
    text = re.sub(r'[^a-z0-9 ]', '', text.lower())
    text = re.sub(r'\b(?:hey|jarvis|please)\b', '', text)
    text = ' '.join(text.split())
    return bool(re.fullmatch(r'(?:stop|cancel)(?: (?:the |my |that )?(?:(?:pc|computer) )?task| clicking| controlling (?:the |my )?(?:pc|computer))?', text))


def require_desktop():
    if desktop_name().lower() != 'default':
        raise RuntimeError('PC control needs your visible Windows desktop. Run setup-jarvis.cmd from Windows first.')


def window_info(hwnd):
    if not hwnd or not USER.IsWindow(hwnd):
        return None
    title = ctypes.create_unicode_buffer(512)
    USER.GetWindowTextW(hwnd, title, len(title))
    process_id = wt.DWORD()
    USER.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    handle = KERNEL.OpenProcess(0x1000, False, process_id.value)
    process = path = ''
    if handle:
        try:
            name = ctypes.create_unicode_buffer(32768)
            length = wt.DWORD(len(name))
            if KERNEL.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
                path = name.value
                process = Path(path).name.lower()
        finally:
            KERNEL.CloseHandle(handle)
    rect = Rect()
    if not USER.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return {'hwnd': int(hwnd), 'pid': process_id.value, 'title': title.value,
            'process': process, 'path': path, 'rect': [rect.left, rect.top, rect.right, rect.bottom]}


def controllable(window):
    return bool(window and window['process'] and window['pid'] not in OWN_PROCESSES
                and window['process'] not in BLOCKED_APPS and not MAIL_WINDOW.search(window['title']))


def manageable(window):
    return bool(window and window['process'] and window['pid'] not in OWN_PROCESSES
                and window['process'] not in {'credentialuibroker.exe', 'consent.exe', 'lockapp.exe',
                                             'shellexperiencehost.exe', 'searchhost.exe'})


def visible_windows():
    rows = []
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _data):
        if USER.IsWindowVisible(hwnd) and hwnd not in (USER.GetShellWindow(), USER.GetDesktopWindow()):
            row = window_info(hwnd)
            if row and row['title'] and manageable(row):
                rows.append(row)
        return True
    callback = callback_type(visit)
    USER.EnumWindows(callback, 0)
    return rows


def observed_window_state(target):
    current = window_info(target['hwnd'])
    if current is None:
        return 'closed'
    if any(current[key] != target[key] for key in ('pid', 'process')):
        return 'replaced'
    if not USER.IsWindowVisible(target['hwnd']):
        return 'hidden'
    if USER.IsIconic(target['hwnd']):
        return 'minimized'
    return 'visible'


def close_window(target, check=None):
    require_desktop()
    if check:
        check()
    current = window_info(target['hwnd'])
    if not manageable(current) or any(current[key] != target[key] for key in ('pid', 'process')):
        raise RuntimeError('The window changed or is protected. Look again before closing it.')
    check_pointer_stop()
    ctypes.set_last_error(0)
    if not USER.PostMessageW(current['hwnd'], 0x0010, 0, 0):
        code = ctypes.get_last_error()
        raise RuntimeError('Windows denied access to close that window.' if code == 5 else 'Windows did not accept the close request.')
    for _ in range(40):
        if check:
            check()
        state = observed_window_state(current)
        if state == 'closed':
            return {'verified': True, 'state': state, 'error': ''}
        if state == 'replaced':
            raise RuntimeError('The window identity changed before closing could be verified.')
        time.sleep(.05)
    if state == 'hidden':
        return {'verified': True, 'state': state, 'error': ''}
    return {'verified': False, 'state': 'still_open',
            'error': 'The close request was sent, but the window is still open. Inspect for an unsaved-work prompt or an app refusing to close.'}


def window_display(window, screens):
    if USER.IsIconic(window['hwnd']):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        handle = USER.MonitorFromWindow(window['hwnd'], 2)
        if handle and USER.GetMonitorInfoW(handle, ctypes.byref(info)):
            found = next((screen for screen in screens if screen['device'] == info.device), None)
            if found:
                return found
    return containing_display(window['rect'], screens)


def minimize_window(target, check=None):
    require_desktop()
    if check:
        check()
    current = window_info(target['hwnd'])
    if not manageable(current) or any(current[key] != target[key] for key in ('pid', 'process', 'rect')):
        raise RuntimeError('The window changed or moved. Look again before minimizing it.')
    if USER.IsIconic(current['hwnd']):
        return
    def verified():
        if check:
            check()
        fresh = window_info(current['hwnd'])
        if not fresh or any(fresh[key] != current[key] for key in ('pid', 'process')):
            raise RuntimeError('The window closed or changed before minimization could be verified.')
        return bool(USER.IsIconic(current['hwnd']))

    ctypes.set_last_error(0)
    started = USER.ShowWindowAsync(current['hwnd'], 7)
    first_error = ctypes.get_last_error()
    for _ in range(20 if started else 1):
        if verified():
            return
        if started:
            time.sleep(.05)
    fresh = window_info(current['hwnd'])
    if not fresh or any(fresh[key] != current[key] for key in ('pid', 'process', 'rect')):
        raise RuntimeError('The window changed or moved before the minimize command.')
    if check:
        check()
    result = PTR()
    ctypes.set_last_error(0)
    sent = USER.SendMessageTimeoutW(current['hwnd'], 0x0112, 0xF020, 0, 0x0002 | 0x0020, 1000, ctypes.byref(result))
    command_error = ctypes.get_last_error()
    for _ in range(20 if sent else 1):
        if verified():
            return
        if sent:
            time.sleep(.05)
    code = command_error or first_error
    reason = ('Windows denied access to its minimize command.' if code == 5 else
              'The app did not respond to its minimize command.' if code == 1460 else
              'Its minimized state could not be verified.')
    raise RuntimeError(f"Could not minimize {current['title']} ({current['process']}). {reason}")


def encode_png(width, height, bgra):
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    stride = width * 3
    pixels = b''.join(b'\0' + rgb[row * stride:(row + 1) * stride] for row in range(height))
    def chunk(name, data):
        return struct.pack('!I', len(data)) + name + data + struct.pack('!I', zlib.crc32(name + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels, 3)) + chunk(b'IEND', b'')


def scaled_size(source_width, source_height):
    if source_width < 20 or source_height < 20 or source_width > 16000 or source_height > 16000:
        raise RuntimeError('That window has no usable visible area')
    scale = min(1, 1400 / source_width, 1000 / source_height)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def frame_from(dc, source, origin, size, rect, require_detail=False):
    (x, y), (source_width, source_height) = origin, size
    width, height = scaled_size(source_width, source_height)
    memory = GDI.CreateCompatibleDC(dc)
    bitmap = GDI.CreateCompatibleBitmap(dc, width, height)
    old = GDI.SelectObject(memory, bitmap)
    try:
        GDI.SetStretchBltMode(memory, 4)
        GDI.SetBrushOrgEx(memory, 0, 0, None)
        if not GDI.StretchBlt(memory, 0, 0, width, height, source, x, y, source_width, source_height, 0x00CC0020):
            raise RuntimeError('Windows could not capture this screen. Run Jarvis on your visible desktop.')
        GDI.SelectObject(memory, old)
        info = ctypes.create_string_buffer(struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0))
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not GDI.GetDIBits(memory, bitmap, 0, height, pixels, info, 0):
            raise RuntimeError('Windows did not return screenshot pixels')
        raw = pixels.raw
        if require_detail and len({raw[i:i + 3] for i in range(0, len(raw), 4 * 499)}) < 2:
            return None
        png = encode_png(width, height, raw)
        return {'image': base64.b64encode(png).decode('ascii'), 'width': width, 'height': height,
                'rect': rect[:], 'captured': time.monotonic()}
    finally:
        GDI.SelectObject(memory, old)
        GDI.DeleteObject(bitmap)
        GDI.DeleteDC(memory)


def capture_rect(rect):
    require_desktop()
    left, top, right, bottom = rect
    dc = USER.GetDC(None)
    try:
        return frame_from(dc, dc, (left, top), (right - left, bottom - top), rect)
    finally:
        USER.ReleaseDC(None, dc)


def matches_screen_dpi(hwnd):
    dpi_x, dpi_y = wt.UINT(), wt.UINT()
    monitor = USER.MonitorFromWindow(hwnd, 2)
    return bool(monitor) and not SHCORE.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) \
        and USER.GetDpiForWindow(hwnd) == dpi_x.value


def capture_window(target):
    require_desktop()
    left, top, right, bottom = rect = target['rect']
    width, height = right - left, bottom - top
    scaled_size(width, height)
    if matches_screen_dpi(target['hwnd']):
        dc = USER.GetDC(None)
        memory = GDI.CreateCompatibleDC(dc)
        bitmap = GDI.CreateCompatibleBitmap(dc, width, height)
        old = GDI.SelectObject(memory, bitmap)
        try:
            if USER.PrintWindow(target['hwnd'], memory, 2):
                frame = frame_from(dc, memory, (0, 0), (width, height), rect, require_detail=True)
                if frame:
                    frame['source'] = 'window'
                    if chromium_window(target['hwnd']) and fully_covered(target):
                        # Chromium stopped painting this covered window: its page area shows an older frame.
                        frame['paused'] = True
                    return frame
        finally:
            GDI.SelectObject(memory, old)
            GDI.DeleteObject(bitmap)
            GDI.DeleteDC(memory)
            USER.ReleaseDC(None, dc)
    frame = capture_rect(rect)
    frame['source'] = 'screen'
    return frame


def image_point(frame, x, y):
    if type(x) is not int or type(y) is not int or not (0 <= x < frame['width'] and 0 <= y < frame['height']):
        raise RuntimeError('The requested click is outside the screenshot')
    left, top, right, bottom = frame['rect']
    return left + round(x * (right - left) / frame['width']), top + round(y * (bottom - top) / frame['height'])


def send_inputs(*inputs):
    array = (Input * len(inputs))(*inputs)
    if USER.SendInput(len(inputs), array, ctypes.sizeof(Input)) != len(inputs):
        raise RuntimeError('Windows blocked input to that app. I cannot control elevated or protected windows.')


def mouse(flags, x=0, y=0, data=0):
    return Input(0, InputUnion(mi=MouseInput(x, y, data & 0xffffffff, flags, 0, 0)))


def keep_on_top(hwnd):
    USER.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)


def reveal_behind(hwnd):
    USER.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    USER.ShowWindow(hwnd, 4)
    USER.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    for _ in range(20):
        if not USER.IsIconic(hwnd):
            break
        time.sleep(.05)


def bring_to_front(hwnd):
    if USER.IsIconic(hwnd):
        USER.ShowWindow(hwnd, 9)
    if int(USER.GetForegroundWindow() or 0) != hwnd:
        USER.SendInput(1, ctypes.byref(mouse(0)), ctypes.sizeof(Input))
        USER.SetForegroundWindow(hwnd)
        deadline = time.monotonic() + 2
        while int(USER.GetForegroundWindow() or 0) != hwnd and time.monotonic() < deadline:
            time.sleep(.05)
    keep_on_top(hwnd)
    time.sleep(.3)


def key_inputs(value):
    if not isinstance(value, str):
        raise RuntimeError('That keyboard shortcut is not available')
    value = KEY_ALIASES.get(value, value)
    if value not in KEYS and value not in SHORTCUTS:
        raise RuntimeError('That keyboard shortcut is not available')
    parts = value.split('+')
    final = parts[-1]
    code = KEYS.get(final, 115 if final == 'f4' else ord(final.upper()) if len(final) == 1 else None)
    return [MODIFIERS[part] for part in parts[:-1]] + [code]


class InputStopped(RuntimeError):
    pass


def check_pointer_stop():
    pointer = Point()
    USER.GetCursorPos(ctypes.byref(pointer))
    if pointer.x <= USER.GetSystemMetrics(76) + 1 and pointer.y <= USER.GetSystemMetrics(77) + 1:
        raise InputStopped('PC control stopped because the pointer is in the top-left corner.')


def apply_action(target, frame, action, owner='', check=None):
    require_desktop()
    current = window_info(target['hwnd'])
    if not controllable(current) or current['pid'] != target['pid'] or current['rect'] != frame['rect']:
        raise RuntimeError('The app changed or moved. Ask me to look again before clicking.')
    if time.monotonic() - frame['captured'] > 80:
        raise RuntimeError('The screenshot is too old to click safely. Ask me to look again.')
    if int(USER.GetForegroundWindow() or 0) != target['hwnd']:
        raise RuntimeError('Another window took focus. PC control stopped so it does not click the wrong app.')
    check_pointer_stop()
    operation = action.get('action')
    if operation in ('click', 'double_click', 'right_click', 'scroll'):
        x, y = image_point(frame, action.get('x'), action.get('y'))
        under = USER.GetAncestor(USER.WindowFromPoint(Point(x, y)), 2)
        if int(under or 0) != target['hwnd']:
            raise RuntimeError('Another window covers that control. PC control stopped.')
        left, top = USER.GetSystemMetrics(76), USER.GetSystemMetrics(77)
        width, height = USER.GetSystemMetrics(78), USER.GetSystemMetrics(79)
        send_inputs(mouse(0x8000 | 0x4000 | 0x0001, round((x-left)*65535/max(1,width-1)), round((y-top)*65535/max(1,height-1))))
        if operation == 'scroll':
            amount = action.get('scroll')
            if type(amount) is not int or not -6 <= amount <= 6:
                raise RuntimeError('Invalid scroll distance')
            send_inputs(mouse(0x0800, data=amount * 120))
        else:
            if operation == 'right_click':
                send_inputs(mouse(0x0008), mouse(0x0010))
            else:
                send_inputs(mouse(0x0002), mouse(0x0004))
            if operation == 'double_click':
                time.sleep(.08)
                send_inputs(mouse(0x0002), mouse(0x0004))
    elif operation == 'type':
        text = action.get('text', '')
        if not isinstance(text, str) or not 1 <= len(text) <= 4000 or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
            raise RuntimeError('The requested text must contain 1 to 4000 ordinary characters')
        data = text.encode('utf-16-le')
        for offset in range(0,len(data),2):
            if check:
                check()
            if int(USER.GetForegroundWindow() or 0) != target['hwnd']:
                raise RuntimeError('Focus changed while typing. Input stopped.')
            code = int.from_bytes(data[offset:offset+2], 'little')
            send_inputs(Input(1,InputUnion(ki=KeyboardInput(0,code,4,0,0))),
                        Input(1,InputUnion(ki=KeyboardInput(0,code,6,0,0))))
    elif operation == 'key':
        pressed = []
        try:
            for key in key_inputs(action.get('key', '')):
                if check:
                    check()
                if int(USER.GetForegroundWindow() or 0) != target['hwnd']:
                    raise RuntimeError('Focus changed before the shortcut. Input stopped.')
                pressed.append(key)
                send_inputs(Input(1, InputUnion(ki=KeyboardInput(key, 0, 0, 0, 0))))
        finally:
            for key in reversed(pressed):
                send_inputs(Input(1, InputUnion(ki=KeyboardInput(key, 0, 2, 0, 0))))
    else:
        raise RuntimeError('Unknown PC action')


# Jarvis's own pointer. Input goes straight to the window under a point as window messages, so the owner keeps
# their real pointer, keyboard and focus, and it reaches the app even while another window covers it.
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK = 0x0200, 0x0201, 0x0202, 0x0203
WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MOUSEWHEEL = 0x0204, 0x0205, 0x020A
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002
CWP_SKIP_HIDDEN = 0x0001 | 0x0002 | 0x0004
POINTER_GLIDE_SECONDS = .45
USER.ChildWindowFromPointEx.argtypes = [wt.HWND, Point, wt.UINT]
USER.ChildWindowFromPointEx.restype = wt.HWND
USER.ScreenToClient.argtypes = [wt.HWND, ctypes.POINTER(Point)]
USER.ScreenToClient.restype = wt.BOOL
USER.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
USER.MapVirtualKeyW.restype = wt.UINT
USER.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
CHROMIUM_CLASS = 'Chrome_WidgetWin_'


def chromium_window(hwnd):
    """Chrome, Edge, Opera, Brave and Electron apps. While another window fully covers one it stops painting its
    page, it drops wheel input aimed at covered spots, and UI Automation invoke or set_value brings it forward."""
    name = ctypes.create_unicode_buffer(64)
    return bool(USER.GetClassNameW(hwnd, name, 64)) and name.value.startswith(CHROMIUM_CLASS)


def fully_covered(target, samples=6):
    """Whether other windows, or the edges of the screens, hide every sampled point of a window."""
    left, top, right, bottom = target['rect']
    for row in range(samples):
        for column in range(samples):
            point = Point(left + (2 * column + 1) * (right - left) // (2 * samples),
                          top + (2 * row + 1) * (bottom - top) // (2 * samples))
            if int(USER.GetAncestor(USER.WindowFromPoint(point), 2) or 0) == target['hwnd']:
                return False
    return True


def pack_point(x, y):
    return (x & 0xffff) | ((y & 0xffff) << 16)


def child_at(hwnd, x, y):
    """The deepest visible, enabled child of a window under a screen point, even while the window is covered."""
    current = int(hwnd)
    for _ in range(32):
        point = Point(x, y)
        if not USER.ScreenToClient(current, ctypes.byref(point)):
            break
        child = int(USER.ChildWindowFromPointEx(current, point, CWP_SKIP_HIDDEN) or 0)
        if not child or child == current:
            break
        current = child
    return current


def window_message(hwnd, message, wparam, lparam):
    result = PTR()
    if not USER.SendMessageTimeoutW(hwnd, message, wparam, lparam, 0x0002 | 0x0020, 500, ctypes.byref(result)):
        raise RuntimeError('The app did not accept input sent in the background.')


def background_point(target, frame, action):
    """The screen point of a background pointer action, checked against the screenshot it was chosen from."""
    require_desktop()
    if not isinstance(frame, dict) or any(key not in frame for key in ('rect', 'width', 'height', 'captured')):
        raise RuntimeError('There is no current screenshot of this window. Ask me to look again before clicking.')
    current = window_info(target['hwnd'])
    if (not controllable(current) or current['pid'] != target['pid'] or current['rect'] != frame['rect']
            or USER.IsIconic(target['hwnd'])):
        raise RuntimeError('The app changed or moved. Ask me to look again before clicking.')
    if time.monotonic() - frame['captured'] > 80:
        raise RuntimeError('The screenshot is too old to click safely. Ask me to look again.')
    x, y = image_point(frame, action.get('x'), action.get('y'))
    covered = int(USER.GetAncestor(USER.WindowFromPoint(Point(x, y)), 2) or 0) != target['hwnd']
    if frame.get('source') != 'window' and covered:
        # The screenshot shows whatever covers the app there, not the app itself.
        raise RuntimeError('Another window covers that spot in the screenshot. Use a listed control instead.')
    if frame.get('paused') and covered:
        raise RuntimeError('This app is fully covered, so it paused painting and the screenshot may be out of date. '
                           'Use a listed control, whose position stays current.')
    return x, y


def background_pointer(target, frame, action):
    """Click, double click, right click or scroll at a screenshot point without moving the owner's pointer.
    Returns the window that took it, which then receives background typing, and the screen point."""
    x, y = background_point(target, frame, action)
    check_pointer_stop()
    child = child_at(target['hwnd'], x, y)
    client = Point(x, y)
    if not USER.ScreenToClient(child, ctypes.byref(client)):
        raise RuntimeError('Windows could not locate that point in the app.')
    at = pack_point(client.x, client.y)
    operation = action.get('action')
    if operation == 'scroll':
        amount = action.get('scroll')
        if type(amount) is not int or not -6 <= amount <= 6:
            raise RuntimeError('Invalid scroll distance')
        if chromium_window(target['hwnd']) and int(USER.GetAncestor(USER.WindowFromPoint(Point(x, y)), 2) or 0) != target['hwnd']:
            raise RuntimeError('This app ignores background scrolling where another window covers it. '
                               'Use scroll_into_view on a listed control instead.')
        window_message(child, WM_MOUSEMOVE, 0, at)
        # The wheel message carries screen coordinates.
        window_message(child, WM_MOUSEWHEEL, ((amount * 120) & 0xffff) << 16, pack_point(x, y))
        return child, (x, y)
    down, up, held = (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON) if operation == 'right_click' else (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON)
    sequence = [(WM_MOUSEMOVE, 0), (down, held), (up, 0)]
    if operation == 'double_click':
        sequence += [(WM_LBUTTONDBLCLK, MK_LBUTTON), (WM_LBUTTONUP, 0)]
    for message, keys in sequence:
        window_message(child, message, keys, at)
    return child, (x, y)


def background_keys(child, action, check=None):
    """Type text, or press one key, in the control that last took a background click."""
    if not child or not USER.IsWindow(child):
        raise RuntimeError('Click the field in the background first, or use set_value.')
    if action.get('action') == 'type':
        text = action.get('text', '')
        if not isinstance(text, str) or not 1 <= len(text) <= 4000 or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
            raise RuntimeError('The requested text must contain 1 to 4000 ordinary characters')
        data = text.replace('\r\n', '\r').replace('\n', '\r').encode('utf-16-le')
        for offset in range(0, len(data), 2):
            if check and offset % 64 == 0:
                check()
            if not USER.PostMessageW(child, WM_CHAR, int.from_bytes(data[offset:offset + 2], 'little'), 1):
                raise RuntimeError('The app stopped accepting typed text.')
        return
    name = KEY_ALIASES.get(action.get('key', ''), action.get('key', ''))
    if name not in KEYS:
        raise RuntimeError('Only single keys such as enter, tab or escape work in the background; shortcuts need focus_window.')
    code = KEYS[name]
    pressed = 1 | (USER.MapVirtualKeyW(code, 0) << 16)
    if not (USER.PostMessageW(child, WM_KEYDOWN, code, pressed) and USER.PostMessageW(child, WM_KEYUP, code, pressed | 0xC0000000)):
        raise RuntimeError('The app did not accept that key.')


def window_place(window, screens):
    """Where a window sits, or where a minimized one returns to. None when Windows will not say, so describing a
    window never stops the action on it."""
    try:
        from desktop import window_moves
        return window_moves.resting_place(window, screens)
    except Exception:
        return None


def window_spot(window, name):
    """Where Jarvis's pointer goes on a window: its minimize or close button, or its title bar."""
    try:
        from desktop import window_moves
        return window_moves.title_spot(window) if name == 'title' else window_moves.button_spot(window, name)
    except Exception:
        return None


def window_row(key, row, screens, apps):
    """One window of the desktop snapshot. A minimized window also says where it returns to."""
    minimized = bool(USER.IsIconic(row['hwnd']))
    entry = {'id': key, 'title': row['title'], 'process': row['process'], 'app': window_app(apps, row),
             'rect': row['rect'], 'monitor': window_display(row, screens)['device'], 'inputAllowed': controllable(row),
             'minimized': minimized, 'maximized': bool(USER.IsZoomed(row['hwnd'])) and not minimized}
    place = window_place(row, screens) if minimized else None
    if place:
        entry.update(restoredRect=place['rect'], restoresMaximized=place['maximized'])
    return entry


def control_spot(inspection, identifier):
    """The centre of a listed control, where Jarvis's pointer goes before acting on it."""
    for row in (inspection or {}).get('controls', []) if isinstance(inspection, dict) else []:
        if row.get('id') == identifier and len(row.get('rect') or []) == 4:
            left, top, right, bottom = row['rect']
            return (left + right) // 2, (top + bottom) // 2
    return None


class ComputerControl:
    # Set by the controller to draw Jarvis's own pointer on screen while he works.
    pointer = None

    def point(self, spot, action, wait=True):
        """Glide Jarvis's pointer to a screen point before acting there, so the owner can see where he works.
        Without wait the glide runs alongside what follows, as when a window is dragged under it."""
        if not self.pointer or not spot:
            return
        try:
            self.pointer(x=int(spot[0]), y=int(spot[1]), action=action)
        except Exception:
            return
        if wait:
            time.sleep(POINTER_GLIDE_SECONDS)

    def hide_pointer(self):
        if self.pointer:
            try:
                self.pointer(hide=True)
            except Exception:
                pass

    def __init__(self, apps):
        self.apps = apps
        self.lock = threading.Lock()
        self.previous_task = None

    @contextmanager
    def desktop_slot(self, job, check):
        while not self.lock.acquire(timeout=.2):
            check(job)
        try:
            check(job)
            yield
        finally:
            self.lock.release()

    @staticmethod
    def app_window(chosen):
        return next((row for row in visible_windows() if window_score(chosen, row)), None)

    def open_window(self, question):
        choices = named_apps(self.apps.apps, question)
        if not choices:
            words = set(normalized(question).split()) - {'the', 'my', 'app', 'window', 'screen', 'does', 'what', 'say', 'read'}
            choices = [app for app in self.apps.apps if any(len(word) > 2 and word in words
                       for word in normalized(app['Name']).split())]
        return self.app_window(choices[0]) if len(choices) == 1 else None

    def run(self, job, update, check, ask):
        from desktop import window_moves
        require_desktop()
        with self.desktop_slot(job, check):
            check(job)
            previous_dpi = USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            restored = []
            try:
                readonly = bool(job.get('_readOnly', False))
                if not self.apps.apps:
                    self.apps.refresh()
                applications = {f'app:{index}': row for index, row in enumerate(self.apps.apps)}
                target = None
                foreground_input = False
                selected_display = 'all'
                last_window_action = None
                window_results = {}
                launch_requests = {}
                accessibility = Accessibility()
                last_control_action = None
                action_history = []
                window_targets = {}
                # Per window: the child that took Jarvis's last background click, and where it was.
                background_fields, background_spots = {}, {}
                defaults = {'browser': (role_app(self.apps.apps, 'browser') or {}).get('Name'),
                            'email': (role_app(self.apps.apps, 'email') or {}).get('Name')}
                steps = []
                for _ in range(30):
                    check(job)
                    foreground = window_info(USER.GetForegroundWindow())
                    if target:
                        current = window_info(target['hwnd'])
                        if current is None or USER.IsIconic(target['hwnd']):
                            target = None
                        elif current['pid'] != target['pid'] or current['process'] != target['process'] or not controllable(current):
                            raise RuntimeError('The selected app changed. PC control stopped.')
                        else:
                            target = current
                    screens = monitors()
                    if not screens:
                        raise RuntimeError('Windows did not expose any connected displays.')
                    windows = {f'window:{row["hwnd"]}': row for row in visible_windows()}
                    for result_key, result in window_results.items():
                        if result['action'] == 'close_window' and result['state'] in ('still_open', 'hidden'):
                            state = observed_window_state(window_targets[result_key])
                            if state in ('closed', 'hidden'):
                                result.update(verified=True, state=state, error='')
                    scene = {'applications': [{'id': key, 'name': row['Name']} for key, row in applications.items()],
                             'windows': [window_row(key, row, screens, self.apps.apps) for key, row in windows.items()],
                             'launchRequests': list(launch_requests.values()),
                             'defaults': defaults,
                             'monitors': display_inventory(screens),
                             'selectedWindow': f'window:{target["hwnd"]}' if target else None,
                             'foregroundInput': foreground_input,
                             'requestWindow': f'window:{job.get("_foreground", 0)}',
                             'activeWindow': f'window:{foreground["hwnd"]}' if foreground else None}
                    if last_window_action:
                        scene['lastWindowAction'] = last_window_action
                    if last_control_action:
                        scene['lastControlAction'] = last_control_action
                    scene['windowResults'] = list(window_results.values())
                    scene['actionHistory'] = action_history[:]
                    scene['lastAction'] = action_history[-1] if action_history else None
                    if self.previous_task and time.monotonic() - self.previous_task['finished'] < 180:
                        scene['previousTask'] = {key: value for key, value in self.previous_task.items() if key != 'finished'}
                    update(job, stage='looking', note='Looking at ' + (target['title'] if target else 'your screens' if selected_display == 'all' else selected_display))
                    if target:
                        scene['accessibility'] = accessibility.inspect(target, check=lambda: check(job))
                        frame = capture_window(target)
                    else:
                        display = select_display(selected_display, screens, foreground['rect'] if foreground else None)
                        frame = capture_rect(display['rect'] if display else display_bounds(screens))
                    scene['view'] = {'display': selected_display if not target else window_display(target, screens)['device'],
                                     'rect': frame.get('rect'), 'kind': 'window' if target else 'display',
                                     'source': frame.get('source', 'screen'),
                                     'pagePaused': bool(frame.get('paused')),
                                     'foreground': bool(target and foreground and foreground['hwnd'] == target['hwnd'])}
                    update(job, _frame=frame, _desktop=scene, app=target['title'] if target else 'Desktop', steps=steps[:])
                    action = ask(job, frame, steps, readonly)
                    check(job)
                    if not isinstance(action, dict):
                        raise RuntimeError('The model did not return a desktop action')
                    description = str(action.get('message', ''))[:400]
                    operation = action.get('action')
                    if operation == 'done':
                        if action.get('outcome', 'completed') != 'completed':
                            raise RuntimeError(description or 'The desktop request was not fully completed.')
                        failures = [row for row in window_results.values() if not row['verified']]
                        if failures:
                            done = sum(row['verified'] for row in window_results.values())
                            summary = (f'Minimized {done} window' + ('s' if done != 1 else '')
                                       if all(row['action'] == 'minimize_window' for row in window_results.values())
                                       else description or f'{done} of {len(window_results)} window actions verified')
                            summary += '. ' + ' '.join(row['title'] + ': ' + row['error'] for row in failures)
                            update(job, status='failed', stage='failed', note=summary, summary=summary,
                                   error=summary, finished=time.time())
                            return
                        self.previous_task = {'question': job['question'][:1000],
                                              'window': target['title'] if target else None, 'finished': time.monotonic()}
                        update(job, status='completed', stage='completed', note='PC task finished',
                               summary=description or 'Finished, sir.', finished=time.time())
                        return
                    if operation == 'blocked':
                        raise RuntimeError(description or 'I could not identify the correct control on screen')
                    if operation == 'view_display':
                        display = select_display(action.get('target'), screens, foreground['rect'] if foreground else None)
                        selected_display = display['device'] if display else 'all'
                        target = None
                        steps.append('Viewed ' + selected_display)
                        action_history.append({'action': operation, 'target': action.get('target'),
                                               'result': {'ok': True, 'display': selected_display}})
                        continue
                    if readonly:
                        raise RuntimeError('This request only permits looking at the screen')
                    if operation not in ('open_app', 'inspect_window', 'focus_window', 'minimize_window', 'close_window', 'move_window', 'click', 'double_click', 'right_click', 'scroll', 'type', 'key', 'wait', *CONTROL_ACTIONS):
                        raise RuntimeError('The model returned an unknown desktop action')
                    if isinstance(action.get('text'), str) and CONTENT_ADDRESS.match(action['text']):
                        # Refused before anything is sent, and reported to the model so it can say why.
                        steps.append('Input not sent: ' + CONTENT_REFUSED)
                        action_history.append({'action': operation, 'target': action.get('target', ''),
                                               'result': {'ok': False, 'status': 'input_not_sent', 'error': CONTENT_REFUSED}})
                        update(job, steps=steps[:])
                        continue
                    acted_window = target
                    action_result = {'ok': True, 'status': 'action_requested'}
                    update(job, stage='acting', note='Preparing ' + operation.replace('_', ' '))
                    if operation == 'open_app':
                        chosen = applications.get(action.get('target'))
                        if chosen is None:
                            raise RuntimeError('Choose an app from the current installed-app list')
                        identifier = action['target']
                        existing = app_windows(chosen, windows)
                        if existing and identifier not in launch_requests and not NEW_WINDOW.search(job['question']):
                            description = chosen['Name'] + ' is already open, so no new copy was launched'
                            action_result = {'ok': False, 'status': 'already_open', 'windows': existing,
                                             'error': chosen['Name'] + ' already has a window. Inspect that window instead of opening another copy.'}
                        else:
                            if identifier in launch_requests:
                                description = 'Waiting for ' + chosen['Name'] + '; launch was already requested'
                            else:
                                result = self.apps.launch(chosen['Name'])
                                if not result.get('ok'):
                                    raise RuntimeError('Windows could not open ' + chosen['Name'])
                                launch_requests[identifier] = {'id': identifier, 'name': chosen['Name'], 'status': 'launch_requested'}
                            target = None
                    elif operation in ('minimize_window', 'close_window', 'move_window'):
                        chosen = windows.get(action.get('target'))
                        if chosen is None:
                            raise RuntimeError('Choose a window from the current desktop snapshot before operating it.')
                        acted_window = chosen
                        identifier = action['target']
                        result_key = (identifier, operation)
                        previous = window_results.get(result_key)
                        # A window can be moved again, to another place or after a failed try.
                        if previous and not previous['verified'] and operation != 'move_window':
                            raise RuntimeError('That window action already failed. ' + previous['error'])
                        try:
                            if operation == 'move_window':
                                outcome = window_moves.place_window(chosen, action, screens, check=lambda: check(job), point=self.point)
                            elif operation == 'minimize_window':
                                # Where it was, for a request such as put another window in its place.
                                was = window_place(chosen, screens)
                                self.point(window_spot(chosen, 'minimize'), 'click')
                                minimize_window(chosen, check=lambda: check(job))
                                outcome = {'verified': True, 'state': 'minimized', 'error': '', **({'was': was} if was else {})}
                            else:
                                self.point(window_spot(chosen, 'close'), 'click')
                                outcome = close_window(chosen, check=lambda: check(job))
                            result = {'action': operation, 'target': identifier, 'title': chosen['title'],
                                      'app': chosen['process'], **outcome}
                            if operation == 'move_window':
                                description = (f"Moved {chosen['title']} {'maximized onto' if outcome['maximized'] else 'to its place on'} "
                                               f"{outcome['monitor']} (verified)" if outcome['verified'] else outcome['error'])
                            else:
                                description = (('Minimized ' if operation == 'minimize_window' else 'Closed ' if outcome['state'] == 'closed' else 'Hidden ')
                                               + chosen['title'] + ' (verified)' if outcome['verified'] else outcome['error'])
                        except InputStopped:
                            raise
                        except RuntimeError as exc:
                            check(job)
                            result = {'action': operation, 'target': identifier, 'title': chosen['title'], 'app': chosen['process'],
                                      'verified': False, 'state': 'unverified', 'error': str(exc)}
                            description = 'Could not ' + operation.replace('_window', '') + ' ' + chosen['title'] + ': ' + str(exc)
                        window_results[result_key] = result
                        window_targets[result_key] = chosen.copy()
                        last_window_action = result
                        action_result = {'ok': result['verified'], **result}
                        update(job, windowResults=list(window_results.values()))
                        if operation == 'move_window':
                            # The owner asked for it there, so it is not minimized again when the task ends.
                            if chosen['hwnd'] in restored:
                                restored.remove(chosen['hwnd'])
                        else:
                            target = None
                    elif operation in ('focus_window', 'inspect_window'):
                        chosen = windows.get(action.get('target'))
                        current = window_info(chosen['hwnd']) if chosen else None
                        if not controllable(current) or current['pid'] != chosen['pid'] or current['process'] != chosen['process']:
                            raise RuntimeError('That window is no longer available. Ask me to look again.')
                        if operation == 'focus_window':
                            self.point(window_spot(current, 'title'), 'click')
                            bring_to_front(current['hwnd'])
                        elif USER.IsIconic(current['hwnd']):
                            reveal_behind(current['hwnd'])
                            restored.append(current['hwnd'])
                            current = window_info(current['hwnd']) or current
                        foreground_input = operation == 'focus_window'
                        target = current
                        acted_window = current
                    elif operation in CONTROL_ACTIONS:
                        if target is None:
                            raise RuntimeError('Inspect a window before choosing one of its controls.')
                        def check_input():
                            check(job)
                            check_pointer_stop()
                        self.point(control_spot(scene.get('accessibility'), action.get('target')), operation)
                        try:
                            result = accessibility.perform(target, action.get('target'), operation,
                                                           action.get('text', ''), check=check_input, button=action.get('button', 'left'))
                            last_control_action = {'action': operation, 'target': action.get('target'),
                                                   'window': f'window:{target["hwnd"]}', **result}
                        except InputStopped:
                            raise
                        except RuntimeError as exc:
                            check(job)
                            last_control_action = {'action': operation, 'target': action.get('target'),
                                                   'ok': False, 'error': str(exc)}
                            description = 'Control action was not confirmed: ' + str(exc)
                        action_result = last_control_action.copy()
                    elif operation != 'wait':
                        if target is None:
                            raise RuntimeError('Select a window from the current desktop snapshot before sending input')
                        if not foreground_input:
                            # Jarvis's own pointer: input goes straight to the app, the owner keeps pointer and focus.
                            try:
                                if operation in ('type', 'key'):
                                    child = background_fields.get(target['hwnd'])
                                    if child:
                                        self.point(background_spots.get(target['hwnd']), operation)
                                    background_keys(child, action, check=lambda: (check(job), check_pointer_stop()))
                                else:
                                    self.point(background_point(target, frame, action), operation)
                                    child, spot = background_pointer(target, frame, action)
                                    background_fields[target['hwnd']], background_spots[target['hwnd']] = child, spot
                                action_result = {'ok': True, 'status': 'sent_in_background',
                                                 'note': 'Sent to the app without moving the owner\'s pointer. Check the fresh observation to confirm it was handled.'}
                            except InputStopped:
                                raise
                            except RuntimeError as exc:
                                check(job)
                                description = 'Background input was not sent: ' + str(exc)
                                action_result = {'ok': False, 'status': 'input_not_sent', 'error': str(exc)}
                        elif int(USER.GetForegroundWindow() or 0) != target['hwnd']:
                            bring_to_front(target['hwnd'])
                            steps.append('Input not sent: requested focus; inspect the refreshed app before acting.')
                            action_history.append({'action': operation, 'target': action.get('target', ''),
                                                   'result': {'ok': False, 'status': 'input_not_sent',
                                                              'error': 'Focus was reacquired. Inspect the fresh observation before sending input.'}})
                            update(job, steps=steps[:])
                            continue
                        else:
                            if operation in ('click', 'double_click', 'right_click', 'scroll'):
                                try:
                                    self.point(image_point(frame, action.get('x'), action.get('y')), operation)
                                except (RuntimeError, KeyError, TypeError):
                                    pass  # apply_action reports the problem with the point itself
                            apply_action(target, frame, action, job['question'], check=lambda: check(job))
                    if frame.get('paused') and action_result.get('ok') and operation not in ('open_app', 'wait', 'inspect_window', 'focus_window'):
                        action_result['note'] = ('The app is paused while covered, so the next screenshot will not show this change. '
                                                 'Check the window title and control tree; the tree can lag a few seconds, so use '
                                                 'wait rather than repeating the action if the change is not there yet.')
                    steps.append(description or operation)
                    update(job, steps=steps[:])
                    for _ in range(5 if operation in ('open_app', 'wait') else 3):
                        check(job)
                        time.sleep(.2)
                    if acted_window:
                        action_result['windowState'] = observed_window_state(acted_window)
                    action_history.append({'action': operation, 'target': action.get('target', ''),
                                           'window': {'id': f'window:{acted_window["hwnd"]}', 'title': acted_window['title'][:160],
                                                      'process': acted_window['process']} if acted_window else None,
                                           'result': action_result})
                    update(job, windowResults=list(window_results.values()))
                raise RuntimeError('The PC task reached its step limit. Check the activity window before continuing.')
            finally:
                self.hide_pointer()
                for hwnd in restored:
                    if USER.IsWindow(hwnd) and not USER.IsIconic(hwnd) and int(USER.GetForegroundWindow() or 0) != hwnd:
                        USER.ShowWindow(hwnd, 7)
                if previous_dpi:
                    USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous_dpi))
