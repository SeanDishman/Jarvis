import ctypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from ui_harness import Interface, wait
from desktop.common import ROOT
from desktop.computer import USER, Rect, bring_to_front, visible_windows, window_info
from desktop.diagnostics import LOG_PATH
from desktop.focus_guard import VK_CONTROL, VK_ESCAPE, VK_RETURN, candidate, chord, find_address_field, read_field, type_text
from desktop.preferences import Preferences

BROWSERS = {'chrome': Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Google/Chrome/Application/chrome.exe',
            'opera': Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Opera GX/opera.exe'}


def physical_rect(handle):
    rect = Rect()
    assert USER.GetWindowRect(handle, ctypes.byref(rect))
    return [rect.left, rect.top, rect.right, rect.bottom]


def center_inside(inner, outer):
    x, y = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= x < outer[2] and outer[1] <= y < outer[3]


class TestBrowser:
    def __init__(self, kind, folder):
        self.folder = str(folder)
        self.process = subprocess.Popen([str(BROWSERS[kind]), f'--user-data-dir={folder}', '--no-first-run', '--no-default-browser-check',
                                         '--disable-search-engine-choice-screen', '--disable-sync', '--window-position=120,90',
                                         '--window-size=1180,820', '--new-window', 'about:blank'])
        self.window = wait(self.find, 30, 'The test browser did not open its own profile window, so nothing was typed')
        self.pid, self.process_name = self.window['pid'], self.window['process']
        self.field = wait(lambda: find_address_field(self.current() or self.window), 30, 'The test browser address bar never appeared')

    def owned(self):
        import psutil
        rows = []
        for process in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                command = process.info['cmdline'] or []
            except (psutil.Error, OSError):
                continue
            if any(argument == f'--user-data-dir={self.folder}' for argument in command):
                rows.append((process.info['pid'], any(argument.startswith('--type=') for argument in command)))
        return rows

    def find(self):
        browsers = {pid for pid, helper in self.owned() if not helper}
        return next((row for row in visible_windows() if row['process'] in ('chrome.exe', 'opera.exe') and row['pid'] in browsers), None)

    def current(self):
        return window_info(self.window['hwnd'])

    def address(self):
        return read_field(self.field)

    def front(self):
        assert self.window['pid'] == self.pid, 'Refusing to type into a browser this check did not start'
        bring_to_front(self.window['hwnd'])
        wait(lambda: int(USER.GetForegroundWindow() or 0) == self.window['hwnd'], 5, 'The test browser did not come to the front')

    def user_types(self, text, enter=True, new_tab=True):
        self.front()
        if new_tab:
            chord(VK_CONTROL, ord('T'))
            time.sleep(.6)
        assert int(USER.GetForegroundWindow() or 0) == self.window['hwnd'], 'Focus moved away from the test browser'
        chord(VK_CONTROL, ord('L'))
        wait(lambda: self.address()[1], 5, 'The address bar did not take focus')
        type_text(text)
        if enter:
            chord(VK_RETURN)

    def describe(self, target):
        import base64
        from desktop.computer import capture_window
        current = self.current()
        state = {'title': current and current['title'], 'foreground_is_test_browser': int(USER.GetForegroundWindow() or 0) == self.window['hwnd']}
        try:
            state['address'], state['address_focused'] = self.address()
            target.write_bytes(base64.b64decode(capture_window(current)['image']))
            state['screenshot'] = str(target)
        except Exception as exc:
            state['error'] = type(exc).__name__
        return state

    def close(self):
        import psutil
        for pid, _helper in self.owned():
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        self.process.kill()
        self.process.wait(10)


def verify_setting(ui, controller):
    ui.ui('open', {'window': 'settings', 'view': 'focus', 'focus': False})
    page = ui.page('settings')
    toggle = 'button.toggle[aria-label="Lock in mode"]'
    page.wait_for_selector(toggle, timeout=10000)
    labels = page.evaluate("[...document.querySelectorAll('.nav-item .label')].map((node) => node.textContent)")
    assert 'Lock in' in labels, labels
    assert page.get_attribute(toggle, 'aria-checked') == 'true', 'Lock in mode should start on'
    page.click(toggle)
    wait(lambda: controller.preferences.read()['lockInMode'] is False, 5, 'Turning the switch off was not saved')
    assert page.get_attribute(toggle, 'aria-checked') == 'false'
    page.click(toggle)
    wait(lambda: controller.preferences.read()['lockInMode'] is True, 5, 'Turning the switch on was not saved')
    text = page.evaluate("document.querySelector('.content').innerText")
    assert 'x.com' in text and 'instagram.com' in text and 'tells you to lock in' in text, text
    return {'section': 'Lock in', 'default_on': True, 'switch_saves_off_and_on': True}


def verify(kind='chrome'):
    import comtypes
    USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    comtypes.CoInitializeEx()
    assert BROWSERS[kind].is_file(), f'{kind} is required for this check'
    report = {'browser': kind}
    with tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='lock-in-check-', ignore_cleanup_errors=True) as folder, Interface() as ui:
        controller = ui.orb.controller
        controller.preferences = Preferences(Path(folder) / 'settings.json')
        spoken, visits, finished = [], [], []
        controller.speak = spoken.append
        visit, done = ui.orb.on_lock_in, ui.orb.on_lock_in_finished
        ui.orb.on_lock_in = lambda row: (visits.append(row), visit(row))
        ui.orb.on_lock_in_finished = lambda number: (finished.append(number), done(number))
        home = ui.windows()['orb']['bounds']
        report['settings'] = verify_setting(ui, controller)
        browser = TestBrowser(kind, Path(folder) / 'browser-profile')
        try:
            controller.focus_guard.start()
            browser.user_types('x.com')
            wait(lambda: visits, 20, 'Lock in did not block x.com')
            opened = time.monotonic()
            # He glides over from his corner, so give the flight a moment to land.
            orb_handle = ui.windows()['orb']['handle']

            def landed():
                rect = physical_rect(orb_handle)
                return rect if center_inside(rect, visits[0]['rect']) else None

            orb_rect = wait(landed, 3, 'The orb did not land over the browser')
            card = wait(lambda: ui.page('companion').evaluate("document.querySelector('.lock-in-card')?.innerText ?? ''"),
                        3, 'The lock-in card did not appear')
            assert visits[0]['text'] in card and 'X is blocked' in card and f"Attempt {visits[0]['attempt']}" in card, card
            wait(lambda: browser.current() and not candidate(browser.current()['title']), 5, 'The X tab is still showing')
            assert 'x.com' not in browser.address()[0]
            wait(lambda: spoken == [visits[0]['text']], 30, 'Jarvis did not say the lock in line')
            assert controller.speech_cache[spoken[0]][:4] == b'RIFF', 'The lock in voice was not prepared with Piper'
            wait(lambda: finished, 10, 'The lock in visit did not finish')
            wait(lambda: ui.windows()['orb']['bounds'] == home, 5, 'The orb did not fly back home')
            report['x'] = {'tab_closed': True, 'orb_over_browser': True, 'orb_rect': orb_rect, 'browser_rect': visits[0]['rect'],
                           'card': card.splitlines(), 'spoken': spoken[:], 'orb_returned_seconds': round(time.monotonic() - opened, 1)}

            browser.user_types('x.com', enter=False)
            time.sleep(2.5)
            assert len(visits) == 1, 'Typing x.com without opening it must not be blocked'
            chord(VK_ESCAPE)
            chord(VK_ESCAPE)
            report['typing_only'] = {'blocked': False}

            browser.user_types('instagram.com')
            wait(lambda: len(visits) == 2, 20, 'Lock in did not block instagram.com')
            assert visits[1]['site'] == 'instagram'
            wait(lambda: browser.current() and 'instagram' not in browser.address()[0], 5, 'Instagram is still showing')
            report['instagram'] = {'blocked': True}

            browser.user_types('x.com')
            wait(lambda: len(visits) == 3, 20, 'Reopening X right away was not blocked')
            wait(lambda: browser.address()[0] == 'about:blank', 5, 'The reopened X tab was not sent to a blank page')
            wait(lambda: len(finished) >= 2 and ui.windows()['orb']['bounds'] == home, 12, 'The orb did not return after the repeat block')
            report['repeat_within_three_seconds'] = {'blocked': True, 'tab_sent_to': 'about:blank'}

            controller.preferences.save({'lockInMode': False})
            browser.user_types('x.com')
            time.sleep(4)
            assert len(visits) == 3 and 'x.com' in browser.address()[0], 'Lock in mode off must leave X alone'
            controller.preferences.save({'lockInMode': True})
            browser.front()
            wait(lambda: len(visits) == 4, 10, 'Turning lock in back on did not block the open X tab')
            wait(lambda: browser.current() and 'x.com' not in browser.address()[0], 5, 'X is still showing after lock in came back on')
            report['setting'] = {'off_allows_x': True, 'on_blocks_open_tab': True}
            assert len(spoken) >= 3 and all(line and 'You need to lock in' not in line for line in spoken), spoken
            assert [row['attempt'] for row in visits] == list(range(visits[0]['attempt'], visits[0]['attempt'] + 4))
            assert len({row['text'] for row in visits}) >= 2, [row['text'] for row in visits]
            report['spoken_lines'] = len(spoken)
            report['attempts'] = [row['attempt'] for row in visits]
        except Exception:
            print(json.dumps({'failure_state': browser.describe(ROOT / '.cache' / f'lock-in-failure-{kind}.png'), 'report': report}, default=str), flush=True)
            raise
        finally:
            controller.closed.set()
            browser.close()
    rows = [json.loads(line) for line in LOG_PATH.read_text(encoding='utf-8').splitlines()[-600:]]
    rows = [row for row in rows if row.get('pid') == os.getpid() and row['event'] == 'lock_in_blocked']
    assert len(rows) == 4, rows
    assert [row['action'] for row in rows] == ['close_tab', 'close_tab', 'leave_page', 'close_tab'], rows
    assert [row['attempts'] for row in rows] == report['attempts'], rows
    assert not any(term in json.dumps(rows) for term in ('x.com', 'instagram.com', 'http')), rows
    report['diagnostics'] = [{key: row.get(key) for key in ('event', 'domain', 'action', 'attempts')} for row in rows]
    print(json.dumps(report, indent=1), flush=True)


if __name__ == '__main__':
    import sys
    verify(sys.argv[1] if len(sys.argv) > 1 else 'chrome')
