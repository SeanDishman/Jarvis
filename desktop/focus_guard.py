from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from desktop.common import ROOT
from desktop.computer import (OWN_PROCESSES, USER, Input, InputUnion, KeyboardInput, Point, send_inputs, visible_windows,
                              window_info)
from desktop.diagnostics import event as diagnostic
from desktop.learning_reminders import learning_reminder, write_json
from desktop.lock_in_lines import acceptable, clean_line, fallback_line, lock_in_line
from desktop.timeline import ago, local_time

MESSAGE = 'You cannot be here. You need to lock in. You cannot be here right now. You need to lock in.'
SITES = {'x': ('x.com', 'twitter.com'), 'instagram': ('instagram.com',), 'doordash': ('doordash.com',)}
NAMES = {'x': 'X', 'instagram': 'Instagram', 'doordash': 'DoorDash'}
# Sites that get a second chance: the first visit is closed, and going straight back within a few minutes means
# the owner has decided. Then the page stays open and lock in leaves that site alone for a while.
SECOND_CHANCE = {'doordash': {'repeat_seconds': 30 * 60.0, 'cooldown_seconds': 20 * 60.0}}
# The owner's one-visit passes are for the feeds; a second-chance site has its own way through.
PASS_SITES = tuple(site for site in SITES if site not in SECOND_CHANCE)
LEARNING_NAMES = {'linkedin': 'LinkedIn', 'youtube': 'YouTube'}
WATCHED_SITES = {**SITES, 'linkedin': ('linkedin.com',), 'youtube': ('youtube.com', 'youtu.be')}
TITLES = {'x': re.compile(r'(?:^|/ )x(?: [-|–—]|$)|\bx\.com\b|\btwitter\b', re.I),
          'instagram': re.compile(r'instagram', re.I), 'doordash': re.compile(r'\bdoor\s?dash\b', re.I),
          'linkedin': re.compile(r'\blinkedin\b', re.I), 'youtube': re.compile(r'\byoutube\b|\byoutu\.be\b', re.I)}
CHROMIUM = frozenset({'opera.exe', 'chrome.exe', 'msedge.exe', 'brave.exe', 'vivaldi.exe', 'chromium.exe', 'thorium.exe'})
BROWSERS = CHROMIUM | {'firefox.exe', 'librewolf.exe', 'waterfox.exe', 'floorp.exe', 'zen.exe'}
ADDRESS_CLASSES = frozenset({'AddressTextfieldView', 'OmniboxViewViews'})
ADDRESS_IDS = frozenset({'urlbar-input'})
ADDRESS_NAMES = frozenset({'address field', 'address and search bar', 'search or enter address',
                           'search with google or enter address'})
UIA_EDIT, UIA_DOCUMENT, UIA_VALUE_PATTERN = 50004, 50030, 10002
OBJID_CLIENT, ROLE_DOCUMENT, ROLE_PAGE_TAB, ROLE_PAGE_TAB_LIST = 0xFFFFFFFC, 0x0F, 0x25, 0x3C
STATE_SYSTEM_SELECTED = 0x2
STRIP_NODES = 800
POLL_SECONDS = .3
MISS_SECONDS = 2.0
SETTLE_SECONDS = .8
RETRY_SECONDS = 3.0
SILENT_VISIT_SECONDS = 4.0
LINGER_SECONDS = 1.5
PASS_WAIT_SECONDS = 600.0
PASS_AWAY_SECONDS = 180.0
ATTEMPT_HISTORY = 500
# A learning site earns one reminder per session. The session lasts while any tab of the site stays open,
# whichever window or tab is in front, and ends once every tab of it has been closed for a while, or once
# a tab left open has gone unused for hours.
LEARNING_CLOSED_SECONDS = 15 * 60.0
LEARNING_IDLE_SECONDS = 4 * 3600.0
LEARNING_SCAN_SECONDS = 20.0
LEARNING_DELIVERY_SECONDS = 120.0
LEARNING_RETRY_SECONDS = 30.0
LEARNING_SAVE_SECONDS = 60.0
STRIP_RETRY_SECONDS = 120.0
STRIP_LEARN_SECONDS = 10.0
BROWSER_SUFFIX = re.compile(r'\s+[-–—]\s+(?:opera(?: gx)?|google chrome|mozilla firefox|microsoft\W*edge|brave|vivaldi|'
                            r'chromium|thorium|librewolf|waterfox|floorp|zen browser)\s*$', re.I)
SITE_SUFFIX = {'youtube': re.compile(r'\s+[-–—|]\s+youtube\s*$', re.I),
               'linkedin': re.compile(r'\s+[-–—|]\s+linkedin\s*$', re.I)}
VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN, VK_RETURN, VK_ESCAPE = 0x11, 0x10, 0x12, 0x5B, 0x5C, 0x0D, 0x1B
USER.GetAsyncKeyState.argtypes = [ctypes.c_int]
USER.GetAsyncKeyState.restype = ctypes.c_short


def watched_site(address):
    text = str(address or '').strip()
    if not text or len(text) > 4096 or any(character.isspace() for character in text):
        return None
    try:
        parts = urlsplit(text if '://' in text else 'https://' + text)
        host = (parts.hostname or '').rstrip('.')
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https'):
        return None
    return next((site for site, domains in WATCHED_SITES.items()
                 if any(host == domain or host.endswith('.' + domain) for domain in domains)), None)


def blocked_site(address):
    site = watched_site(address)
    return site if site in SITES else None


def title_agrees(site, title):
    return bool(TITLES[site].search(' '.join(str(title or '').split())))


def candidate(title):
    return any(title_agrees(site, title) for site in TITLES)


def foreground_browser():
    window = window_info(USER.GetForegroundWindow())
    return window if window and window['process'] in BROWSERS and window['pid'] not in OWN_PROCESSES else None


def browser_windows():
    return [window for window in visible_windows() if window['process'] in BROWSERS and window['pid'] not in OWN_PROCESSES]


def page_title(site, title):
    """What the tab shows, such as a video's name, without the site and browser names. Empty for home pages."""
    text = ' '.join(str(title or '').split())
    for _ in range(2):
        text = BROWSER_SUFFIX.sub('', text)
    match = SITE_SUFFIX[site].search(text)
    if not match:
        return ''
    return re.sub(r'^\(\d+\+?\)\s*', '', text[:match.start()]).strip()[:160]


class Automation:
    """A UI Automation client for the calling thread only. Elements it returns must stay on that thread."""

    def __init__(self):
        import comtypes.client
        self.module = comtypes.client.GetModule('UIAutomationCore.dll')
        self.client = comtypes.client.CreateObject('{ff48dba4-60ef-4201-aa87-54103eef594e}', interface=self.module.IUIAutomation)
        self.walker = self.client.RawViewWalker

    def children(self, element):
        rows, child = [], self.walker.GetFirstChildElement(element)
        while child and len(rows) < 400:
            rows.append(child)
            child = self.walker.GetNextSiblingElement(child)
        return rows

    @staticmethod
    def matches(element, pid):
        try:
            return bool(element) and element.CurrentControlType == UIA_EDIT and element.CurrentProcessId == pid and (
                element.CurrentClassName in ADDRESS_CLASSES or element.CurrentAutomationId in ADDRESS_IDS
                or (element.CurrentName or '').casefold() in ADDRESS_NAMES)
        except Exception:
            return False

    def focused_field(self, window):
        element = self.client.GetFocusedElement()
        return element if self.matches(element, window['pid']) else None

    def field_near_top(self, window):
        # Opera GX can refuse to list the children of its window decoration once a page loads,
        # so also look straight at the toolbar band, top down, stopping before page content.
        left, top, right, bottom = window['rect']
        scale = max(1.0, (USER.GetDpiForWindow(window['hwnd']) or 96) / 96)
        for offset in range(round(24 * scale), min(bottom - top, round(150 * scale)), max(1, round(8 * scale))):
            for share in (.35, .5, .25, .65):
                x, y = round(left + (right - left) * share), top + offset
                if int(USER.GetAncestor(USER.WindowFromPoint(Point(x, y)), 2) or 0) != window['hwnd']:
                    continue
                element = self.client.ElementFromPoint(self.module.tagPOINT(x, y))
                if self.matches(element, window['pid']):
                    return element
        return None

    def walk(self, window, budget=2500, seconds=3.0):
        root = self.client.ElementFromHandle(window['hwnd'])
        deadline = time.monotonic() + seconds
        stack, visited = [(root, 0)], 0
        while stack and visited < budget and time.monotonic() < deadline:
            element, depth = stack.pop()
            visited += 1
            try:
                kind = element.CurrentControlType
                if kind == UIA_EDIT and (element.CurrentClassName in ADDRESS_CLASSES or element.CurrentAutomationId in ADDRESS_IDS
                                         or (element.CurrentName or '').casefold() in ADDRESS_NAMES):
                    return element
                if depth >= 24 or (kind == UIA_DOCUMENT and window['process'] in CHROMIUM):
                    continue
                children = self.children(element)
            except Exception:
                continue
            stack.extend((child, depth + 1) for child in reversed(children))
        return None

    def find_address_field(self, window):
        for strategy in (self.focused_field, self.field_near_top, self.walk):
            try:
                element = strategy(window)
            except Exception:
                element = None
            if element is not None:
                return element
        return None

    def read(self, field):
        pattern = field.GetCurrentPattern(UIA_VALUE_PATTERN).QueryInterface(self.module.IUIAutomationValuePattern)
        return str(pattern.CurrentValue or ''), bool(field.CurrentHasKeyboardFocus)


