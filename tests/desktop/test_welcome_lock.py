"""The welcome-back greeting waits behind a lock screen (Alpenlock or Windows') and greets once the owner is past it."""
from datetime import datetime
import queue
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop.welcome import STALE_SECONDS, PresenceLog, WelcomeBack

MAIN = [0, 0, 1920, 1032]


class FakeController:
    def __init__(self):
        self.closed = threading.Event()
        self.breaks = SimpleNamespace(active=False)
        self.events = queue.Queue()
        self.muted = False
        self.voice_ready = True
        self.busy = threading.Lock()
        self.cancelled = threading.Event()
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def release_response(self):
        self.busy.release()


@patch('desktop.welcome.diagnostic')
class WelcomeBehindLockScreenTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.presence = PresenceLog(Path(folder.name) / 'presence.json')
        self.controller = FakeController()
        self.steps = []
        self.clock = [100.0]
        self.time_of_day = [datetime(2026, 11, 19, 8, 30)]

    def guard(self, covers=(), unlock=True, unlock_after=5.0):
        covers = list(covers)

        def cover():
            self.steps.append('checked')
            value = covers.pop(0) if covers else None
            if isinstance(value, Exception):
                raise value
            return value

        def until_clear(stop):
            self.steps.append('waited')
            self.assertIs(stop, self.controller.closed)
            self.clock[0] += unlock_after
            if unlock_after >= STALE_SECONDS:
                self.time_of_day[0] = datetime(2026, 11, 19, 9, 5)
            return unlock

        def write(hours, part, weekday):
            self.steps.append(('wrote', round(hours, 1), part))
            return f'{part.title()} greeting, sir.'

        return WelcomeBack(self.controller, writer=write, presence=self.presence, work_area=lambda: list(MAIN),
                           local_time=lambda: self.time_of_day[0], cover=cover, until_clear=until_clear,
                           monotonic=lambda: self.clock[0])

    def events(self):
        return list(self.controller.events.queue)

    def test_behind_alpenlock_it_writes_while_he_types_and_greets_once_unlocked(self, _log):
        guard = self.guard(covers=['alpenlock'])
        guard.deliver(7 * 3600)
        self.assertEqual(self.steps, [('wrote', 7.0, 'morning'), 'checked', 'waited'])
        names = [event[0] for event in self.events()]
        self.assertEqual(names, ['welcome', 'welcome_finished'])
        self.assertEqual(self.controller.spoken, ['Morning greeting, sir.'])

    def test_on_a_clear_desktop_it_greets_without_waiting(self, _log):
        guard = self.guard(covers=[None])
        guard.deliver(7 * 3600)
        self.assertNotIn('waited', self.steps)
        self.assertEqual([event[0] for event in self.events()], ['welcome', 'welcome_finished'])

    def test_the_windows_lock_screen_holds_it_too(self, _log):
        guard = self.guard(covers=['windows'])
        guard.deliver(7 * 3600)
        self.assertIn('waited', self.steps)
        self.assertEqual(self.events()[0][0], 'welcome')

    def test_shutting_down_behind_the_lock_screen_greets_nobody(self, _log):
        guard = self.guard(covers=['alpenlock'], unlock=False)
        guard.deliver(7 * 3600)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.controller.spoken, [])

    def test_a_greeting_held_for_hours_is_rewritten_for_the_new_time_of_day(self, _log):
        self.time_of_day[0] = datetime(2026, 11, 19, 3, 10)
        guard = self.guard(covers=['alpenlock'], unlock_after=6 * 3600)
        guard.deliver(7 * 3600)
        written = [step for step in self.steps if isinstance(step, tuple)]
        self.assertEqual(written, [('wrote', 7.0, 'night'), ('wrote', 13.0, 'morning')])
        welcome = self.events()[0][1]
        self.assertEqual((welcome['title'], welcome['text']), ('Good morning', 'Morning greeting, sir.'))

    def test_a_lock_check_that_fails_does_not_cost_him_the_greeting(self, _log):
        guard = self.guard(covers=[OSError('no desktop')])
        guard.deliver(7 * 3600)
        self.assertNotIn('waited', self.steps)
        self.assertEqual(self.events()[0][0], 'welcome')


if __name__ == '__main__':
    unittest.main()
