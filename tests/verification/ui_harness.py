from __future__ import annotations

import contextlib
import json
import pathlib
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop.breaks import BreakState
from desktop.computer import USER
from desktop.orb import Orb


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def wait(predicate, timeout=8.0, message='Condition was not met', interval=.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


class Interface:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.stack = contextlib.ExitStack()
        self.port = free_port()

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def start(self):
        from playwright.sync_api import sync_playwright
        self.temp = pathlib.Path(self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='ui-check-')))
        self.breaks = BreakState(self.temp / 'break.json')
        self.stack.enter_context(patch.object(Orb, 'start_services'))
        self.stack.enter_context(patch('desktop.controller.BreakState', return_value=self.breaks))
        self.stack.enter_context(patch.object(Orb, 'save_settings'))
        environment = {'JARVIS_UI_DEBUG_PORT': str(self.port), 'JARVIS_UI_PROFILE': str(self.temp / 'profile')}
        self.orb = Orb(ui_environment=environment)
        self.orb.settings.update(self.settings)
        self.orb.controller.microphone_ready = True
        self.orb.controller.voice_ready = True
        self.orb.controller.update('ready', 'Say Jarvis')
        self.loop = threading.Thread(target=self.orb.run, name='jarvis-host', daemon=True)
        self.loop.start()
        wait(lambda: self.orb.controller.interface.get('running'), 40, 'The Electron interface did not connect')
        self.playwright = self.stack.enter_context(sync_playwright())
        self.browser = wait(self._connect, 20, 'Could not attach to the interface for inspection')
        self.anchor = None
        self.anchor = self.page('orb')
        return self

    def pause(self, seconds=.05):
        anchor = getattr(self, 'anchor', None)
        if anchor is not None and not anchor.is_closed():
            anchor.wait_for_timeout(seconds * 1000)
        else:
            time.sleep(seconds)

    def wait(self, predicate, timeout=8.0, message='Condition was not met'):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            self.pause(.05)
        raise AssertionError(message)

    def _connect(self):
        try:
            return self.playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{self.port}')
        except Exception:
            return None

    def pages(self):
        return [page for context in self.browser.contexts for page in context.pages]

    def page(self, view, timeout=15):
        def find():
            for page in self.pages():
                if f'view={view}' in page.url and not page.is_closed():
                    try:
                        if page.evaluate('document.documentElement.dataset.ready') == 'true':
                            return page
                    except Exception:
                        return None
            return None
        return self.wait(find, timeout, f'The {view} window did not finish loading')

    def evaluate(self, script, argument=None):
        return self.page('orb').evaluate(script, argument)

    def ui(self, action, params=None):
        reply = self.evaluate('([action, params]) => window.jarvis.ui(action, params)', [action, params or {}])
        if not reply.get('ok'):
            raise AssertionError(reply.get('error'))
        return reply.get('result')

    def call(self, method, params=None, view='orb'):
        reply = self.page(view).evaluate('([method, params]) => window.jarvis.call(method, params)', [method, params or {}])
        if not reply.get('ok'):
            raise AssertionError(reply.get('error'))
        return reply.get('result')

    def windows(self):
        return self.ui('inspect')['windows']

    def state(self, kind):
        row = self.window(kind)
        if not row:
            return None
        handle = row['handle']
        if not USER.IsWindow(handle):
            return None
        if USER.IsIconic(handle):
            return 'iconic'
        return 'normal' if USER.IsWindowVisible(handle) else 'withdrawn'

    def handle(self, kind):
        row = self.window(kind)
        return row['handle'] if row else None

    def focus_target(self, title='Jarvis interface check'):
        import tkinter as tk
        root = tk.Tk()
        root.title(title)
        root.geometry('360x120+60+60')
        root.update()
        root.focus_force()
        root.update()
        self.pause(.3)
        root.update()
        return root, USER.GetForegroundWindow()

    def window(self, kind):
        return self.windows().get(kind)

    def emit(self, *event):
        self.orb.events.put(event)

    def __exit__(self, *exc):
        orb = getattr(self, 'orb', None)
        try:
            if orb is not None:
                orb.events.put(('quit',))
                loop = getattr(self, 'loop', None)
                if loop is not None:
                    loop.join(10)
                if orb.ui:
                    orb.ui.stop()
        finally:
            browser = getattr(self, 'browser', None)
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if orb is not None:
                orb.controller.research.close()
                orb.controller.tasks.close()
                orb.controller.browser.close()
            time.sleep(.5)
            self.stack.close()
        return False


def dump(value):
    return json.dumps(value, indent=1, default=str)