class Accessible:
    """An MSAA client for the calling thread only. Opera GX refuses UI Automation tree walks below its window
    decoration, but its MSAA tree, like every browser's, still shows the tab strip, even under other windows."""

    def __init__(self):
        import comtypes
        import comtypes.client
        from comtypes.automation import VARIANT
        self.variant = VARIANT
        self.module = comtypes.client.GetModule('oleacc.dll')
        self.pointer = ctypes.POINTER(self.module.IAccessible)
        self.ole = ctypes.OleDLL('oleacc')
        self.ole.AccessibleObjectFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.POINTER(comtypes.GUID),
                                                        ctypes.POINTER(self.pointer)]
        self.ole.AccessibleChildren.argtypes = [self.pointer, wintypes.LONG, wintypes.LONG, ctypes.POINTER(VARIANT),
                                                ctypes.POINTER(wintypes.LONG)]

    def window(self, hwnd):
        node = self.pointer()
        self.ole.AccessibleObjectFromWindow(hwnd, OBJID_CLIENT, ctypes.byref(self.module.IAccessible._iid_), ctypes.byref(node))
        return node

    def children(self, node):
        count = min(int(node.accChildCount or 0), 400)
        if not count:
            return []
        values, obtained = (self.variant * count)(), wintypes.LONG()
        self.ole.AccessibleChildren(node, 0, count, values, ctypes.byref(obtained))
        return [variant.value.QueryInterface(self.module.IAccessible) for variant in values[:obtained.value]
                if hasattr(variant.value, 'QueryInterface')]


def find_strip(root, children, budget=STRIP_NODES):
    """Breadth first from the window to the browser's own tab list, never looking inside web pages. A tab list
    without tabs, such as the container of Chrome's vertical tab strip, is searched through instead."""
    queue, visited = [root], 0
    while queue and visited < budget:
        node = queue.pop(0)
        visited += 1
        try:
            role = node.accRole(0)
            if role == ROLE_PAGE_TAB_LIST and page_tabs(node, children):
                return node
            if role != ROLE_DOCUMENT:
                queue.extend(children(node))
        except Exception:
            continue
    return None


def page_tabs(strip, children):
    tabs = []
    for node in children(strip):
        if node.accRole(0) == ROLE_PAGE_TAB:
            tabs.append(node)
        else:
            # Tab groups hold their tabs one level down.
            tabs.extend(child for child in children(node) if child.accRole(0) == ROLE_PAGE_TAB)
    return tabs


def strip_titles(strip, children):
    return [str(node.accName(0) or '') for node in page_tabs(strip, children)]


def tab_runtime_id(node):
    return tuple(automation().client.ElementFromIAccessible(node, 0).GetRuntimeId())


def selected_tab(strip, children=None, identify=None):
    """The tab in front, by its UI Automation runtime ID. Chromium numbers each tab once for as long as it exists,
    so a tab opened later never shares the identity of an earlier one, whatever page either shows."""
    for node in page_tabs(strip, children or accessible().children):
        if int(node.accState(0) or 0) & STATE_SYSTEM_SELECTED:
            return (identify or tab_runtime_id)(node)
    return None


_thread = threading.local()


def automation():
    client = getattr(_thread, 'automation', None)
    if client is None:
        client = _thread.automation = Automation()
    return client


def accessible():
    client = getattr(_thread, 'accessible', None)
    if client is None:
        client = _thread.accessible = Accessible()
    return client


def find_address_field(window):
    return automation().find_address_field(window)


def read_field(field):
    return automation().read(field)


def find_tab_strip(window):
    client = accessible()
    strip = find_strip(client.window(window['hwnd']), client.children)
    return strip if strip is not None and strip_titles(strip, client.children) else None


def tab_titles(strip):
    return strip_titles(strip, accessible().children)


