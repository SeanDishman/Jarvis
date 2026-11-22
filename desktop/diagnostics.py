from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

from desktop.common import ROOT

LOG_PATH = ROOT / 'logs/jarvis-events.jsonl'
FIELDS = frozenset(('state', 'previous_state', 'source', 'reason', 'error_type', 'error_code',
    'request_id', 'command_chars', 'heard_chars', 'word_count', 'wake', 'listening',
    'muted', 'seconds', 'duration_ms', 'audio_ms', 'model', 'model_tier', 'action',
    'domain', 'spoken', 'reply_chars', 'device', 'sample_rate', 'windows_desktop',
    'ui_visible', 'version', 'cancelled', 'completed', 'needs_clarification',
    'input_peak', 'queue_drops', 'input_overflows', 'installed', 'on_user_desktop',
    'pause_seconds', 'max_audio_seconds', 'attempts'))


class QuietRotatingHandler(RotatingFileHandler):
    def handleError(self, _record):
        pass


class EventLog:
    def __init__(self, path=LOG_PATH, max_bytes=1024 * 1024, backups=3):
        self.path = Path(path)
        self.max_bytes, self.backups = max_bytes, backups
        self.logger = logging.Logger('jarvis.diagnostics', level=logging.INFO)
        self.logger.propagate = False
        self.handler = None
        import threading
        self.lock = threading.Lock()

    def write(self, event, **fields):
        try:
            with self.lock:
                if self.handler is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self.handler = QuietRotatingHandler(self.path, maxBytes=self.max_bytes,
                        backupCount=self.backups, encoding='utf-8', delay=True)
                    self.logger.addHandler(self.handler)
                data = {'time': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                        'pid': os.getpid(), 'event': str(event)[:80]}
                for key, value in fields.items():
                    if key in FIELDS and (value is None or isinstance(value, (str, int, float, bool))):
                        data[key] = value[:240] if isinstance(value, str) else value
                self.logger.info(json.dumps(data, ensure_ascii=True))
        except Exception:
            pass

    def close(self):
        with self.lock:
            if self.handler is not None:
                self.logger.removeHandler(self.handler)
                self.handler.close()
                self.handler = None


_events = EventLog()
event = _events.write
