"""Reminders wait for the owner: they come up at his first mouse or keyboard input after their time, and the greeting
he gets after hours away carries the ones due then, including one he asked for that morning."""
from datetime import datetime
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.learning_reminders import QuoteHistory, passed_on, passes_on, welcome_greeting
from desktop.reminders import Reminders, WaitingReminders
from desktop.welcome import TICKS, PresenceLog, WelcomeBack

MAIN = [0, 0, 1920, 1032]
START = datetime(2026, 11, 21, 16, 0)   # a Saturday afternoon, local time
LIGHTER = 'Put the cigarette lighter in the car'


class FakeController:
    """The parts of Jarvis the presence watcher uses, with the real reminder hand-over."""
    deliver_reminders = Controller.deliver_reminders

    def __init__(self, store):
        self.closed = threading.Event()
        self.breaks = SimpleNamespace(active=False)
        self.events = queue.Queue()
        self.muted = False
        self.voice_ready = True
        self.busy = threading.Lock()
        self.cancelled = threading.Event()
        self.spoken = []
        self.reminders = store
        self.waiting_reminders = WaitingReminders()
        self.pending_announcements = []

    def speak(self, text):
        self.spoken.append(text)

    def release_response(self):
        self.busy.release()


def temporary_folder(test):
    folder = tempfile.TemporaryDirectory()
    test.addCleanup(folder.cleanup)
    return Path(folder.name)