class TabCensus:
    """Reads every browser window's tab titles, so a site left open in a background tab still counts as open."""

    def __init__(self, windows=browser_windows, finder=find_tab_strip, lister=tab_titles, clock=time.monotonic,
                 selector=selected_tab):
        self.windows, self.finder, self.lister, self.clock, self.selector = windows, finder, lister, clock, selector
        self.strips, self.tried = {}, {}

    def strip(self, window, retry):
        key = (window['hwnd'], window['pid'])
        strip = self.strips.get(key)
        if strip is None and self.clock() - self.tried.get(key, float('-inf')) >= retry:
            if len(self.tried) >= 64:
                self.tried.clear()
            self.tried[key] = self.clock()
            strip = self.finder(window)
            if strip is not None:
                if len(self.strips) >= 16:
                    self.strips.clear()
                self.strips[key] = strip
        return strip

    def learn(self, window):
        """Look for this window's tab strip as soon as a learning site is in front of it."""
        self.strip(window, STRIP_LEARN_SECONDS)

    def selected(self, window):
        """The identity of the tab in front of a browser window, or None when its tabs cannot be read."""
        strip = self.strip(window, STRIP_LEARN_SECONDS)
        if strip is None:
            return None
        try:
            return self.selector(strip)
        except Exception:
            self.strips.pop((window['hwnd'], window['pid']), None)
            return None

    def scan(self):
        """One row per browser window: its handle, the watched sites it shows, and whether its tabs were unreadable."""
        rows = []
        for window in self.windows():
            key = (window['hwnd'], window['pid'])
            titles, tabs = [window['title']], None
            strip = self.strip(window, STRIP_RETRY_SECONDS)
            if strip is not None:
                try:
                    tabs = self.lister(strip) or None
                except Exception:
                    tabs = None
                if tabs is None:
                    # Every browser window has a tab, so an empty or broken strip is stale and is looked up again.
                    self.strips.pop(key, None)
            titles.extend(tabs or ())
            sites = {site for site in LEARNING_NAMES if any(title_agrees(site, title) for title in titles)}
            rows.append((window['hwnd'], sites, tabs is None))
        return rows


class AddressReader:
    def __init__(self, finder=find_address_field, reader=read_field, clock=time.monotonic):
        self.finder, self.reader, self.clock = finder, reader, clock
        self.fields, self.misses = {}, {}

    def read(self, window):
        key = (window['hwnd'], window['pid'])
        field = self.fields.get(key)
        if field is None:
            if self.clock() < self.misses.get(key, 0):
                return None
            field = self.finder(window)
            if field is None:
                if len(self.misses) >= 64:
                    self.misses.clear()
                self.misses[key] = self.clock() + MISS_SECONDS
                return None
            if len(self.fields) >= 16:
                self.fields.clear()
            self.fields[key] = field
        try:
            return self.reader(field)
        except Exception:
            self.fields.pop(key, None)
            return None

    def field(self, window):
        return self.fields.get((window['hwnd'], window['pid']))


def key(code, up=False):
    return Input(1, InputUnion(ki=KeyboardInput(code, 0, 2 if up else 0, 0, 0)))


def unicode_key(unit, up=False):
    return Input(1, InputUnion(ki=KeyboardInput(0, unit, 6 if up else 4, 0, 0)))


def chord(*codes):
    send_inputs(*[key(code) for code in codes], *[key(code, True) for code in reversed(codes)])


def type_text(text):
    data = text.encode('utf-16-le')
    units = [int.from_bytes(data[index:index + 2], 'little') for index in range(0, len(data), 2)]
    send_inputs(*[event for unit in units for event in (unicode_key(unit), unicode_key(unit, True))])


def require_foreground(window):
    if int(USER.GetForegroundWindow() or 0) != window['hwnd']:
        raise RuntimeError('The browser lost focus')


class BrowserActions:
    def __init__(self, read=read_field, sleep=time.sleep, clock=time.monotonic):
        self.read, self.sleep, self.clock = read, sleep, clock

    @staticmethod
    def modifiers_held():
        return any(USER.GetAsyncKeyState(code) & 0x8000 for code in (VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN))

    def settle(self, condition, seconds):
        deadline = self.clock() + seconds
        while True:
            try:
                if condition():
                    return True
            except Exception:
                return False
            if self.clock() >= deadline:
                return False
            self.sleep(.05)

    def close_tab(self, window, field):
        require_foreground(window)
        chord(VK_CONTROL, ord('W'))
        # Opera GX ignores keyboard shortcuts after a scripted tab close until something takes focus,
        # so hand the address bar focus once the blocked tab is gone.
        if (self.settle(lambda: not USER.IsWindow(window['hwnd']) or not blocked_site(self.read(field)[0]), 1.0)
                and USER.IsWindow(window['hwnd']) and int(USER.GetForegroundWindow() or 0) == window['hwnd']):
            try:
                field.SetFocus()
            except Exception:
                pass

    def leave_page(self, window, field):
        require_foreground(window)
        field.SetFocus()
        if not self.settle(lambda: self.read(field)[1], .6):
            raise RuntimeError('The address bar did not take focus')
        require_foreground(window)
        chord(VK_CONTROL, ord('A'))
        type_text('about:blank')
        if not self.settle(lambda: self.read(field) == ('about:blank', True), .6):
            chord(VK_ESCAPE)
            raise RuntimeError('The address bar did not accept the blank page')
        require_foreground(window)
        chord(VK_RETURN)


