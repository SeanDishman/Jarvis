import json
import os
import subprocess
import threading
import time

from desktop.common import ROOT
from desktop.diagnostics import event

MAX_COMMAND = 4000

def powershell_executable():
    return os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                        'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')


def host_arguments(filename):
    return [powershell_executable(), '-NoLogo', '-NoProfile', '-NonInteractive',
            '-Command', (ROOT / 'desktop' / filename).read_text(encoding='utf-8')]


def execute(command, cancel=None, timeout=20):
    if not isinstance(command, str) or not 1 <= len(command.strip()) <= MAX_COMMAND:
        return {'ok': False, 'error': 'Enter a PowerShell diagnostic command of 1 to 4000 characters.'}
    cancel = cancel if cancel is not None else threading.Event()
    if cancel.is_set():
        return {'ok': False, 'cancelled': True, 'error': 'PowerShell command cancelled.'}
    started = time.monotonic()
    event('powershell_started', command_chars=len(command))
    process = None
    try:
        process = subprocess.Popen(host_arguments('powershell-host.ps1'), cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW)
        request = json.dumps({'command': command}).encode('utf-8')
        while True:
            if cancel.is_set():
                return {'ok': False, 'cancelled': True, 'error': 'PowerShell command cancelled.'}
            if time.monotonic() - started >= timeout:
                return {'ok': False, 'timedOut': True, 'error': 'PowerShell exceeded its time limit.'}
            try:
                output, _error = process.communicate(input=request, timeout=.1)
                break
            except subprocess.TimeoutExpired:
                request = None
        if process.returncode:
            return {'ok': False, 'error': 'Windows could not run the PowerShell diagnostic host.'}
        if len(output) > 200000:
            return {'ok': False, 'error': 'PowerShell output exceeded its limit.'}
        result = json.loads(output.decode('utf-8-sig'))
        if not isinstance(result, dict) or type(result.get('ok')) is not bool:
            raise ValueError('Invalid PowerShell response')
        event('powershell_finished', completed=result['ok'], duration_ms=round((time.monotonic()-started)*1000))
        return result
    except (OSError, ValueError):
        event('powershell_failed', reason='host_unavailable')
        return {'ok': False, 'error': 'The Windows PowerShell diagnostic host is unavailable.'}
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
            event('powershell_stopped', cancelled=cancel.is_set(), duration_ms=round((time.monotonic()-started)*1000))


def run_job(job, update):
    update(job, status='running', stage='running', note='Running PowerShell diagnostics', model='Windows PowerShell')
    result = execute(job['_powershellCommand'], job['cancel'])
    if result.get('cancelled'):
        return
    output = result.get('output', '')
    summary = ('PowerShell finished, sir. ' + (output[:1800] or 'The command returned no rows.')) if result['ok'] else result['error']
    if len(output) > 1800:
        summary += '\nThe remaining output is in Activity.'
    update(job, status='completed' if result['ok'] else 'failed',
           stage='completed' if result['ok'] else 'failed',
           note='PowerShell output is ready' if result['ok'] else result['error'],
           output=output, stdout=output, stderr='' if result['ok'] else result['error'],
           summary=summary, truncated=result.get('truncated', False), outputTruncated=result.get('truncated', False),
           finished=time.time())