@patch('desktop.reminders.diagnostic')
class MorningReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.wall = [START.timestamp()]
        self.store = Reminders(temporary_folder(self) / 'reminders.json', lambda: self.wall[0])

    def at(self, *moment):
        self.wall[0] = datetime(*moment).timestamp()

    def test_a_morning_reminder_is_due_when_that_morning_begins(self, _log):
        result = self.store.create(LIGHTER, morning_of='2026-11-22')
        self.assertEqual(datetime.fromtimestamp(result['reminder']['due']), datetime(2026, 11, 22, 5, 0))
        self.assertIs(result['reminder']['morning'], True)
        self.assertIn('greeting', result['heard'])
        self.assertIn('first mouse or keyboard input', self.store.create('Stretch', delay_seconds=60)['heard'])
        self.at(2026, 11, 22, 1, 30)
        early = self.store.create('Take the bins out', morning_of='2026-11-22 this morning')['reminder']
        self.assertEqual(datetime.fromtimestamp(early['due']), datetime(2026, 11, 22, 5, 0), 'after midnight the morning is still to come')

    def test_what_cannot_be_a_morning_reminder_is_refused(self, _log):
        self.at(2026, 11, 22, 7, 0)
        for arguments, message in (({'morning_of': '2026-11-22'}, 'already begun'),
                                   ({'morning_of': '2026-11-23', 'delay_seconds': 60}, 'only one'),
                                   ({'morning_of': '2026-11-23', 'due_at': '2026-11-23T08:00:00-06:00'}, 'only one'),
                                   ({'morning_of': 'tomorrow'}, 'YYYY-MM-DD'),
                                   ({'morning_of': '2026-11-23', 'repeat': 'interval', 'interval_seconds': 3600}, 'repeats daily')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.store.create('Take the bins out', **arguments)
        self.assertEqual(self.store.list()['reminders'], [])

    def test_a_greeting_takes_only_what_is_due_and_that_days_morning_reminder(self, _log):
        lighter = self.store.create(LIGHTER, morning_of='2026-11-22')['reminder']
        garage = self.store.create('Call the garage', due_at=datetime(2026, 11, 22, 8, 0).astimezone().isoformat())['reminder']
        self.at(2026, 11, 21, 23, 50)
        self.assertEqual(self.store.for_greeting(), [], 'the evening before is not that morning')
        self.at(2026, 11, 22, 0, 10)
        self.assertEqual([row['id'] for row in self.store.for_greeting()], [lighter['id']])
        self.at(2026, 11, 22, 5, 1)
        self.assertEqual(self.store.poll(), [], 'a reminder the greeting gave is not given again when the morning begins')
        self.at(2026, 11, 22, 8, 1)
        self.assertEqual([row['id'] for row in self.store.for_greeting()], [garage['id']], 'a timed one is never early')

    def test_a_daily_morning_reminder_moves_on_to_the_next_morning(self, _log):
        row = self.store.create('Take your vitamins', morning_of='2026-11-22', repeat='daily')['reminder']
        self.at(2026, 11, 22, 3, 0)
        self.assertEqual([found['id'] for found in self.store.for_greeting()], [row['id']])
        following = self.store.change('dismiss', row['id'])['reminder']
        self.assertEqual((datetime.fromtimestamp(following['due']), following['state'], following['morning']),
                         (datetime(2026, 11, 23, 5, 0), 'scheduled', True))

    def test_a_reminder_waiting_for_him_counts_from_when_it_fell_due(self, _log):
        waiting = WaitingReminders()
        waiting.add({'id': 'a', 'text': 'A', 'due': 100.0})
        waiting.add({'id': 'b', 'text': 'B', 'due': 50.0, 'snoozed_until': 300.0})
        self.assertFalse(waiting.ready(99))
        self.assertTrue(waiting.ready(100))
        self.assertEqual([row['id'] for row in waiting.take(200)], ['a'], 'a snoozed one counts from the end of its snooze')
        self.assertEqual([row['id'] for row in waiting.take()], ['b'])
        self.assertEqual(waiting.take(), [])


@patch('desktop.controller.diagnostic')
@patch('desktop.reminders.diagnostic')
@patch('desktop.welcome.diagnostic')
class RemindersWaitForTheOwnerTests(unittest.TestCase):
    def setUp(self):
        folder = temporary_folder(self)
        self.wall = [START.timestamp()]
        self.now_tick = [50 * 3600 * 1000]
        self.input = [self.now_tick[0]]   # he is at the computer when the test starts
        self.store = Reminders(folder / 'reminders.json', lambda: self.wall[0])
        self.controller = FakeController(self.store)
        self.covers = []
        self.writer_calls = []
        self.guard = WelcomeBack(self.controller, last_input=lambda: self.input[0] % TICKS, tick=lambda: self.now_tick[0] % TICKS,
                                 wall=lambda: self.wall[0], writer=self.write, presence=PresenceLog(folder / 'presence.json'),
                                 work_area=lambda: list(MAIN), local_time=lambda: datetime.fromtimestamp(self.wall[0]),
                                 cover=lambda: self.covers.pop(0) if self.covers else None, until_clear=lambda _stop: True)
        self.guard.check()

    def write(self, hours, part, weekday, reminders=()):
        self.writer_calls.append((round(hours, 1), part, list(reminders)))
        return ' '.join([f'{part.title()} greeting, sir.'] + [f'You asked me to remind you: {text}.' for text in reminders])

    def step(self, seconds=2, moved=False):
        """Time passes, Jarvis's background loop polls the reminders, and the presence watcher looks for input."""
        self.now_tick[0] += int(seconds * 1000)
        self.wall[0] += seconds
        if moved:
            self.input[0] = self.now_tick[0]
        for row in self.store.poll():
            self.controller.waiting_reminders.add(row)
        self.guard.check()
        if self.guard.worker:
            self.guard.worker.join(3)
            self.assertFalse(self.guard.worker.is_alive())

    def events(self):
        return list(self.controller.events.queue)

    def delivered(self):
        return [event[1]['text'] for event in self.events() if event[0] == 'reminder_due']

    def announced(self):
        return [row['text'] for row in self.controller.pending_announcements]

    def test_a_reminder_that_falls_due_while_he_is_away_waits_for_his_first_input(self, *_):
        self.store.create('Call the garage', delay_seconds=600)
        self.step(30)
        for _ in range(3):
            self.step(600)
        self.assertEqual((self.delivered(), self.announced()), ([], []), 'nobody is at the computer to hear it')
        self.step(2, moved=True)
        self.assertEqual((self.delivered(), self.announced()), (['Call the garage'], ['Call the garage']))
        for _ in range(5):
            self.step(2, moved=True)
        self.assertEqual(self.delivered(), ['Call the garage'], 'it comes up once')

    def test_while_he_is_working_it_comes_up_right_after_its_time(self, *_):
        self.store.create('Stretch', delay_seconds=60)
        for _ in range(29):
            self.step(2, moved=True)
        self.assertEqual(self.delivered(), [])
        self.step(2, moved=True)
        self.assertEqual(self.delivered(), ['Stretch'])

    def test_input_from_before_its_time_does_not_count(self, *_):
        self.store.create('Stretch', delay_seconds=60)
        self.step(50, moved=True)
        self.step(30)
        self.step(30)
        self.assertEqual(self.delivered(), [], 'his last touch was before it was due')
        self.step(2, moved=True)
        self.assertEqual(self.delivered(), ['Stretch'])

    def test_behind_a_lock_screen_it_waits_until_he_is_past_it(self, *_):
        self.store.create('Stretch', delay_seconds=60)
        self.step(120)
        self.covers = ['alpenlock', 'windows']
        self.step(2, moved=True)
        self.step(2, moved=True)
        self.assertEqual(self.delivered(), [], 'typing the password is input too')
        self.step(2, moved=True)
        self.assertEqual(self.delivered(), ['Stretch'])

    def test_a_break_holds_reminders_and_one_cancelled_meanwhile_is_dropped(self, *_):
        self.store.create('Stretch', delay_seconds=60)
        dropped = self.store.create('Water the plants', delay_seconds=60)['reminder']
        self.controller.breaks.active = True
        self.step(120, moved=True)
        self.step(2, moved=True)
        self.assertEqual(self.delivered(), [])
        self.store.change('cancel', dropped['id'])
        self.controller.breaks.active = False
        self.step(2, moved=True)
        self.assertEqual(self.delivered(), ['Stretch'])

    def test_the_morning_greeting_carries_the_reminder_he_set_for_the_morning(self, *_):
        self.store.create(LIGHTER, morning_of='2026-11-22')
        self.step(1800, moved=True)        # 16:30, the last touch before bed
        self.step(14 * 3600)               # 06:30, the morning has begun and nobody is at the computer
        self.assertEqual(self.events(), [])
        self.step(2, moved=True)           # he sits down
        self.assertEqual(self.writer_calls, [(14.0, 'morning', [LIGHTER])])
        self.assertEqual([event[0] for event in self.events()], ['welcome', 'reminder_due', 'welcome_finished'])
        self.assertEqual(self.controller.spoken, [f'Morning greeting, sir. You asked me to remind you: {LIGHTER}.'])
        self.assertEqual(self.announced(), [], 'the greeting said it, so it is not said again on its own')
        for _ in range(5):
            self.step(2, moved=True)
        self.assertEqual(self.delivered(), [LIGHTER])

    def test_up_before_the_morning_begins_he_still_gets_it_with_his_greeting(self, *_):
        self.store.create(LIGHTER, morning_of='2026-11-22')
        self.step(1800, moved=True)        # 16:30
        self.step(11 * 3600)               # 03:30, before five
        self.step(2, moved=True)
        self.assertEqual(self.writer_calls, [(11.0, 'night', [LIGHTER])])
        self.assertEqual(self.delivered(), [LIGHTER])
        self.step(2 * 3600, moved=True)    # 05:30, the morning begins and it is not given again
        for _ in range(3):
            self.step(2, moved=True)
        self.assertEqual(self.delivered(), [LIGHTER])
        self.assertEqual(self.announced(), [])

    def test_a_greeting_the_evening_before_leaves_it_for_his_first_input_in_the_morning(self, *_):
        self.store.create(LIGHTER, morning_of='2026-11-22')
        self.step(60, moved=True)          # 16:01
        self.step(7 * 3600)                # 23:01
        self.step(2, moved=True)           # back after seven hours, the evening before
        self.assertEqual(self.writer_calls, [(7.0, 'night', [])])
        self.assertEqual(self.delivered(), [])
        self.step(2 * 3600, moved=True)    # 01:01, then four hours of sleep: too short for a greeting
        self.step(4 * 3600)
        self.step(2, moved=True)           # 05:01
        self.assertEqual(len(self.writer_calls), 1)
        self.assertEqual((self.delivered(), self.announced()), ([LIGHTER], [LIGHTER]))

    def test_a_greeting_he_did_not_hear_leaves_its_reminders_to_be_said(self, *_):
        self.controller.muted = True
        self.store.create('Call the garage', delay_seconds=3600)
        with patch.object(self.controller.closed, 'wait', return_value=False):
            self.step(7 * 3600)
            self.step(2, moved=True)
        self.assertEqual(self.controller.spoken, [])
        self.assertEqual((self.delivered(), self.announced()), (['Call the garage'], ['Call the garage']))

    def test_when_his_input_cannot_be_read_a_due_reminder_is_not_held_back(self, *_):
        self.store.create('Stretch', delay_seconds=60)
        self.wall[0] += 120
        for row in self.store.poll():
            self.controller.waiting_reminders.add(row)
        self.guard.last_input = lambda: (_ for _ in ()).throw(OSError('no input information'))
        with patch.object(self.controller.closed, 'wait', side_effect=[False, False, True]):
            self.guard.run()
        self.assertEqual(self.delivered(), ['Stretch'])


class GreetingWordsTests(unittest.TestCase):
    class Wikiquote:
        def reset_budget(self):
            pass

        def available(self):
            return True

        def source(self, _author, quote):
            return 'Seneca the Younger', quote

    def answer(self, reminders):
        return {'candidates': [{'author': 'Seneca the Younger', 'quote': 'Begin at once to live',
                                'lead': 'Good morning, sir. As Seneca wrote,', 'close': "Let's make a start."}],
                'plain': 'Good morning, sir.', 'reminders': reminders}

    def greet(self, answer, reminders):
        history = QuoteHistory(temporary_folder(self) / 'quotes.json')
        with patch('desktop.learning_reminders.ask', return_value=answer) as ask, patch('desktop.learning_reminders.diagnostic'):
            line = welcome_greeting(9.5, 'morning', 'Sunday', history=history, wikiquote=self.Wikiquote(), reminders=reminders)
        return line, ask.call_args.args[1]

    def test_the_greeting_passes_on_his_reminders_in_jarvis_own_words(self):
        line, details = self.greet(self.answer('Before you head out, sir, the cigarette lighter needs to go in the car.'), [LIGHTER])
        self.assertEqual(details['reminders'], [LIGHTER])
        self.assertEqual(line, "Good morning, sir. As Seneca wrote, “Begin at once to live” Let's make a start. "
                               'Before you head out, sir, the cigarette lighter needs to go in the car.')

    def test_without_reminders_the_greeting_is_unchanged(self):
        line, details = self.greet(self.answer(''), [])
        self.assertEqual(details['reminders'], [])
        self.assertEqual(line, "Good morning, sir. As Seneca wrote, “Begin at once to live” Let's make a start.")

    def test_a_reminder_the_model_leaves_out_is_said_plainly(self):
        for worded in ('Have a splendid day, sir.', ''):
            with self.subTest(worded=worded):
                line, _ = self.greet(self.answer(worded), [LIGHTER])
                self.assertTrue(line.endswith(f'You asked me to remind you: {LIGHTER}.'), line)

    def test_what_counts_as_passing_a_reminder_on(self):
        self.assertTrue(passes_on('Before you go, sir, the cigarette lighter needs to go in the car.', [LIGHTER]))
        self.assertTrue(passes_on('Mind the lighter for the car, sir.', [LIGHTER]))
        self.assertFalse(passes_on('Have a splendid day, sir.', [LIGHTER]))
        self.assertFalse(passes_on('Mind the lighter, sir, and call the garage.', [LIGHTER, 'Book the dentist']))
        self.assertEqual(passed_on([LIGHTER, 'Book the dentist.']),
                         f'You asked me to remind you: {LIGHTER}; and Book the dentist.')

    @patch('desktop.welcome.diagnostic')
    def test_without_the_model_the_greeting_still_carries_the_reminder(self, _log):
        guard = WelcomeBack(FakeController(None), writer=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('offline')),
                            presence=PresenceLog(temporary_folder(self) / 'presence.json'),
                            local_time=lambda: datetime(2026, 11, 22, 7, 0))
        text, part = guard.compose(9 * 3600, [{'text': LIGHTER}])
        self.assertEqual((text, part), (f"Good morning, sir. Let's get to work. You asked me to remind you: {LIGHTER}.", 'morning'))


