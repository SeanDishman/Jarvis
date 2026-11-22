from datetime import datetime
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

from desktop.common import ROOT


class BreakState:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path or ROOT / '.openclaw/break.json')
        self.clock = clock
        self.lock = threading.RLock()
        self.state = {'active': False, 'wakeAt': None}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        self.finish_due()

    @property
    def active(self):
        with self.lock:
            return self.state['active']

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as out:
                json.dump(state, out)
                out.flush()
                os.fsync(out.fileno())
            temporary.replace(self.path)
            self.state = state
        finally:
            temporary.unlink(missing_ok=True)

    def start(self, delay_seconds=None, wake_at=None):
        with self.lock:
            if delay_seconds is not None and wake_at:
                raise ValueError('Specify a duration or a return time, not both')
            deadline = None
            if delay_seconds is not None:
                if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)) or not math.isfinite(delay_seconds) or delay_seconds <= 0:
                    raise ValueError('The break duration must be positive')
                deadline = self.clock() + delay_seconds
            elif wake_at:
                parsed = datetime.fromisoformat(wake_at)
                if parsed.tzinfo is None:
                    raise ValueError('The return time needs a time zone')
                deadline = parsed.timestamp()
            if deadline is not None and (not math.isfinite(deadline) or deadline <= self.clock()):
                raise ValueError('The return time must be in the future')
            self._save({'active': True, 'wakeAt': deadline})
            return self.snapshot()

    def resume(self):
        with self.lock:
            self._save({'active': False, 'wakeAt': None})

    def finish_due(self):
        with self.lock:
            if self.state['active'] and self.state['wakeAt'] is not None and self.clock() >= self.state['wakeAt']:
                self.resume()
                return True
            return False
