from concurrent.futures import Future
import ipaddress
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import threading
import time
import urllib.parse

from desktop.web_tools import validate_url

FORBIDDEN = re.compile(r'\b(?:delete|remove|purchase|buy|pay|checkout|send|reply|forward|archive|mark (?:as )?(?:un)?read|publish|post|transfer|unsubscribe|confirm order)\b', re.I)


def browser_backend(name):
    if not name:
        return None
    normalized = str(name).strip().casefold().replace('_', ' ')
    backends = {'msedge':'msedge', 'edge':'msedge', 'microsoft edge':'msedge',
                'chrome':'chrome', 'google chrome':'chrome', 'opera':'opera', 'opera gx':'opera_gx'}
    if normalized not in backends:
        raise ValueError(f'The dedicated browser cannot control {name}. Use the computer tool to operate that installed browser; do not substitute another browser.')
    return backends[normalized]


class ActionCancellation:
    def __init__(self, owner):
        self.owner = owner
        self.expired = threading.Event()

    def is_set(self):
        return self.expired.is_set() or (self.owner is not None and self.owner.is_set())


def opera_executable(kind):
    folder = 'Opera GX' if kind == 'opera_gx' else 'Opera'
    roots = [Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / folder]
    roots += [Path(os.environ[key]) / folder for key in ('ProgramFiles','ProgramFiles(x86)') if os.environ.get(key)]
    for root in roots:
        target = root / 'opera.exe'
        if target.is_file():
            return str(target.resolve())
    raise RuntimeError(folder + ' is not installed in a supported location. I will not substitute a different browser.')


def public_url(url):
    validate_url(url)
    parsed = urllib.parse.urlsplit(url)
    if parsed.port not in (None, 80, 443):
        raise ValueError('Browser actions use public web ports only')
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('Local and private network browser targets are unavailable')
    return url


def owner_named_host(url, command):
    host = (urllib.parse.urlsplit(url).hostname or '').lower().removeprefix('www.')
    if not host:
        return False
    if host in command.lower():
        return True
    name = host.split('.')[0]
    return len(name) >= 3 and bool(re.search(r'\b' + re.escape(name) + r'\b', command, re.I))


def owner_url(url, command):
    parsed = urllib.parse.urlsplit(url)
    if url in command or url.removeprefix('https://').removeprefix('http://') in command:
        return True
    return owner_named_host(url, command) and parsed.path in ('', '/') and not parsed.query and not parsed.fragment


def supplied_value(value, command):
    def normalized(text):
        text = re.sub(r'\s+at\s+', '@', text, flags=re.I)
        text = re.sub(r'\s+dot\s+', '.', text, flags=re.I)
        return re.sub(r'\s+', ' ', text).strip().casefold()
    return not value or normalized(value) in normalized(command)


