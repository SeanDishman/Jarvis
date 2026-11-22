import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop.common import local_request


def verify():
    health = local_request(18792, '/health')
    assert health.get('processTermination'), 'Start the updated desktop before verification.'
    folder = Path(tempfile.mkdtemp(prefix='force-quit-', dir=ROOT / '.cache')).resolve()
    assert folder.parent == (ROOT / '.cache').resolve()
    children = []
    try:
        names = ['Probe' + label + uuid.uuid4().hex[:6] for label in ('Alpha', 'Beta', 'Gamma', 'Guard')]
        executable = Path(os.environ['SystemRoot']) / 'System32/ping.exe'
        for name in names:
            target = folder / (name + '.exe')
            shutil.copy2(executable, target)
            children.append(subprocess.Popen([str(target), '-t', '127.0.0.1'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW))
        time.sleep(.3)
        assert all(child.poll() is None for child in children), 'A fixture process failed to start.'
        text = 'Force quit ' + ', '.join(names[:3]) + '. Leave ' + names[3] + ' alone.'
        result = local_request(18792, '/command', {'text': text, 'speak': False}, timeout=60)
        assert result.get('ok'), result
        identifier = result['taskId']
        print(json.dumps({'task': identifier, 'targets': [child.pid for child in children[:3]],
                          'untargeted': children[-1].pid}), flush=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            jobs = local_request(18792, '/research/jobs')['jobs']
            job = next(row for row in jobs if row['id'] == identifier)
            if job['status'] in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(.5)
        assert job['status'] == 'completed', job
        assert all(child.poll() is not None for child in children[:3]), 'A requested process remained running.'
        assert children[-1].poll() is None, 'An unrelated process was terminated.'
        print(json.dumps({'verified': True, 'terminated': len(children) - 1,
                          'unrelated_process_preserved': True, 'summary': job['summary']}), flush=True)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        for path in folder.iterdir():
            path.unlink()
        folder.rmdir()


if __name__ == '__main__':
    verify()
