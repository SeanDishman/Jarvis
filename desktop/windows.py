from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import json
import os
import pathlib
import re
import subprocess
import sys
import winreg

from desktop.app_identity import close_names, role_app
from desktop.common import ROOT

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'JarvisDesktop'


def startup_command():
    pythonw = pathlib.Path(sys.executable).with_name('pythonw.exe')
    return subprocess.list2cmdline([str(pythonw), str(ROOT / 'scripts/start-jarvis.py'), '--desktop'])


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0] == startup_command()
    except OSError:
        return False


def set_startup(enabled=True):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
    return startup_enabled()


def normalized(text):
    return re.sub(r'[^a-z0-9+]+', ' ', text.lower()).strip()


class AppCatalog:
    def __init__(self):
        self.apps = []

    def refresh(self):
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            '[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress'],
            capture_output=True, encoding='utf-8-sig', timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('Windows could not list installed apps')
        data = json.loads(result.stdout)
        self.apps = data if isinstance(data, list) else [data]
        return self.apps

    def match(self, name):
        query = normalized(name)
        if not query or len(query) > 100:
            return []
        exact = [app for app in self.apps if normalized(app['Name']) == query]
        if exact:
            return exact
        default = role_app(self.apps, name)
        if default:
            return [default]
        partial = [app for app in self.apps if query in normalized(app['Name'])]
        return partial or close_names(self.apps, name)

    def launch(self, name):
        if not self.apps:
            self.refresh()
        matches = self.match(name)
        if len(matches) != 1:
            return {'ok': False, 'error': 'Choose an installed app' if matches else 'App not found',
                    'choices': [app['Name'] for app in matches[:8]]}
        app = matches[0]
        os.startfile('shell:AppsFolder\\' + app['AppID'])
        return {'ok': True, 'app': app['Name'], 'status': 'launch_requested'}


def desktop_name():
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetThreadDesktop.argtypes = [wt.DWORD]
    user.GetThreadDesktop.restype = wt.HANDLE
    user.GetUserObjectInformationW.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    handle = user.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
    buffer = ctypes.create_unicode_buffer(256)
    needed = wt.DWORD()
    if user.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
        return buffer.value
    return 'unknown'


class Rect(ctypes.Structure):
    _fields_ = [('left', wt.LONG), ('top', wt.LONG), ('right', wt.LONG), ('bottom', wt.LONG)]


class MonitorInfo(ctypes.Structure):
    _fields_ = [('size', wt.DWORD), ('rect', Rect), ('work', Rect), ('flags', wt.DWORD), ('device', wt.WCHAR * 32)]


def internal_display_names():
    user = ctypes.windll.user32
    paths, modes = wt.UINT(), wt.UINT()
    if user.GetDisplayConfigBufferSizes(2, ctypes.byref(paths), ctypes.byref(modes)):
        return set()
    path_data = (ctypes.c_byte * (72 * paths.value))()
    mode_data = (ctypes.c_byte * (64 * modes.value))()
    if user.QueryDisplayConfig(2, ctypes.byref(paths), path_data, ctypes.byref(modes), mode_data, None):
        return set()
    names = set()
    raw = bytes(path_data)
    for i in range(paths.value):
        item = raw[i * 72:(i + 1) * 72]
        technology = int.from_bytes(item[36:40], 'little')
        if technology not in (6, 11, 0x80000000):
            continue
        request = ctypes.create_string_buffer(84)
        header = (1).to_bytes(4, 'little') + (84).to_bytes(4, 'little') + item[:12]
        ctypes.memmove(request, header, len(header))
        if user.DisplayConfigGetDeviceInfo(request) == 0:
            names.add(bytes(request)[20:84].decode('utf-16-le').rstrip('\0'))
    return names


def monitors():
    screens = []
    internal = internal_display_names()
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(Rect), wt.LPARAM)
    def visit(handle, _dc, _rect, _data):
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info))
        screens.append({'device': info.device, 'primary': bool(info.flags & 1),
                        'internal': info.device in internal,
                        'rect': [info.rect.left, info.rect.top, info.rect.right, info.rect.bottom],
                        'work': [info.work.left, info.work.top, info.work.right, info.work.bottom]})
        return True
    callback = callback_type(visit)
    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback, 0)
    return screens


def preferred_monitor(screens, selected=None):
    return (next((s for s in screens if s['device'] == selected), None)
            or next((s for s in screens if s['internal']), None)
            or next((s for s in screens if s['primary']), None) or screens[0])