class BrowserTools:
    def __init__(self, events=None, headless=False):
        self.events = events
        self.headless = headless
        self.queue = queue.Queue(maxsize=10)
        self.worker = None
        self.start_lock = threading.Lock()
        self.runtime = self.browser = self.context = self.page = None
        self.refs = {}
        self.generation = ''
        self.handed_off = False
        self.closed = False
        self.dns_cache = {}
        self.allowed_hosts = set()
        self.browser_choice = None
        self.desired_browser = None
        self.task_lock = threading.Lock()

    def execute(self, action, arguments=None, command='', cancelled=None, browser=''):
        if self.closed:
            return {'ok': False, 'error': 'The browser controller has closed'}
        with self.start_lock:
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True)
                self.worker.start()
        future = Future()
        cancellation = ActionCancellation(cancelled)
        try:
            self.queue.put_nowait((future, action, arguments or {}, command, cancellation, browser))
            return future.result(timeout=27)
        except Exception:
            future.cancel()
            cancellation.expired.set()
            return {'ok': False, 'error': 'The browser action could not finish. Check the Jarvis browser window.'}

    def _run(self):
        while True:
            try:
                task = self.queue.get(timeout=.1)
            except queue.Empty:
                if self.closed:
                    break
                if self.page and not self.page.is_closed():
                    try:
                        self.page.wait_for_timeout(50)
                    except Exception:
                        pass
                continue
            if task is None:
                break
            future, action, arguments, command, cancelled, browser = task
            if not future.set_running_or_notify_cancel():
                continue
            try:
                if cancelled is not None and cancelled.is_set():
                    result = {'ok': False, 'error': 'Cancelled'}
                else:
                    result = self._perform(action, arguments, command, cancelled, browser=browser)
            except (ValueError, RuntimeError) as exc:
                result = {'ok': False, 'error': str(exc)}
            except Exception:
                result = {'ok': False, 'error': 'The browser could not complete that step. Inspect the page and try again.'}
            future.set_result(result)
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
        if self.runtime:
            self.runtime.stop()

    def _ensure(self):
        self.desired_browser = browser_backend(self.desired_browser)
        if not self.headless:
            from desktop.windows import desktop_name
            if desktop_name().lower() != 'default':
                raise RuntimeError('Run Jarvis from your normal Windows desktop to use its visible browser.')
        if self.browser and not self.browser.is_connected():
            self.browser = self.context = self.page = None
            self.handed_off = False
        if self.handed_off:
            raise ValueError('This browser is under your manual control. Close its window before starting a new Jarvis browser session.')
        if self.page and not self.page.is_closed():
            if not self.desired_browser or self.desired_browser == self.browser_choice:
                return
            self.browser.close()
            self.browser = self.context = self.page = None
            self.refs.clear()
            self.allowed_hosts.clear()
        from playwright.sync_api import sync_playwright
        if self.runtime is None:
            self.runtime = sync_playwright().start()
        if self.browser is None:
            choice = self.desired_browser
            if choice in ('opera', 'opera_gx'):
                try:
                    self.browser = self.runtime.chromium.launch(executable_path=opera_executable(choice), headless=self.headless)
                except RuntimeError:
                    raise
                except Exception:
                    raise RuntimeError('Opera could not start its dedicated browser session. Try native desktop control or open Opera manually.') from None
            elif choice:
                self.browser = self.runtime.chromium.launch(channel=choice, headless=self.headless)
            else:
                try:
                    self.browser = self.runtime.chromium.launch(channel='msedge', headless=self.headless)
                    choice = 'msedge'
                except Exception:
                    self.browser = self.runtime.chromium.launch(channel='chrome', headless=self.headless)
                    choice = 'chrome'
            self.browser_choice = choice
        self.context = self.browser.new_context(accept_downloads=False, service_workers='block')
        self.context.route('**/*', self._guard)
        self.context.on('page', self._new_page)
        self.page = self.context.new_page()
        self.page.set_default_timeout(6000)
        self.page.set_default_navigation_timeout(14000)

    def _new_page(self, page):
        page.on('dialog', lambda dialog: dialog.dismiss())
        page.on('download', lambda download: download.cancel())
        page.set_default_timeout(6000)
        page.set_default_navigation_timeout(14000)

    def _guard(self, route):
        request = route.request
        try:
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                return route.abort('blockedbyclient')
            parsed = urllib.parse.urlsplit(request.url)
            host = parsed.hostname
            cache = self.dns_cache.get(host, 0)
            validate_url(request.url)
            if parsed.port not in (None, 80, 443):
                return route.abort('blockedbyclient')
            if time.monotonic() - cache > 30:
                public_url(request.url)
                self.dns_cache[host] = time.monotonic()
            if request.is_navigation_request() and request.frame == request.frame.page.main_frame:
                if host not in self.allowed_hosts:
                    return route.abort('blockedbyclient')
            route.continue_()
        except Exception:
            route.abort('blockedbyclient')

    def _snapshot(self):
        for element, _info in self.refs.values():
            try:
                element.dispose()
            except Exception:
                pass
        self.refs.clear()
        self.generation = secrets.token_hex(5)
        elements = []
        handles = self.page.query_selector_all('a[href],button,input:not([type=hidden]),textarea,select,[role=button]')
        for element in handles[:160]:
            if len(elements) >= 65:
                break
            try:
                if not element.is_visible():
                    continue
                info = element.evaluate('''e => ({tag:e.tagName.toLowerCase(),type:e.type||'',
                  label:(e.getAttribute('aria-label') || (e.labels&&e.labels[0]?.innerText) || e.placeholder || e.innerText || e.name || '').slice(0,160),
                  href:e.tagName==='A'?e.href:'', autocomplete:e.autocomplete||'',
                  options:e.tagName==='SELECT'?Array.from(e.options).slice(0,20).map(o=>o.label):[]})''')
                ref = self.generation + '-' + str(len(elements) + 1)
                self.refs[ref] = (element, info)
                elements.append({'id': ref, **info})
            except Exception:
                continue
        result = {'ok': True, 'url': self.page.url, 'title': self.page.title(),
                  'text': self.page.locator('body').inner_text(timeout=4000)[:14000], 'elements': elements,
                  'mode': 'Navigation and field filling; final submissions require manual control.'}
        if self.events:
            self.events.put(('browser_status', result['title'] + '\n' + result['url']))
        return result

    def _element(self, reference):
        saved = self.refs.get(reference)
        if saved is None or not saved[0].evaluate('e=>e.isConnected'):
            raise ValueError('That page element is stale. Read the browser page again.')
        return saved

    def _perform(self, action, arguments, command, cancelled=None, browser=''):
        if action == 'handoff':
            if not self.context:
                raise ValueError('There is no Jarvis browser to release')
            self.context.unroute('**/*', self._guard)
            self.handed_off = True
            self.refs.clear()
            return {'ok': True, 'message': 'Browser released. You can finish manually; Jarvis control is disconnected.'}
        self.desired_browser = browser_backend(browser) or self.browser_choice
        self._ensure()
        if cancelled is not None and cancelled.is_set():
            return {'ok': False, 'error': 'Cancelled'}
        if action == 'browser_navigate':
            url = str(arguments.get('url', ''))
            if not owner_url(url, command):
                raise ValueError('Name the website or give its full address in your command first')
            public_url(url)
            host = urllib.parse.urlsplit(url).hostname
            base = host.removeprefix('www.')
            self.allowed_hosts.update((host, base, 'www.' + base))
            self.page.goto(url, wait_until='domcontentloaded')
        elif action == 'browser_click':
            element, info = self._element(arguments.get('id'))
            if FORBIDDEN.search(info['label']):
                raise ValueError('Use the browser controls to continue manually for this action')
            if info['tag'] == 'a':
                target = public_url(info['href'])
                host = urllib.parse.urlsplit(target).hostname
                if host not in self.allowed_hosts:
                    if not owner_named_host(target, command):
                        raise ValueError('Name that destination website in your command before following its link')
                    self.allowed_hosts.add(host)
                self.page.goto(target, wait_until='domcontentloaded')
            else:
                form = element.evaluate("e=>e.form?{method:e.form.method,type:e.type}:null")
                if form and form['type'] == 'submit':
                    raise ValueError('The form is ready for you to submit. Choose Continue manually in Browser controls.')
                element.click(timeout=6000)
        elif action == 'browser_fill':
            fields = arguments.get('fields', [])
            if not isinstance(fields, list) or not 1 <= len(fields) <= 12:
                raise ValueError('Fill between one and twelve fields at a time')
            prepared = []
            for field in fields:
                element, info = self._element(field.get('id'))
                value = field.get('value')
                if not isinstance(value, str) or len(value) > 2000 or not supplied_value(value, command):
                    raise ValueError('Dictate the exact field values in your command before filling them')
                if (info['tag'] not in ('input', 'textarea', 'select') or info['type'] in ('password', 'file', 'hidden', 'submit', 'button', 'checkbox', 'radio')
                        or re.search(r'password|cc-|credit|one-time-code', info['autocomplete'], re.I)):
                    raise ValueError('Enter passwords, payment details, uploads and protected fields manually')
                prepared.append((element, info, value))
            for element, info, value in prepared:
                if cancelled is not None and cancelled.is_set():
                    return {'ok': False, 'error': 'Stopped filling fields. Already-filled fields remain visible.'}
                if info['tag'] == 'select':
                    element.select_option(label=value)
                else:
                    element.fill(value)
        elif action == 'browser_scroll':
            amount = max(-3000, min(3000, int(arguments.get('pixels', 700))))
            self.page.mouse.wheel(0, amount)
            self.page.wait_for_timeout(200)
        elif action != 'browser_read':
            raise ValueError('Unknown browser operation')
        if self.page.url == 'about:blank':
            raise ValueError('Tell Jarvis which website to visit first')
        return self._snapshot()

    def close(self):
        self.closed = True
        if self.worker:
            try:
                self.queue.put_nowait(None)
            except queue.Full:
                pass
