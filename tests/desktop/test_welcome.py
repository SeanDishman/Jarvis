from datetime import datetime
import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop import welcome
from desktop.breaks import BreakState
from desktop.welcome import AWAY_SECONDS, TICKS, PresenceLog, WelcomeBack, fallback_greeting, part_of_day

HOUR = 3600 * 1000
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


@patch.object(welcome, 'POLL_SECONDS', 0)
@patch('desktop.welcome.diagnostic')
class WelcomeBackTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.presence_path = Path(folder.name) / 'presence.json'
        self.controller = FakeController()
        self.input = [10 * HOUR]       # tick of the last keyboard or mouse input
        self.now_tick = [10 * HOUR]    # tick right now
        self.wall = [1_800_000_000.0]
        self.clock_time = [datetime(2026, 11, 19, 8, 30)]
        self.writer_calls = []

    def guard(self, writer=None):
        def write(hours, part, weekday):
            self.writer_calls.append((round(hours, 1), part, weekday))
            return 'Good morning, sir. As Seneca wrote, “Begin at once to live.” Let us get to work.'
        # No lock screen is up in these tests; the lock-screen wait has its own tests.
        return WelcomeBack(self.controller, last_input=lambda: self.input[0] % TICKS, tick=lambda: self.now_tick[0] % TICKS,
                           wall=lambda: self.wall[0], writer=writer or write, presence=PresenceLog(self.presence_path),
                           work_area=lambda: list(MAIN), local_time=lambda: self.clock_time[0],
                           cover=lambda: None, until_clear=lambda _stop: True)

    def elapse(self, milliseconds, moved=False):
        self.now_tick[0] += milliseconds
        self.wall[0] += milliseconds / 1000
        if moved:
            self.input[0] = self.now_tick[0]

    def finish(self, guard):
        if guard.worker:
            guard.worker.join(3)
            self.assertFalse(guard.worker.is_alive())
        return list(self.controller.events.queue)

    def test_the_first_input_after_six_hours_away_brings_one_greeting_on_the_main_monitor(self, _log):
        guard = self.guard()
        guard.check()
        self.elapse(7 * HOUR)
        guard.check()
        self.assertIsNone(guard.worker)
        self.elapse(1000, moved=True)
        guard.check()
        events = self.finish(guard)
        for _ in range(30):
            self.elapse(2000, moved=True)
            guard.check()
        self.finish(guard)
        self.assertEqual(self.writer_calls, [(7.0, 'morning', 'Thursday')])
        welcome_rows = [row[1] for row in events if row[0] == 'welcome']
        self.assertEqual(len(welcome_rows), 1)
        self.assertEqual(welcome_rows[0]['rect'], MAIN)
        self.assertEqual(welcome_rows[0]['title'], 'Good morning')
        self.assertEqual(self.controller.spoken, [welcome_rows[0]['text']])
        self.assertEqual([row[0] for row in events], ['welcome', 'welcome_finished'])
        self.assertFalse(self.controller.busy.locked())

    def test_shorter_absences_are_left_alone(self, _log):
        guard = self.guard()
        guard.check()
        for gap in (5.9 * HOUR, 30 * 60 * 1000, 1000):
            self.elapse(gap)
            self.elapse(500, moved=True)
            guard.check()
        self.assertIsNone(guard.worker)
        self.assertTrue(self.controller.events.empty())

    def test_the_away_time_survives_the_tick_counter_wrapping_around(self, _log):
        self.input[0] = self.now_tick[0] = TICKS - HOUR
        guard = self.guard()
        guard.check()
        self.elapse(8 * HOUR, moved=True)
        guard.check()
        self.finish(guard)
        self.assertEqual(self.writer_calls[0][0], 8.0)

    def test_after_the_computer_was_off_overnight_jarvis_greets_once_he_is_at_it(self, _log):
        PresenceLog(self.presence_path).write(self.wall[0] - 9 * 3600)
        guard = self.guard()
        guard.started()
        self.elapse(5 * 60 * 1000)
        guard.check()
        self.assertIsNone(guard.worker, 'nobody has touched the computer since Jarvis started')
        self.elapse(1000, moved=True)
        guard.check()
        self.finish(guard)
        self.elapse(2000, moved=True)
        guard.check()
        self.finish(guard)
        self.assertEqual(len(self.writer_calls), 1)
        self.assertGreaterEqual(self.writer_calls[0][0], 9.0)

    def test_restarting_jarvis_soon_after_use_or_for_the_first_time_does_not_greet(self, _log):
        for seen in (None, self.wall[0] - 20 * 60):
            with self.subTest(seen=seen):
                if seen is None:
                    self.presence_path.unlink(missing_ok=True)
                else:
                    PresenceLog(self.presence_path).write(seen)
                guard = self.guard()
                guard.started()
                self.elapse(1000, moved=True)
                guard.check()
                self.assertIsNone(guard.worker)

    def test_presence_is_saved_while_he_is_here_and_holds_only_the_time(self, _log):
        guard = self.guard()
        self.elapse(1000, moved=True)
        guard.check()
        stored = json.loads(self.presence_path.read_text(encoding='utf-8'))
        self.assertEqual(set(stored), {'active'})
        self.assertAlmostEqual(stored['active'], self.wall[0], delta=1)
        self.elapse(60 * 1000, moved=True)
        guard.check()
        self.assertAlmostEqual(json.loads(self.presence_path.read_text(encoding='utf-8'))['active'], self.wall[0] - 60, delta=1)

    def test_muted_jarvis_shows_the_greeting_without_speaking_and_a_break_skips_it(self, _log):
        self.controller.muted = True
        with patch.object(self.controller.closed, 'wait', return_value=False):
            guard = self.guard()
            guard.check()
            self.elapse(7 * HOUR, moved=True)
            guard.check()
            events = self.finish(guard)
        self.assertEqual([row[0] for row in events], ['welcome', 'welcome_finished'])
        self.assertEqual(self.controller.spoken, [])
        self.controller.muted, self.controller.breaks.active = False, True
        self.controller.events = queue.Queue()
        guard = self.guard()
        guard.check()
        self.elapse(7 * HOUR, moved=True)
        guard.check()
        self.assertIsNone(guard.worker)
        self.assertTrue(self.controller.events.empty())

    def test_without_the_model_the_greeting_still_fits_the_time_of_day(self, _log):
        self.clock_time[0] = datetime(2026, 11, 19, 23, 40)
        guard = self.guard(writer=lambda *_: (_ for _ in ()).throw(OSError('offline')))
        guard.check()
        self.elapse(6 * HOUR + 1000, moved=True)
        guard.check()
        self.finish(guard)
        self.assertEqual(self.controller.spoken, ["Welcome back, sir. Let's get to work."])

    def test_parts_of_the_day(self, _log):
        self.assertEqual([part_of_day(hour) for hour in (4, 5, 11, 12, 16, 17, 21, 22, 0)],
                         ['night', 'morning', 'morning', 'afternoon', 'afternoon', 'evening', 'evening', 'night', 'night'])
        self.assertEqual(fallback_greeting('evening'), "Good evening, sir. Let's get to work.")


class WelcomeDesktopTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        from desktop.orb import Orb
        self.breaks = BreakState(Path(temporary.name) / 'break.json')
        for patcher in (patch('desktop.controller.BreakState', return_value=self.breaks), patch.object(Orb, 'save_settings'),
                        patch('desktop.orb.diagnostic'), patch('desktop.controller.diagnostic'), patch('desktop.listener.diagnostic')):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.orb = Orb(interface=False)
        for service in (self.orb.controller.research, self.orb.controller.tasks, self.orb.controller.browser):
            self.addCleanup(service.close)
        self.requests, self.sent = [], []
        self.orb.ui = SimpleNamespace(request=lambda method, params, timeout: self.requests.append((method, params)) or {'verified': True},
                                      send=lambda name, data=None: self.sent.append((name, data)))

    def test_controller_owns_the_welcome_watcher_without_starting_it(self):
        self.assertIsInstance(self.orb.controller.welcome, WelcomeBack)
        self.assertIsNone(self.orb.controller.welcome.thread)

    def test_he_flies_big_to_the_main_monitor_with_a_card_then_home(self):
        row = {'visit': 1, 'text': 'Good morning, sir.', 'title': 'Good morning', 'rect': MAIN}
        self.orb.events.put(('welcome', row))
        self.orb.step()
        self.assertEqual(self.sent, [('lockin', {'site': 'welcome', 'name': 'Jarvis', 'text': 'Good morning, sir.',
                                                 'welcome': True, 'title': 'Good morning'})])
        self.assertEqual(self.requests, [('visit_orb', {'rect': MAIN, 'holdMs': welcome.WELCOME_HOLD_MS,
                                                        'scale': welcome.WELCOME_SCALE})])
        self.orb.events.put(('welcome_finished', 1))
        self.orb.step()
        self.assertEqual(self.requests[-1], ('return_orb', {}))

    def test_a_lock_in_visit_that_starts_later_keeps_the_orb_until_it_ends(self):
        self.orb.events.put(('welcome', {'visit': 1, 'text': 'Hello, sir.', 'title': 'Good evening', 'rect': MAIN}))
        self.orb.events.put(('lock_in', {'visit': 5, 'site': 'x', 'name': 'X', 'text': 'Closed.', 'rect': MAIN}))
        self.orb.events.put(('welcome_finished', 1))
        self.orb.step()
        self.assertNotIn(('return_orb', {}), self.requests)
        self.orb.events.put(('lock_in_finished', 5))
        self.orb.step()
        self.assertEqual(self.requests[-1], ('return_orb', {}))

    def test_hidden_jarvis_does_not_reveal_the_orb_for_a_greeting(self):
        self.orb.ui_hidden = True
        self.orb.sync_break_visibility()
        self.orb.events.put(('welcome', {'visit': 1, 'text': 'Hello, sir.', 'title': 'Good morning', 'rect': MAIN}))
        self.orb.step()
        self.assertEqual(self.requests, [])
        self.assertEqual(self.sent.count(('lockin', {'site': 'welcome', 'name': 'Jarvis', 'text': 'Hello, sir.',
                                                     'welcome': True, 'title': 'Good morning'})), 0)


if __name__ == '__main__':
    unittest.main()
