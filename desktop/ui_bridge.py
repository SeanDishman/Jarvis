from __future__ import annotations

import hmac
import json
import os
import queue
import secrets
import socket
import subprocess
import threading
import time

from desktop.common import ROOT
from desktop.diagnostics import event as diagnostic

UI_DIR = ROOT / 'desktop/ui'
ELECTRON = UI_DIR / 'node_modules/electron/dist/electron.exe'
LOG = ROOT / 'logs/ui-stderr.log'
DROPPABLE = {'level', 'mic_level'}
CONNECT_SECONDS = 30


def plain(value, depth=0):
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {str(key): plain(item, depth + 1) for key, item in value.items()
                if not str(key).startswith('_') and _serializable(item)}
    if isinstance(value, (list, tuple)):
        return [plain(item, depth + 1) for item in value if _serializable(item)]
    return value


def _serializable(value):
    return value is None or isinstance(value, (str, int, float, bool, dict, list, tuple))


class UIBridge:
    def __init__(self, on_call, on_ready, arguments=(), environment=None, command=None, on_exit=None):
        self.on_call, self.on_ready, self.on_exit = on_call, on_ready, on_exit
        self.arguments = list(arguments)
        self.environment = dict(environment or {})
        self.command = list(command) if command else None
        self.process = None
        self.connection = None
        self.server = None
        self.outbox = queue.Queue(maxsize=4000)
        self.lock = threading.Lock()
        self.connected = threading.Event()
        self.closing = False
        self.ready = False
        self.backlogged = False
        self.failures = []
        self.writer = None
        self.pending = {}

    @property
    def running(self):
        return bool(self.process and self.process.poll() is None)

    def listen(self):
        if self.server is None:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            server.bind(('127.0.0.1', 0))
            server.listen(4)
            server.settimeout(.5)
            self.server = server
        return self.server.getsockname()[1]

    def start(self):
        if self.command is None and not ELECTRON.is_file():
            raise RuntimeError('The Jarvis interface is not installed. Run setup-jarvis.cmd to install it.')
        if self.writer is None:
            self.writer = threading.Thread(target=self._write_loop, name='ui-bridge-writer', daemon=True)
            self.writer.start()
        key = secrets.token_hex(24)
        env = {**os.environ, **self.environment, 'JARVIS_UI_PORT': str(self.listen()), 'JARVIS_UI_KEY': key}
        env.pop('ELECTRON_RUN_AS_NODE', None)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as log:
            log.write(f'\n--- Jarvis interface starting {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n')
        with LOG.open('ab') as log:
            process = subprocess.Popen([*(self.command or [str(ELECTRON), str(UI_DIR)]), *self.arguments], cwd=UI_DIR, env=env,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with self.lock:
            self.process = process
            self.ready = False
        diagnostic('ui_started', pid=process.pid)
        threading.Thread(target=self._accept, args=(process, key), name='ui-bridge-accept', daemon=True).start()
        threading.Thread(target=self._watch, args=(process,), name='ui-bridge-watch', daemon=True).start()
        return process

    def send(self, name, data=None):
        if name in DROPPABLE and (not self.ready or self.outbox.qsize() > 40):
            return False
        return self._queue({'t': 'event', 'name': name, 'data': data})

    def reply(self, identifier, ok, result=None, error=None):
        message = {'t': 'reply', 'id': identifier, 'ok': bool(ok)}
        if ok:
            message['result'] = result
        else:
            message['error'] = str(error or 'The request failed')
        return self._queue(message)

    def request(self, method, params=None, timeout=5):
        identifier = secrets.token_hex(12)
        waiting = queue.Queue(maxsize=1)
        with self.lock:
            if not self.ready or not self.connected.is_set():
                raise RuntimeError('The Jarvis interface is not connected')
            self.pending[identifier] = waiting
        try:
            if not self._queue({'t': 'request', 'id': identifier, 'method': method, 'params': params or {},
                                'deadline': (time.time() + timeout) * 1000}):
                raise RuntimeError('The Jarvis interface could not accept the request')
            try:
                response = waiting.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError('The Jarvis interface did not confirm the request in time') from None
            if not response.get('ok'):
                raise RuntimeError(str(response.get('error') or 'The interface request failed'))
            return response.get('result')
        finally:
            with self.lock:
                self.pending.pop(identifier, None)

    def _queue(self, message):
        if not self.running:
            return False
        try:
            line = (json.dumps(plain(message), ensure_ascii=True, separators=(',', ':')) + '\n').encode('ascii')
        except (TypeError, ValueError):
            diagnostic('ui_message_dropped', reason='unserializable', kind=message.get('name') or message.get('t'))
            return False
        try:
            self.outbox.put_nowait(line)
            self.backlogged = False
            return True
        except queue.Full:
            if not self.backlogged:
                self.backlogged = True
                diagnostic('ui_message_dropped', reason='backlog', kind=message.get('name') or message.get('t'))
            return False

    def _write_loop(self):
        while True:
            line = self.outbox.get()
            if line is None:
                return
            if not self.connected.wait(10):
                continue
            with self.lock:
                connection = self.connection
                message = json.loads(line)
                if message.get('t') == 'request' and message.get('id') not in self.pending:
                    continue
            if connection is None:
                continue
            try:
                connection.sendall(line)
            except OSError:
                self._disconnect(connection)

    def _accept(self, process, key):
        deadline = time.monotonic() + CONNECT_SECONDS
        while process.poll() is None and not self.closing and time.monotonic() < deadline:
            try:
                connection, _address = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                connection.settimeout(5)
                reader = connection.makefile('rb')
                hello = reader.readline(256).strip()
            except OSError:
                connection.close()
                continue
            if not hmac.compare_digest(hello, key.encode('ascii')):
                connection.close()
                continue
            connection.settimeout(None)
            with self.lock:
                if process is not self.process or self.closing:
                    connection.close()
                    return
                self.connection = connection
            self.connected.set()
            self._read_loop(process, connection, reader)
            return
        if process.poll() is None and not self.closing:
            diagnostic('ui_connect_failed')
            process.kill()

    def _read_loop(self, process, connection, reader):
        try:
            for raw in reader:
                try:
                    message = json.loads(raw.decode('utf-8'))
                except (UnicodeDecodeError, ValueError):
                    continue
                kind = message.get('t') if isinstance(message, dict) else None
                if kind == 'ready':
                    with self.lock:
                        if process is self.process:
                            self.ready = True
                    self.on_ready()
                elif kind == 'call' and isinstance(message.get('method'), str):
                    params = message.get('params') if isinstance(message.get('params'), dict) else {}
                    self.on_call(message.get('id'), message['method'], params)
                elif kind == 'response':
                    with self.lock:
                        waiting = self.pending.get(message.get('id'))
                    if waiting is not None:
                        try:
                            waiting.put_nowait(message)
                        except queue.Full:
                            pass
        except OSError:
            pass
        self._disconnect(connection)

    def _disconnect(self, connection):
        with self.lock:
            if self.connection is connection:
                self.connection = None
                self.ready = False
                self.connected.clear()
                for waiting in self.pending.values():
                    try:
                        waiting.put_nowait({'ok': False, 'error': 'The Jarvis interface disconnected'})
                    except queue.Full:
                        pass
                self.pending.clear()
        try:
            connection.close()
        except OSError:
            pass

    def _watch(self, process):
        code = process.wait()
        with self.lock:
            current = process is self.process
            if current:
                self.ready = False
            connection = self.connection if current else None
        if connection is not None:
            self._disconnect(connection)
        if not current:
            return
        if self.on_exit:
            self.on_exit(code)
        if self.closing:
            return
        diagnostic('ui_exited', code=code)
        now = time.monotonic()
        self.failures = [moment for moment in self.failures if now - moment < 60] + [now]
        if len(self.failures) > 4:
            diagnostic('ui_failed', restarts=len(self.failures))
            return
        time.sleep(min(5, len(self.failures)))
        if not self.closing:
            try:
                self.start()
            except (OSError, RuntimeError) as exc:
                diagnostic('ui_restart_failed', error_type=type(exc).__name__)

    def stop(self, timeout=3):
        self.closing = True
        with self.lock:
            process = self.process
        if process and process.poll() is None:
            self._queue({'t': 'event', 'name': 'quit', 'data': None})
            deadline = time.monotonic() + timeout
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
        if process and process.poll() is None:
            process.kill()
        with self.lock:
            connection = self.connection
        if connection is not None:
            self._disconnect(connection)
        if self.server is not None:
            self.server.close()
        try:
            self.outbox.put_nowait(None)
        except queue.Full:
            pass
