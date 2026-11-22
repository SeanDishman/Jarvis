"""A one-visit lock in pass covers one browser tab, checked on a real browser.

An isolated Chrome profile opens behind the owner's windows. The real tab reader decides, for the real tab in front,
whether the pass covers it: the tab the pass began in stays allowed, a tab opened afterwards is not, closing that tab
brings the allowed one back, and a tab opened later still is refused. Tabs are opened and closed with background
clicks on the browser's own tab strip, so nothing takes the owner's focus.

    python tests/verification/verify-lock-in-pass-tab.py [--browser PATH]
"""
import argparse
import ctypes
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop import computer
from desktop.accessibility import Accessibility
from desktop.focus_guard import AttemptLog, FocusGuard, SessionLog, TabCensus

BROWSERS = [r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']


def wait(predicate, seconds, message):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.2)
    raise AssertionError(message)


def verify(browser):
    import comtypes
    import psutil
    comtypes.CoInitializeEx()
    folder = Path(tempfile.mkdtemp(prefix='lock-in-pass-', dir=ROOT / '.cache'))
    page = folder / 'page.html'
    page.write_text('<title>Pass check page</title><h1>Pass check</h1>', encoding='utf-8')
    profile = folder / 'profile'
    process = subprocess.Popen([browser, '--user-data-dir=' + str(profile), '--no-first-run', '--no-default-browser-check',
                                '--disable-default-apps', '--window-size=900,600', '--window-position=200,200', page.as_uri()],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owners, report = [], {}
    try:
        def find():
            nonlocal owners
            owners = [p for p in psutil.process_iter(['pid', 'cmdline']) if '--user-data-dir=' + str(profile) in (p.info['cmdline'] or [])]
            return next((row for row in computer.visible_windows() if row['pid'] in {p.pid for p in owners}
                         and row['title'].startswith('Pass check page')), None)
        window = wait(find, 25, 'The isolated browser did not open')
        surface = Accessibility()

        def tabs():
            return [row for row in surface.inspect(computer.window_info(window['hwnd']))['controls'] if row['role'] == 'TabItemControl']

        def press(name, button='left', role='ButtonControl'):
            # The browser's own tab strip, clicked with window messages: no focus change, no real input.
            controls = surface.inspect(computer.window_info(window['hwnd']))['controls']
            row = next(row for row in controls if row['role'] == role and row['name'].startswith(name))
            surface.perform(computer.window_info(window['hwnd']), row['id'], 'click_control', button=button)

        census = TabCensus()
        with patch('desktop.focus_guard.diagnostic'):
            guard = FocusGuard(SimpleNamespace(), census=census, attempts=AttemptLog(folder / 'attempts.json'),
                               session_log=SessionLog(folder / 'sessions.json'))
            guard.grant_pass('x')
            first = census.selected(window)
            assert first, 'The tab in front could not be identified'
            decisions = {'first visit': guard.allowed('x', time.monotonic(), window)}
            press('New Tab')
            wait(lambda: len(tabs()) == 2, 10, 'The new tab did not open')
            second = census.selected(window)
            decisions['new tab'] = guard.allowed('x', time.monotonic(), window)
            press('New Tab', button='middle', role='TabItemControl')
            wait(lambda: len(tabs()) == 1, 10, 'The new tab did not close')
            decisions['back to the first tab'] = guard.allowed('x', time.monotonic(), window)
            again = census.selected(window)
            press('New Tab')
            wait(lambda: len(tabs()) == 2, 10, 'The later tab did not open')
            later = census.selected(window)
            decisions['a later new tab'] = guard.allowed('x', time.monotonic(), window)
        report = {'decisions': decisions,
                  'identities': {'first': first, 'new': second, 'first again': again, 'later': later},
                  'foreground_is_browser': int(computer.USER.GetForegroundWindow() or 0) == window['hwnd']}
        print(json.dumps(report, indent=1))
        assert decisions == {'first visit': True, 'new tab': False, 'back to the first tab': True, 'a later new tab': False}, decisions
        assert first == again and len({first, second, later}) == 3, report['identities']
        print('PASS: the pass covered only the tab it began in; new tabs were refused and the allowed tab kept its identity.')
    finally:
        for owner in owners:
            try:
                owner.terminate()
            except psutil.NoSuchProcess:
                pass
        process.terminate()
        psutil.wait_procs(owners, timeout=5)
        import shutil
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', default=next((path for path in BROWSERS if Path(path).exists()), None))
    args = parser.parse_args()
    assert args.browser, 'Pass --browser with a Chromium browser'
    computer.USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    verify(args.browser)