@patch('desktop.controller.diagnostic')
class ControllerReminderTests(unittest.TestCase):
    def setUp(self):
        folder = temporary_folder(self)
        self.wall = [START.timestamp()]
        self.controller = Controller(queue.Queue())
        self.controller.reminders = Reminders(folder / 'reminders.json', lambda: self.wall[0])
        self.controller.breaks = BreakState(folder / 'break.json')

    def test_jarvis_saves_a_morning_reminder_with_his_tool(self, _log):
        result = self.controller.execute_tool({'capabilities': ['create_reminder']}, 'create_reminder',
                                              {'text': LIGHTER, 'due_at': '', 'delay_seconds': 0, 'morning_of': '2026-11-22',
                                               'repeat': 'none', 'interval_seconds': 0})
        self.assertEqual((result['reminder']['text'], result['reminder']['morning']), (LIGHTER, True))
        self.assertEqual(datetime.fromtimestamp(result['reminder']['due']), datetime(2026, 11, 22, 5, 0))
        self.assertIn(('reminders_changed',), list(self.controller.events.queue))

    def test_the_background_loop_keeps_a_due_reminder_for_when_he_is_here(self, _log):
        row = self.controller.reminders.create('Stretch', delay_seconds=60)['reminder']
        self.wall[0] += 61
        with patch.object(self.controller.closed, 'wait', side_effect=[False, True]), \
             patch.object(self.controller.briefing, 'scheduled_today', return_value=False):
            self.controller.background_loop()
        self.assertEqual(list(self.controller.waiting_reminders.rows), [row['id']])
        self.assertNotIn('reminder_due', [event[0] for event in self.controller.events.queue])
        self.assertEqual(self.controller.pending_announcements, [])


if __name__ == '__main__':
    unittest.main()