class SecondChance:
    """When a second-chance site was last closed and until when it is cooling down, kept across restarts."""

    def __init__(self, path=None):
        self.path = Path(path or ROOT / '.openclaw/lock-in-cooldowns.json')
        self.lock = threading.Lock()

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return {site: row for site, row in data.items() if site in SECOND_CHANCE and isinstance(row, dict)} if isinstance(data, dict) else {}

    def save(self, rows):
        write_json(self.path, rows)

    def verdict(self, site, now):
        """'cooldown' while the site is let through, 'repeat' when the owner came straight back, else 'first'."""
        row = self.read().get(site, {})
        until, closed = row.get('until'), row.get('closed')
        if type(until) in (int, float) and now < until:
            return 'cooldown'
        if type(closed) in (int, float) and 0 <= now - closed <= SECOND_CHANCE[site]['repeat_seconds']:
            return 'repeat'
        return 'first'

    def closed(self, site, now):
        with self.lock:
            rows = self.read()
            rows[site] = {'closed': now}
            self.save(rows)

    def grant(self, site, now):
        with self.lock:
            rows = self.read()
            rows[site] = {'until': now + SECOND_CHANCE[site]['cooldown_seconds'], 'started': now}
            self.save(rows)
        return rows[site]['until']


def cooldown_line(name, minutes):
    return f'Alright, sir. {name} stays open, and lock in will leave it alone for the next {minutes} minutes.'


