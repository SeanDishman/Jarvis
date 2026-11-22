from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
import json
from pathlib import Path

from desktop.common import ROOT
from desktop.weather import Weather


class Briefing:
    def __init__(self, preferences, reminders, mail, state_path=None):
        self.preferences, self.reminders, self.mail = preferences, reminders, mail
        self.weather = Weather(preferences)
        self.path = Path(state_path or ROOT / '.openclaw/briefing-state.json')

    def collect(self):
        pool = ThreadPoolExecutor(max_workers=2)
        weather = pool.submit(self.weather.current)
        email = pool.submit(self.mail.search, 'all', '', True, 5)
        wait([weather, email], timeout=22)
        def result(future, message):
            try:
                return future.result(timeout=0)
            except Exception:
                return {'ok': False, 'error': message}
        try:
            return {'ok': True, 'local_time': datetime.now().astimezone().isoformat(),
                    'weather': result(weather, 'Weather location is unavailable. The VPN location was not used.'),
                    'reminders': self.reminders.list(),
                    'email': result(email, 'Email is unavailable or timed out. Check Email accounts on the orb.'),
                    'email_scope': 'Up to five unread inbox messages per connected provider; prioritize relevant items from this sample.'}
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def scheduled_today(self):
        when = self.preferences.read()['briefingTime']
        if not when:
            return False
        now = datetime.now()
        hour, minute = map(int, when.split(':'))
        elapsed = now.hour * 60 + now.minute - hour * 60 - minute
        if not 0 <= elapsed < 240:
            return False
        state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        return state.get('lastDate') != now.date().isoformat()

    def mark_delivered(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'lastDate': datetime.now().date().isoformat()}), encoding='utf-8')
        temporary.replace(self.path)
