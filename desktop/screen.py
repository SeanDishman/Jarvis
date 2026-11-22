import ctypes
import time

from desktop.computer import USER, capture_rect, capture_window, window_info
from desktop.windows import desktop_name, monitors
from desktop.displays import display_bounds, display_inventory, select_display


def capture_app(hwnd):
    if USER.IsIconic(hwnd):
        USER.ShowWindow(hwnd, 4)
        time.sleep(.4)
    window = window_info(hwnd)
    if not window or USER.IsIconic(hwnd):
        raise RuntimeError('That app window closed before I could look at it, sir.')
    return {**capture_window(window), 'display': window['title']}


def capture_screen(display='current', window=None):
    if desktop_name().lower() != 'default':
        raise RuntimeError('Screen viewing needs your normal Windows desktop, sir. Open start-jarvis.cmd there, then ask me again.')
    previous_dpi = USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        if window and display == 'current':
            return capture_app(window['hwnd'])
        screens = monitors()
        foreground = window_info(USER.GetForegroundWindow())
        selected = select_display(display, screens, foreground['rect'] if foreground else None)
        rect = selected['rect'] if selected else display_bounds(screens)
        label = selected['device'] if selected else 'All displays'
        return {**capture_rect(rect), 'display': label, 'monitors': display_inventory(screens)}
    finally:
        if previous_dpi:
            USER.SetThreadDpiAwarenessContext(previous_dpi)