class AttemptLog:
    """Every blocked attempt, with when it happened and which site, so Jarvis can tell an instant retry from one
    days later. The lifetime count is kept as well."""

    def __init__(self, path=None, wall=time.time):
        self.path = Path(path or ROOT / '.openclaw/lock-in.json')
        self.lock = threading.Lock()
        self.wall = wall

    def data(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = None
        data = data if isinstance(data, dict) else {}
        count = data.get('attempts')
        history = [row for row in data.get('history', []) if isinstance(row, dict) and type(row.get('at')) in (int, float)]
        return (count if type(count) is int and count >= 0 else 0), history[-ATTEMPT_HISTORY:]

    def read(self):
        return self.data()[0]

    def record(self, site=None):
        with self.lock:
            count, history = self.data()
            count += 1
            history = [*history, {'at': self.wall(), 'site': site}][-ATTEMPT_HISTORY:]
            temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open('w', encoding='utf-8') as out:
                    json.dump({'attempts': count, 'history': history}, out)
                    out.flush()
                    os.fsync(out.fileno())
                temporary.replace(self.path)
            except OSError:
                temporary.unlink(missing_ok=True)
            return count

    def timing(self):
        """When the newest attempt happened relative to the ones before it, in words a spoken line can use."""
        _, history = self.data()
        if not history:
            return {}
        latest = history[-1]
        now, today = latest['at'], time.localtime(latest['at'])[:3]
        before = history[:-1]
        previous = before[-1] if before else None
        same = next((row for row in reversed(before) if row.get('site') == latest.get('site')), None)
        return {
            'localTime': local_time(now),
            'previousAttempt': ago(now - previous['at']) if previous else 'none before this one',
            'previousSite': NAMES.get(previous.get('site'), 'unknown') if previous else None,
            'previousAttemptOnThisSite': ago(now - same['at']) if same else 'none before this one',
            'secondsSincePrevious': round(now - previous['at']) if previous else None,
            'attemptsInLastTenMinutes': sum(now - row['at'] <= 600 for row in history),
            'attemptsInLastHour': sum(now - row['at'] <= 3600 for row in history),
            'attemptsToday': sum(time.localtime(row['at'])[:3] == today for row in history),
        }


@dataclass
class LearningSession:
    """One stretch of a learning site being open. It earns a single reminder, however often focus comes and goes."""
    site: str
    started: float
    seen: float
    used: float
    reminded: bool = False
    text: str = ''
    retry_at: float = 0.0
    windows: set = field(default_factory=set)

    def ended(self, now):
        if now - self.seen >= LEARNING_CLOSED_SECONDS:
            return 'closed'
        if now - self.used >= LEARNING_IDLE_SECONDS:
            return 'idle'
        return None


class SessionLog:
    """Keeps reminded sessions across restarts, so restarting Jarvis with YouTube open does not repeat the reminder.
    Only the site and times are kept, never an address or page title."""

    def __init__(self, path=None):
        self.path = Path(path or ROOT / '.openclaw/learning-sessions.json')

    def read(self, now):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        rows = data.get('sessions') if isinstance(data, dict) else None
        sessions = {}
        for site, row in (rows.items() if isinstance(rows, dict) else ()):
            times = [row.get(name) for name in ('started', 'seen', 'used')] if isinstance(row, dict) else []
            if site in LEARNING_NAMES and len(times) == 3 and all(type(value) in (int, float) for value in times):
                session = LearningSession(site, *map(float, times), reminded=row.get('reminded') is True)
                if session.reminded and session.ended(now) is None and max(times) <= now + 60:
                    sessions[site] = session
        return sessions

    def write(self, sessions):
        write_json(self.path, {'sessions': {site: {'started': session.started, 'seen': session.seen, 'used': session.used,
                                                   'reminded': True}
                                            for site, session in sessions.items() if session.reminded}})


class FocusGuard:
    def __init__(self, controller, reader=None, foreground=foreground_browser, actions=None, clock=time.monotonic,
                 attempts=None, liner=None, reminder_writer=None, wall=time.time, session_log=None, census=None,
                 second_chance=None):
        self.controller = controller
        self.reader = reader or AddressReader()
        self.foreground, self.actions, self.clock, self.wall = foreground, actions or BrowserActions(), clock, wall
        self.attempts = attempts or AttemptLog()
        self.liner = liner or lock_in_line
        self.reminder_writer = reminder_writer or learning_reminder
        self.session_log = session_log or SessionLog()
        self.census = census or TabCensus()
        self.second_chance = second_chance or SecondChance()
        self.learning_session = self.reminder_worker = self.sessions = None
        self.next_scan = self.saved_at = float('-inf')
        self.lock = threading.Lock()
        self.thread = self.warning = self.voice_thread = None
        self.visits = 0
        self.pending = []
        self.recent = {}
        self.allowance = None

    def grant_pass(self, site=None):
        """One visit the owner asked for: the next tab of the site he looks at stays open until he has left it for a
        while. Every other tab of the site is still closed, so the pass cannot open a second one."""
        if site is not None and site not in PASS_SITES:
            raise ValueError('Choose X, Instagram, or either site.')
        with self.lock:
            self.allowance = {'site': site, 'granted': self.clock(), 'seen': None}
        diagnostic('lock_in_pass_granted', domain=site or 'any')
        return self.pass_status()

    def pass_status(self):
        now = self.clock()
        with self.lock:
            ended = self.lapse(now)
            allowance = dict(self.allowance) if self.allowance else None
        if ended:
            diagnostic(ended[0], **ended[1])
        if not allowance:
            return None
        if allowance['seen'] is None:
            return {'state': 'waiting', 'site': allowance['site'],
                    'expiresInSeconds': max(0, round(allowance['granted'] + PASS_WAIT_SECONDS - now))}
        return {'state': 'in_use', 'site': allowance['site']}

    def lapse(self, now):
        allowance = self.allowance
        if allowance is None:
            return None
        if allowance['seen'] is None and now - allowance['granted'] > PASS_WAIT_SECONDS:
            self.allowance = None
            return 'lock_in_pass_expired', {'domain': allowance['site'] or 'any'}
        if allowance['seen'] is not None and now - allowance['seen'] > PASS_AWAY_SECONDS:
            self.allowance = None
            return 'lock_in_pass_used', {'domain': allowance['site']}
        return None

    def allowed(self, site, now, window=None):
        """Whether the pass covers this visit: the first one after it was granted, then only that same tab."""
        with self.lock:
            ended = self.lapse(now)
            covers = site in PASS_SITES and self.allowance is not None and self.allowance['site'] in (None, site)
        if ended:
            diagnostic(ended[0], **ended[1])
        if not covers:
            return False
        # Asking the browser which tab is in front goes through accessibility, so it happens outside the lock.
        tab = self.census.selected(window) if window else None
        with self.lock:
            allowance = self.allowance
            if allowance is None or allowance['site'] not in (None, site):
                return False
            started = allowance['seen'] is None
            if started:
                allowance['tab'] = tab
            elif None not in (allowance.get('tab'), tab) and tab != allowance['tab']:
                return False
            # A tab that cannot be read right now gets the benefit of the doubt rather than closing the allowed one.
            allowance['site'], allowance['seen'] = site, now
        if started:
            diagnostic('lock_in_pass_started', domain=site, tab_tracked=tab is not None)
        return True

    def enabled(self):
        try:
            return self.controller.preferences.read().get('lockInMode', True) is True
        except (OSError, ValueError):
            return True

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='jarvis-lock-in', daemon=True)
            self.thread.start()
        return self.thread

    def run(self):
        import comtypes
        USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        comtypes.CoInitializeEx()
        try:
            self.learning_state()
            while not self.controller.closed.wait(POLL_SECONDS):
                try:
                    self.check()
                except Exception as exc:
                    diagnostic('lock_in_check_failed', error_type=type(exc).__name__)
                    self.controller.closed.wait(2)
        finally:
            for cache in (getattr(self.reader, 'fields', None), getattr(self.reader, 'misses', None),
                          getattr(self.census, 'strips', None)):
                if cache is not None:
                    cache.clear()
            _thread.automation = _thread.accessible = None
            comtypes.CoUninitialize()

    def observe(self, window):
        if not window or not candidate(window['title']):
            return None
        observed = self.reader.read(window)
        if not observed:
            return None
        address, editing = observed
        site = watched_site(address)
        return site if site and not editing and title_agrees(site, window['title']) else None

    def check(self):
        if self.allowance is not None:
            self.pass_status()
        self.refresh_sessions()
        if self.controller.closed.is_set() or self.controller.breaks.active:
            self.leave_learning_site()
            return None
        window = self.foreground()
        if not window or not candidate(window['title']) or not self.enabled():
            self.leave_learning_site()
            return None
        site = self.observe(window)
        if site in LEARNING_NAMES:
            return self.remind(window, site)
        # A focused address bar or temporarily unavailable browser control is not a new visit.
        if site or not (self.learning_session and title_agrees(self.learning_session.site, window['title'])):
            self.leave_learning_site()
        if site not in SITES or self.allowed(site, self.clock(), window):
            return None
        if site in SECOND_CHANCE:
            verdict = self.second_chance.verdict(site, self.wall())
            if verdict == 'cooldown':
                return None
            if verdict == 'repeat':
                return self.start_cooldown(window, site)
        return self.block(window, site)

    def start_cooldown(self, window, site):
        """The owner went straight back after the first close: leave it open and stop closing it for a while."""
        self.second_chance.grant(site, self.wall())
        minutes = round(SECOND_CHANCE[site]['cooldown_seconds'] / 60)
        diagnostic('lock_in_cooldown_started', domain=site, minutes=minutes)
        self.publish(window, site, cooldown_line(NAMES[site], minutes), cooldown=True)
        return None

    def learning_state(self):
        if self.sessions is None:
            try:
                self.sessions = self.session_log.read(self.wall())
            except Exception as exc:
                diagnostic('learning_sessions_unreadable', error_type=type(exc).__name__)
                self.sessions = {}
        return self.sessions

    def save_sessions(self):
        self.saved_at = self.clock()
        try:
            self.session_log.write(self.learning_state())
        except Exception as exc:
            diagnostic('learning_sessions_unsaved', error_type=type(exc).__name__)

    def refresh_sessions(self):
        """End sessions whose site was closed or left unused, and keep alive those still open in some tab."""
        if not self.sessions:
            return
        now, changed = self.wall(), False
        for site, session in list(self.sessions.items()):
            reason = session.ended(now)
            if reason:
                del self.sessions[site]
                changed = True
                if self.learning_session is session:
                    self.learning_session = None
                diagnostic('learning_session_ended', domain=site, reason=reason)
        stale = [session for session in self.sessions.values() if now - session.seen >= LEARNING_SCAN_SECONDS]
        if stale and self.clock() >= self.next_scan:
            self.next_scan = self.clock() + LEARNING_SCAN_SECONDS
            try:
                rows = self.census.scan()
            except Exception as exc:
                # When the tabs cannot be read, assume the site is still open rather than risk a repeat reminder.
                diagnostic('learning_scan_failed', error_type=type(exc).__name__)
                rows = None
            for session in stale:
                if rows is None or any(session.site in sites or (unreadable and hwnd in session.windows)
                                       for hwnd, sites, unreadable in rows):
                    session.seen = now
        if changed or (self.sessions and self.clock() - self.saved_at >= LEARNING_SAVE_SECONDS):
            self.save_sessions()

    def write_reminder(self, session, page):
        try:
            text = self.reminder_writer(LEARNING_NAMES[session.site], page)
            if not text:
                raise ValueError('Empty learning reminder')
            session.text = text
        except Exception as exc:
            session.retry_at = self.clock() + LEARNING_RETRY_SECONDS
            diagnostic('learning_reminder_unavailable', domain=session.site, error_type=type(exc).__name__)

    def leave_learning_site(self):
        self.learning_session = None

    def remind(self, window, site):
        now = self.wall()
        sessions = self.learning_state()
        session = sessions.get(site)
        if session is None:
            session = sessions[site] = LearningSession(site, now, now, now)
            diagnostic('learning_session_started', domain=site)
        session.seen = session.used = now
        if len(session.windows) >= 16:
            session.windows.clear()
        session.windows.add(window['hwnd'])
        self.learning_session = session
        try:
            self.census.learn(window)
        except Exception as exc:
            diagnostic('learning_tabs_unreadable', domain=site, error_type=type(exc).__name__)
        if session.reminded or (self.reminder_worker and self.reminder_worker.is_alive()):
            return None
        if session.text:
            session.reminded = True
            self.save_sessions()
            self.publish(window, site, session.text, learning=session)
            diagnostic('learning_reminder_shown', domain=site, reply_chars=len(session.text))
            return site
        if now - session.started > LEARNING_DELIVERY_SECONDS:
            # Too late to greet the visit; stay quiet for the rest of this session.
            session.reminded = True
            self.save_sessions()
            diagnostic('learning_reminder_skipped', domain=site, reason='not_ready')
            return None
        if self.clock() >= session.retry_at:
            self.reminder_worker = threading.Thread(target=self.write_reminder, args=(session, page_title(site, window['title'])),
                                                    name='jarvis-learning-reminder', daemon=True)
            self.reminder_worker.start()
        return None

    def block(self, window, site):
        now = self.clock()
        last = self.recent.get(window['hwnd'])
        if (last is not None and now - last < SETTLE_SECONDS) or self.actions.modifiers_held():
            return None
        current = self.foreground()
        if not current or current['hwnd'] != window['hwnd'] or self.observe(current) != site:
            return None
        action = 'leave_page' if last is not None and now - last < RETRY_SECONDS else 'close_tab'
        if len(self.recent) >= 32:
            self.recent.clear()
        self.recent[window['hwnd']] = now
        try:
            getattr(self.actions, action)(current, self.reader.field(current))
        except Exception as exc:
            diagnostic('lock_in_failed', domain=site, action=action, error_type=type(exc).__name__)
            return None
        attempt = self.attempts.record(site)
        if site in SECOND_CHANCE:
            self.second_chance.closed(site, self.wall())
        try:
            timing = self.attempts.timing()
        except Exception:
            timing = {}
        diagnostic('lock_in_blocked', domain=site, action=action, attempts=attempt,
                   seconds_since_previous=timing.get('secondsSincePrevious'))
        self.announce(current, site, attempt, timing)
        return site

    def reply(self, site, attempt, timing=None):
        name = NAMES[site]
        try:
            text = self.liner(name, attempt, 'lockin-' + uuid.uuid4().hex, timing or {})
        except Exception as exc:
            diagnostic('lock_in_line_fallback', error_type=type(exc).__name__, attempts=attempt)
            text = None
        text = clean_line(text)
        return text if acceptable(text) else fallback_line(name, attempt, timing)

    def announce(self, window, site, attempt, timing=None):
        self.publish(window, site, self.reply(site, attempt, timing), attempt=attempt)

    def publish(self, window, site, text, *, attempt=None, learning=None, cooldown=False):
        thread = None
        with self.lock:
            self.visits += 1
            visit = self.visits
            self.pending.append((text, learning))
            if self.warning is None:
                thread = self.warning = self.voice_thread = threading.Thread(target=self.warn, name='jarvis-lock-in-voice', daemon=True)
        details = ({'name': LEARNING_NAMES[site], 'learning': True} if learning else {'name': NAMES[site], 'cooldown': True}
                   if cooldown else {'name': NAMES[site], 'attempt': attempt})
        self.controller.events.put(('lock_in', {'visit': visit, 'site': site, 'text': text, 'rect': list(window['rect']), **details}))
        if thread:
            thread.start()

    def speak_line(self, text, *, cache_voice=True):
        controller = self.controller
        if (not controller.muted and controller.voice_ready and not controller.breaks.active
                and controller.busy.acquire(blocking=False)):
            try:
                controller.cancelled = threading.Event()
                if cache_voice and text not in controller.speech_cache:
                    from phone.voice import synthesize_wav
                    controller.speech_cache[text] = synthesize_wav(text)
                controller.speak(text)
            finally:
                controller.release_response()
        else:
            controller.closed.wait(SILENT_VISIT_SECONDS)

    def warn(self):
        controller = self.controller
        try:
            while not controller.closed.is_set():
                with self.lock:
                    item = self.pending.pop(0) if self.pending else None
                if item is not None:
                    text, learning = item
                    if learning is None or (learning is self.learning_session and self.enabled()):
                        self.speak_line(text, cache_voice=learning is None)
                    continue
                if controller.closed.wait(LINGER_SECONDS):
                    break
                with self.lock:
                    if self.pending:
                        continue
                    latest = self.visits
                    self.warning = None
                controller.events.put(('lock_in_finished', latest))
                return
        except Exception as exc:
            diagnostic('lock_in_voice_failed', error_type=type(exc).__name__)
        with self.lock:
            latest = self.visits
            self.warning = None
        controller.events.put(('lock_in_finished', latest))
