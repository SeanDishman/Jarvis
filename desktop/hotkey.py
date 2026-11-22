from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import threading

from desktop.windows import desktop_name

VK_INSERT = 0x2D
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x100, 0x101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x104, 0x105
WM_QUIT = 0x12
KEY_DOWN = (WM_KEYDOWN, WM_SYSKEYDOWN)
KEY_UP = (WM_KEYUP, WM_SYSKEYUP)
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class KeyboardEvent(ctypes.Structure):
    _fields_ = [('vkCode', wt.DWORD), ('scanCode', wt.DWORD),
                ('flags', wt.DWORD), ('time', wt.DWORD),
                ('dwExtraInfo', ctypes.c_size_t)]


class InsertHotkey:
    def __init__(self, events):
        self.events = events
        self.active = False
        self.error = None
        self.desktop = None
        self.thread = None
        self.ready = threading.Event()
        self._stopped = threading.Event()
        self._pressed = False
        self._thread_id = 0
        self._hook = None
        self._callback = None
        self._user = None

    def status(self):
        return {'key': 'Insert', 'installed': self.active,
                'availableOnUserDesktop': self.active and str(self.desktop).lower() == 'default',
                'exclusive': self.active, 'error': self.error}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.ready.clear()
        self._stopped.clear()
        self._pressed = False
        self.error = None
        self.thread = threading.Thread(target=self._run, name='Jarvis Insert key', daemon=True)
        self.thread.start()

    def stop(self):
        self._stopped.set()
        if self._thread_id and self._user:
            self._user.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)

    def _handle(self, code, message, pointer):
        if code >= 0 and message in KEY_DOWN + KEY_UP and pointer and not self._stopped.is_set():
            event = ctypes.cast(pointer, ctypes.POINTER(KeyboardEvent)).contents
            if event.vkCode == VK_INSERT:
                if message in KEY_UP:
                    self._pressed = False
                elif not self._pressed:
                    self._pressed = True
                    self.events.put_nowait(('toggle_jarvis',))
                return 1
        return self._user.CallNextHookEx(self._hook, code, message, pointer)

    def _run(self):
        try:
            self.desktop = desktop_name()
            self._user = user = ctypes.WinDLL('user32', use_last_error=True)
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            user.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
            user.SetWindowsHookExW.restype = wt.HANDLE
            user.CallNextHookEx.argtypes = [wt.HANDLE, ctypes.c_int, wt.WPARAM, wt.LPARAM]
            user.CallNextHookEx.restype = LRESULT
            user.UnhookWindowsHookEx.argtypes = [wt.HANDLE]
            user.UnhookWindowsHookEx.restype = wt.BOOL
            user.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
            user.PeekMessageW.restype = wt.BOOL
            user.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
            user.GetMessageW.restype = ctypes.c_int
            user.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
            user.TranslateMessage.restype = wt.BOOL
            user.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
            user.DispatchMessageW.restype = LRESULT
            user.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
            user.PostThreadMessageW.restype = wt.BOOL
            kernel.GetCurrentThreadId.restype = wt.DWORD
            kernel.GetModuleHandleW.argtypes = [wt.LPCWSTR]
            kernel.GetModuleHandleW.restype = wt.HMODULE

            message = wt.MSG()
            user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self._thread_id = kernel.GetCurrentThreadId()
            self._callback = HOOKPROC(self._handle)
            self._hook = user.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback, kernel.GetModuleHandleW(None), 0)
            if not self._hook:
                raise ctypes.WinError(ctypes.get_last_error())
            self.active = True
            self.ready.set()
            self.events.put_nowait(('hotkey_status', self.status()))
            while not self._stopped.is_set():
                result = user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self.error = f'Insert shortcut is unavailable. Windows reported {type(error).__name__}.'
        finally:
            if self._hook:
                self._user.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._callback = None
            self._thread_id = 0
            self._pressed = False
            self.active = False
            self.ready.set()
            self.events.put_nowait(('hotkey_status', self.status()))
