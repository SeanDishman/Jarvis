import json
import contextlib
import msvcrt
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.openclaw/openclaw.json'
sys.path.insert(0, str(ROOT))
from desktop.windows import desktop_name


@contextlib.contextmanager
def startup_lock():
    lock_path = ROOT / '.openclaw/startup.lock'
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open('a+b') as lock:
        if not lock_path.stat().st_size:
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + 30
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Another Jarvis startup is still running. Wait a moment and try again.')
                time.sleep(.2)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def request(url, token):
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)


def port_available(url):
    port = urllib.parse.urlsplit(url).port
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def ensure_interface(node_dir):
    ui = ROOT / 'desktop/ui'
    electron = ui / 'node_modules/electron/dist/electron.exe'
    if electron.is_file():
        return
    print('Installing the Jarvis interface. This happens once and needs an internet connection...', flush=True)
    node = node_dir / 'node.exe'
    env = {**os.environ, 'PATH': str(node_dir) + os.pathsep + os.environ.get('PATH', '')}
    (ROOT / 'logs').mkdir(exist_ok=True)
    steps = [[str(node), str(node_dir / 'node_modules/npm/bin/npm-cli.js'), 'ci', '--no-audit', '--no-fund'],
             [str(node), str(ui / 'node_modules/electron/install.js')]]
    with (ROOT / 'logs/ui-install.log').open('w', encoding='utf-8') as log:
        for command in steps:
            if electron.is_file():
                break
            result = subprocess.run(command, cwd=ui, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                break
    if not electron.is_file():
        raise RuntimeError('The Jarvis interface could not be installed. See logs/ui-install.log.')
    print('Jarvis interface installed.', flush=True)


def main():
    config = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    token = config['gateway']['auth']['token']
    env = os.environ.copy()
    node_dir = ROOT / '.runtime/node-v24.21.0-win-x64'
    env.update(OPENCLAW_STATE_DIR=str(ROOT / '.openclaw'), OPENCLAW_CONFIG_PATH=str(CONFIG),
               JARVIS_TMP_DIR=str(ROOT / '.openclaw/tmp-root'), XDG_CACHE_HOME=str(ROOT / '.cache'))
    env['PATH'] = str(node_dir) + os.pathsep + env.get('PATH', '')
    (ROOT / 'logs').mkdir(exist_ok=True)
    soul = (ROOT / 'prompts/SOUL.md').read_text(encoding='utf-8')
    target = ROOT / '.openclaw/workspace/SOUL.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_text(encoding='utf-8') != soul:
        target.write_text(soul, encoding='utf-8')
    ensure_interface(node_dir)
    services = [
        ('luna', [str(node_dir / 'node.exe'), str(ROOT / 'phone/luna-server.mjs')],
         'http://127.0.0.1:18791/health', lambda data: data.get('service') == 'jarvis-luna' and data.get('learningReminders') and data.get('welcomeGreetings') and data.get('version', 0) >= 34 and data.get('compoundActions') and data.get('processGroupTargets') and data.get('backgroundControlClicks') and data.get('processTermination') and data.get('orbMonitorControl') and data.get('semanticToolRouting') and data.get('computerToolConversation') and data.get('multiMonitorControl')),
    ]
    services.append(('desktop', [sys.executable, '-u', str(ROOT / 'desktop/orb.py')],
                     'http://127.0.0.1:18792/health', lambda data: data.get('service') == 'jarvis-desktop' and data.get('learningReminders') and data.get('version',0) >= 44 and data.get('compoundActions') and data.get('processGroupTargets') and data.get('continuationWordConfirmation') and data.get('backgroundControlClicks') and data.get('interruptionRecovery') and data.get('processTermination') and data.get('orbMonitorControl') and data.get('speechPrefetch') and data.get('semanticToolRouting') and data.get('computerToolConversation') and data.get('wakeWordRequiredForEveryCommand') and data.get('multiMonitorControl') and data.get('independentTasks') and data.get('lockInMode') and data.get('voiceReady') and (data.get('interface') or {}).get('kind') == 'electron' and (data.get('interface') or {}).get('running') and data.get('windowsDesktop') == desktop_name()))
    if '--gateway' in sys.argv:
        services.append(('gateway', [str(node_dir / 'node.exe'),
                         str(ROOT / '.runtime/openclaw/node_modules/openclaw/openclaw.mjs'), 'gateway', 'run'],
                         'http://127.0.0.1:18789/v1/models', lambda data: data.get('object') == 'list'))
    for name, command, url, matches in services:
        existing = None
        try:
            existing = request(url, token)
        except urllib.error.URLError as exc:
            if not port_available(url):
                raise RuntimeError(f'{name} could not be checked. Retry after the current service finishes starting.') from None
        if existing:
            if (name == 'desktop' and existing.get('service') == 'jarvis-desktop'
                    and existing.get('windowsDesktop','').lower() == 'default' and desktop_name().lower() != 'default'):
                print('desktop is already running on your Windows desktop.', flush=True)
                continue
            if matches(existing):
                print(name + ' is already running.', flush=True)
                continue
            expected_service = {'luna':'jarvis-luna', 'desktop':'jarvis-desktop'}.get(name)
            if not expected_service or existing.get('service') != expected_service:
                raise RuntimeError(f'The {name} port is occupied by an unexpected service.')
            shutdown = urllib.request.Request(url.rsplit('/', 1)[0] + '/shutdown', data=b'{}',
                headers={'Authorization': 'Bearer ' + token})
            try:
                with urllib.request.urlopen(shutdown, timeout=3):
                    pass
            except urllib.error.HTTPError:
                raise RuntimeError(f'{name} is still answering. Wait for it to finish, then start Jarvis again.') from None
            stopped = False
            for _ in range(15):
                time.sleep(.2)
                if port_available(url):
                    stopped = True
                    break
            if not stopped:
                raise RuntimeError(f'{name} is still closing. Start Jarvis again in a moment.')
        with (ROOT / f'logs/{name}-stdout.log').open('w', encoding='utf-8') as stdout, \
             (ROOT / f'logs/{name}-stderr.log').open('w', encoding='utf-8') as stderr:
            proc = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                    stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
        (ROOT / f'.openclaw/{name}.pid').write_text(str(proc.pid))
        print('Starting ' + name + '...', flush=True)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f'{name} exited. See logs/{name}-stderr.log' + (' and logs/ui-stderr.log.' if name == 'desktop' else '.'))
            try:
                if matches(request(url, token)):
                    print(name + ' is ready.', flush=True)
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError(f'{name} did not become ready. See logs/{name}-stderr.log' + (' and logs/ui-stderr.log.' if name == 'desktop' else '.'))
    if '--resume' in sys.argv:
        from desktop.common import local_request
        local_request(18792, '/show', {}, timeout=3)
    if not request('http://127.0.0.1:18792/health', token).get('visibleOnUserDesktop'):
        print('Jarvis is on a restricted desktop. Run setup-jarvis.cmd from normal Windows to show the orb and access the microphone.', flush=True)
    else:
        print('Jarvis desktop is running. Say Jarvis, then ask a question.', flush=True)


if __name__ == '__main__':
    if sys.stdout is None:
        (ROOT / 'logs').mkdir(exist_ok=True)
        sys.stdout = (ROOT / 'logs/startup.log').open('a', encoding='utf-8')
    if sys.stderr is None:
        sys.stderr = sys.stdout
    try:
        with startup_lock():
            main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
