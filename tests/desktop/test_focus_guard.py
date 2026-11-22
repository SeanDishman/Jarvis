import json
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop import focus_guard
from desktop.breaks import BreakState
from desktop.focus_guard import (MESSAGE, AddressReader, AttemptLog, FocusGuard, SessionLog, TabCensus, blocked_site, candidate,
                                 foreground_browser, page_title, title_agrees)
from desktop.lock_in_lines import lock_in_line
from desktop.preferences import Preferences


def line_for(name, attempt, _session, _timing=None):
    return f'{name} is closed, sir. Attempt {attempt}.'


def model_response(text):
    return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


class FakeController:
    def __init__(self, enabled=True):
        self.closed = threading.Event()
        self.breaks = SimpleNamespace(active=False)
        self.settings = {'lockInMode': enabled}
        self.preferences = SimpleNamespace(read=lambda: dict(self.settings))
        self.events = queue.Queue()
        self.muted = False
        self.voice_ready = True
        self.busy = threading.Lock()
        self.cancelled = threading.Event()
        self.speech_cache = {MESSAGE: b'cached voice'}
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)

    def release_response(self):
        self.busy.release()


class FakeActions:
    def __init__(self):
        self.log = []
        self.held = False
        self.failure = None

    def modifiers_held(self):
        return self.held

    def close_tab(self, window, field):
        self.log.append(('close_tab', window['hwnd'], field))
        if self.failure:
            raise self.failure

    def leave_page(self, window, field):
        self.log.append(('leave_page', window['hwnd'], field))


class FakeReader:
    def __init__(self, address='https://x.com/home', editing=False):
        self.address, self.editing, self.reads = address, editing, 0

    def read(self, _window):
        self.reads += 1
        return self.address, self.editing

    def field(self, _window):
        return 'address-field'


class FakeField:
    def __init__(self, value='https://x.com/home', focused=False):
        self.value, self.focused, self.focus_calls = value, focused, 0

    def SetFocus(self):
        self.focus_calls += 1
        self.focused = True


class FakeCensus:
    """Stands in for the browser tab reader: rows are (window handle, sites with a tab open, tabs unreadable)."""

    def __init__(self):
        self.rows, self.learned, self.scans = [], [], 0
        self.tabs = {}  # window handle -> identity of the tab in front; unknown when absent

    def learn(self, window):
        self.learned.append(window['hwnd'])

    def selected(self, window):
        return self.tabs.get(window['hwnd'])

    def scan(self):
        self.scans += 1
        return list(self.rows)


def browser(title='Home / X - Opera', process='opera.exe', hwnd=101):
    return {'hwnd': hwnd, 'pid': 7, 'process': process, 'title': title, 'path': '', 'rect': [-8, -8, 1928, 1040]}


@patch.object(focus_guard, 'LINGER_SECONDS', 0)
@patch.object(focus_guard, 'SILENT_VISIT_SECONDS', 0)
@patch('desktop.focus_guard.diagnostic')
class FocusGuardTests(unittest.TestCase):
    def guard(self, window=None, reader=None, enabled=True):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        voice = patch('phone.voice.synthesize_wav', return_value=b'RIFF')
        voice.start()
        self.addCleanup(voice.stop)
        self.controller = FakeController(enabled)
        self.window = window or browser()
        self.reader = reader or FakeReader()
        self.keyboard = FakeActions()
        self.census = FakeCensus()
        self.now = [100.0]
        self.sessions_path = Path(folder.name) / 'learning-sessions.json'
        return FocusGuard(self.controller, reader=self.reader, foreground=lambda: self.window,
                          actions=self.keyboard, clock=lambda: self.now[0], wall=lambda: self.now[0],
                          attempts=AttemptLog(Path(folder.name) / 'lock-in.json'), liner=line_for,
                          session_log=SessionLog(self.sessions_path), census=self.census)

    def actions(self):
        return [entry[:2] for entry in self.keyboard.log]

    def finish(self, guard):
        thread = guard.voice_thread
        if thread:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        return list(self.controller.events.queue)

    def test_only_x_twitter_and_instagram_addresses_are_blocked(self, _log):
        for address, site in (('https://x.com/home', 'x'), ('x.com', 'x'), ('https://mobile.x.com/i/flow/login', 'x'),
                              ('twitter.com/someone', 'x'), ('https://www.instagram.com/', 'instagram'),
                              ('instagram.com/reels/abc', 'instagram'), ('HTTPS://WWW.X.COM.', 'x')):
            self.assertEqual(blocked_site(address), site, address)
        for address in ('', 'notx.com', 'https://x.co/', 'https://x.com.example.org/', 'https://example.com/?next=x.com',
                        'https://x.com@example.com/', 'x.com tips', 'opera://startpage', 'about:blank',
                        'https://instagramfans.com', 'view-source:https://x.com', 'ftp://x.com/'):
            self.assertIsNone(blocked_site(address), address)

    def test_the_window_title_must_agree_with_the_site(self, _log):
        self.assertTrue(title_agrees('x', 'Home / X - Opera'))
        self.assertTrue(title_agrees('x', '(3) Notifications / X — Mozilla Firefox'))
        self.assertTrue(title_agrees('x', 'x.com/home - Google Chrome'))
        self.assertTrue(title_agrees('x', 'X - Opera'))
        self.assertTrue(title_agrees('instagram', 'Instagram - Opera'))
        self.assertFalse(title_agrees('x', 'Max - Opera'))
        self.assertFalse(title_agrees('x', 'Lecture 1 - YouTube - Opera'))
        self.assertFalse(title_agrees('instagram', 'Home / X - Opera'))
        self.assertTrue(candidate('Lecture 1: Introduction to Power Electronics - YouTube - Opera'))
        self.assertFalse(candidate('Lecture 1: Introduction to Power Electronics - Opera'))

    def test_x_tab_is_closed_then_jarvis_flies_over_and_says_to_lock_in(self, log):
        guard = self.guard()
        self.assertEqual(guard.check(), 'x')
        events = self.finish(guard)
        self.assertEqual(self.actions(), [('close_tab', 101)])
        self.assertEqual(events[0], ('lock_in', {'visit': 1, 'site': 'x', 'name': 'X', 'text': 'X is closed, sir. Attempt 1.',
                                                'attempt': 1, 'rect': [-8, -8, 1928, 1040]}))
        self.assertEqual(events[-1], ('lock_in_finished', 1))
        self.assertEqual(self.controller.spoken, ['X is closed, sir. Attempt 1.'])
        self.assertFalse(self.controller.busy.locked())
        blocked = [call for call in log.call_args_list if call.args[0] == 'lock_in_blocked']
        self.assertEqual(blocked[0].kwargs, {'domain': 'x', 'action': 'close_tab', 'attempts': 1, 'seconds_since_previous': None})
        saved = guard.attempts.path.read_text(encoding='utf-8')
        self.assertEqual(json.loads(saved)['attempts'], 1)
        self.assertNotIn('x.com', saved)
        self.assertNotIn('x.com', json.dumps([call.kwargs for call in log.call_args_list]))

    def test_each_attempt_prepares_its_own_line(self, _log):
        guard = self.guard()
        self.controller.speech_cache = {}
        with patch('phone.voice.synthesize_wav', return_value=b'lock in voice') as synthesize:
            self.assertEqual(guard.check(), 'x')
            self.finish(guard)
            self.now[0] += 10
            self.assertEqual(guard.check(), 'x')
            self.finish(guard)
        self.assertEqual([call.args[0] for call in synthesize.call_args_list],
                         ['X is closed, sir. Attempt 1.', 'X is closed, sir. Attempt 2.'])
        self.assertEqual(self.controller.speech_cache['X is closed, sir. Attempt 1.'], b'lock in voice')
        self.assertEqual(self.controller.spoken, ['X is closed, sir. Attempt 1.', 'X is closed, sir. Attempt 2.'])
        self.assertEqual(guard.attempts.read(), 2)

    def test_instagram_is_blocked_too(self, _log):
        guard = self.guard(browser('Instagram - Google Chrome', 'chrome.exe'), FakeReader('https://www.instagram.com/'))
        self.assertEqual(guard.check(), 'instagram')
        self.finish(guard)
        self.assertEqual(self.actions(), [('close_tab', 101)])
        self.assertEqual(self.controller.spoken, ['Instagram is closed, sir. Attempt 1.'])

    def test_learning_sites_show_and_speak_one_model_reply_per_session(self, _log):
        guard = self.guard(browser('(2) Lesson 4: Fourier series - YouTube - Opera'), FakeReader('https://m.youtube.com/watch?v=1'))
        guard.grant_pass()
        with patch.object(guard, 'reminder_writer', side_effect=['First model result.', 'Second model result.']) as writer:
            self.assertIsNone(guard.check())
            guard.reminder_worker.join(2)
            self.assertEqual(guard.check(), 'youtube')
            self.finish(guard)
            self.reader.address = 'https://www.youtube.com/watch?v=2'
            self.assertIsNone(guard.check())
            self.reader.editing = True
            self.assertIsNone(guard.check())
            self.reader.editing = False
            self.assertIsNone(guard.check())
            self.window['title'] = 'Feed | LinkedIn - Opera'
            self.reader.address = 'https://www.linkedin.com/feed'
            self.assertIsNone(guard.check())
            guard.reminder_worker.join(2)
            self.assertEqual(guard.check(), 'linkedin')
            events = self.finish(guard)
        # The model hears what the tab shows, never the address.
        self.assertEqual([call.args for call in writer.call_args_list],
                         [('YouTube', 'Lesson 4: Fourier series'), ('LinkedIn', 'Feed')])
        rows = [row[1] for row in events if row[0] == 'lock_in']
        self.assertEqual([row['text'] for row in rows], self.controller.spoken)
        self.assertEqual(self.controller.spoken, ['First model result.', 'Second model result.'])
        self.assertTrue(all(row['learning'] and 'attempt' not in row for row in rows))
        self.assertEqual(self.census.learned[0], 101)
        self.assertEqual(self.actions(), [])
        self.assertEqual(guard.attempts.read(), 0)
        self.assertEqual(guard.pass_status()['state'], 'waiting')

    def test_slow_generation_cannot_delay_blocking_or_announce_a_visit_after_leaving(self, _log):
        guard = self.guard(browser('Feed | LinkedIn'), FakeReader('https://linkedin.com/feed'))
        release = threading.Event()
        self.addCleanup(release.set)
        with patch.object(guard, 'reminder_writer', side_effect=lambda *_: (release.wait(3), 'Late reply.')[1]):
            guard.check()
            self.window['title'], self.reader.address = 'Home / X - Opera', 'https://x.com'
            self.assertEqual(guard.check(), 'x')
            release.set()
            guard.reminder_worker.join(2)
            events = self.finish(guard)
        self.assertEqual(self.actions(), [('close_tab', 101)])
        self.assertFalse(any(row[0] == 'lock_in' and row[1].get('learning') for row in events))

    def arrive(self, guard, site, title, address, hwnd=101):
        self.window, self.reader.address = browser(title, hwnd=hwnd), address
        guard.check()
        guard.reminder_worker.join(2)
        self.assertEqual(guard.check(), site)
        self.finish(guard)

    def away(self, guard, seconds, rows, step=30):
        """Somewhere other than the browser for a while, with the given tabs open in the background."""
        self.census.rows, self.window = rows, None
        for _ in range(int(seconds // step)):
            self.now[0] += step
            guard.check()

    def test_leaving_youtube_open_never_repeats_the_reminder(self, _log):
        guard = self.guard(browser('YouTube - Opera'), FakeReader('https://youtube.com/'))
        with patch.object(guard, 'reminder_writer', side_effect=['YouTube first.', 'LinkedIn first.', 'YouTube next.']) as writer:
            self.arrive(guard, 'youtube', 'YouTube - Opera', 'https://youtube.com/')
            # A video left playing in a background tab while he works elsewhere for hours, checking back now and then.
            for _ in range(6):
                self.away(guard, 25 * 60, [(101, {'youtube'}, False)])
                self.window, self.reader.address = browser('Another video - YouTube - Opera'), 'https://youtube.com/watch?v=2'
                self.assertIsNone(guard.check())
            # Flicking between apps, tabs and browser windows.
            for index in range(20):
                self.window = browser('Discord', 'discord.exe', 300) if index % 2 else None
                guard.check()
                self.now[0] += 1
                self.window = browser('Shorts - YouTube - Opera', hwnd=1000 + index)
                self.reader.address = 'https://youtube.com/shorts/1'
                self.assertIsNone(guard.check())
            self.arrive(guard, 'linkedin', 'Feed | LinkedIn - Opera', 'https://linkedin.com/feed')
            self.away(guard, 10 * 60, [(101, {'youtube', 'linkedin'}, False)])
            self.window, self.reader.address = browser('YouTube - Opera'), 'https://youtube.com'
            self.assertIsNone(guard.check())
            self.assertEqual(writer.call_count, 2)
            # Every YouTube tab closed, then reopened within the grace period: still the same session.
            self.away(guard, focus_guard.LEARNING_CLOSED_SECONDS - 120, [(101, {'linkedin'}, False)])
            self.window, self.reader.address = browser('YouTube - Opera'), 'https://youtube.com'
            self.assertIsNone(guard.check())
            self.assertEqual(writer.call_count, 2)
            # Closed for longer than that: the next visit is a new session with one new reminder.
            self.away(guard, focus_guard.LEARNING_CLOSED_SECONDS + 60, [(101, {'linkedin'}, False)])
            self.assertNotIn('youtube', guard.sessions)
            self.assertIn('linkedin', guard.sessions)
            self.arrive(guard, 'youtube', 'YouTube - Opera', 'https://youtube.com/')
        self.assertEqual(self.controller.spoken, ['YouTube first.', 'LinkedIn first.', 'YouTube next.'])
        self.assertGreater(self.census.scans, 20)
        self.assertEqual(self.actions(), [])

    def test_unreadable_tabs_keep_the_session_only_while_its_window_exists(self, _log):
        guard = self.guard(browser('YouTube - Opera'), FakeReader('https://youtube.com/'))
        with patch.object(guard, 'reminder_writer', return_value='Only once.') as writer:
            self.arrive(guard, 'youtube', 'YouTube - Opera', 'https://youtube.com/')
            self.away(guard, focus_guard.LEARNING_CLOSED_SECONDS * 2, [(101, set(), True), (555, set(), True)])
            self.assertIn('youtube', guard.sessions)
            broken = guard.census.scan
            guard.census.scan = lambda: (_ for _ in ()).throw(RuntimeError('UI Automation is unavailable'))
            self.away(guard, focus_guard.LEARNING_CLOSED_SECONDS * 2, [])
            self.assertIn('youtube', guard.sessions)
            guard.census.scan = broken
            # The window that showed YouTube has closed, and the other window's tabs say nothing.
            self.away(guard, focus_guard.LEARNING_CLOSED_SECONDS + 60, [(555, set(), True)])
            self.assertNotIn('youtube', guard.sessions)
        self.assertEqual(writer.call_count, 1)

    def test_a_tab_left_open_but_unused_for_hours_starts_a_new_session(self, _log):
        guard = self.guard(browser('YouTube - Opera'), FakeReader('https://youtube.com/'))
        with patch.object(guard, 'reminder_writer', side_effect=['Morning.', 'Evening.']):
            self.arrive(guard, 'youtube', 'YouTube - Opera', 'https://youtube.com/')
            self.away(guard, focus_guard.LEARNING_IDLE_SECONDS - 600, [(101, {'youtube'}, False)], step=600)
            self.window = browser('YouTube - Opera')
            self.assertIsNone(guard.check())
            self.away(guard, focus_guard.LEARNING_IDLE_SECONDS + 600, [(101, {'youtube'}, False)], step=600)
            self.arrive(guard, 'youtube', 'YouTube - Opera', 'https://youtube.com/')
        self.assertEqual(self.controller.spoken, ['Morning.', 'Evening.'])

    def test_restarting_jarvis_keeps_the_session_without_storing_the_page(self, _log):
        guard = self.guard(browser('Secret project - YouTube - Opera'), FakeReader('https://youtube.com/watch?v=abc'))
        with patch.object(guard, 'reminder_writer', return_value='Before the restart.'):
            self.arrive(guard, 'youtube', 'Secret project - YouTube - Opera', 'https://youtube.com/watch?v=abc')
        stored = self.sessions_path.read_text(encoding='utf-8')
        self.assertEqual(set(json.loads(stored)['sessions']), {'youtube'})
        for private in ('Secret', 'watch', 'abc', 'youtube.com'):
            self.assertNotIn(private, stored)

        def restarted():
            return FocusGuard(self.controller, reader=self.reader, foreground=lambda: self.window, actions=self.keyboard,
                              clock=lambda: self.now[0], wall=lambda: self.now[0], attempts=guard.attempts, liner=line_for,
                              session_log=SessionLog(self.sessions_path), census=FakeCensus())

        self.now[0] += 300
        again = restarted()
        with patch.object(again, 'reminder_writer') as writer:
            self.assertIsNone(again.check())
        writer.assert_not_called()
        # Jarvis was off for longer than the site may stay closed, so nothing says the tab stayed open.
        self.now[0] += focus_guard.LEARNING_CLOSED_SECONDS + 1
        later = restarted()
        with patch.object(later, 'reminder_writer', return_value='New session.'):
            later.check()
            later.reminder_worker.join(2)
            self.assertEqual(later.check(), 'youtube')

    def test_failed_model_retries_without_speaking_scripted_text(self, _log):
        guard = self.guard(browser('YouTube'), FakeReader('https://youtube.com'))
        with patch.object(guard, 'reminder_writer', side_effect=[RuntimeError('offline'), 'Generated after retry.']) as writer:
            guard.check()
            guard.reminder_worker.join(2)
            guard.check()
            self.assertTrue(self.controller.events.empty())
            self.assertFalse(self.controller.spoken)
            self.now[0] += focus_guard.LEARNING_RETRY_SECONDS + 1
            guard.check()
            guard.reminder_worker.join(2)
            guard.check()
            self.finish(guard)
        self.assertEqual(self.controller.spoken, ['Generated after retry.'])
        self.assertEqual([call.args for call in writer.call_args_list], [('YouTube', '')] * 2)

    def test_a_reminder_not_ready_soon_after_arriving_is_dropped_for_the_session(self, _log):
        guard = self.guard(browser('YouTube'), FakeReader('https://youtube.com'))
        with patch.object(guard, 'reminder_writer', side_effect=RuntimeError('offline')) as writer:
            guard.check()
            while self.now[0] - 100 <= focus_guard.LEARNING_DELIVERY_SECONDS:
                guard.reminder_worker.join(2)
                self.now[0] += focus_guard.LEARNING_RETRY_SECONDS + 1
                guard.check()
            tries = writer.call_count
            guard.reminder_writer = lambda *_: 'Far too late.'
            for _ in range(5):
                self.now[0] += focus_guard.LEARNING_RETRY_SECONDS + 1
                guard.check()
        self.assertGreater(tries, 2)
        self.assertTrue(guard.sessions['youtube'].reminded)
        self.assertFalse(self.controller.spoken)
        self.assertTrue(self.controller.events.empty())

    def test_learning_reminders_require_the_loaded_site_and_enabled_mode(self, _log):
        guard = self.guard(browser('YouTube'), FakeReader('https://youtube.com.example.org/'))
        with patch.object(guard, 'reminder_writer') as writer:
            for address in ('https://www.example.org/?site=youtube.com', 'https://youtube.com@example.org/', 'https://youtu.be.example.com'):
                self.reader.address = address
                guard.check()
            self.reader.address, self.reader.editing = 'https://youtu.be/abc', True
            guard.check()
            self.reader.editing = False
            self.controller.settings['lockInMode'] = False
            guard.check()
            self.controller.settings['lockInMode'] = self.controller.breaks.active = True
            guard.check()
        writer.assert_not_called()
        self.assertIsNone(guard.reminder_worker)
        self.assertEqual(self.actions(), [])

    def test_typing_an_address_is_never_blocked(self, _log):
        guard = self.guard(browser('x.com - Opera'), FakeReader('x.com', editing=True))
        self.assertIsNone(guard.check())
        self.assertEqual(self.actions(), [])
        self.assertTrue(self.controller.events.empty())

    def test_typed_text_left_in_the_address_bar_needs_a_matching_page(self, _log):
        guard = self.guard(browser('Inbox (3) - Gmail - Opera'), FakeReader('x.com'))
        self.assertIsNone(guard.check())
        self.assertEqual(self.reader.reads, 0)
        guard = self.guard(browser('Twitter alternatives - Google Search - Opera'), FakeReader('https://www.google.com/search?q=x.com'))
        self.assertIsNone(guard.check())
        self.assertEqual(self.actions(), [])

    def test_lock_in_off_or_a_break_skips_the_check(self, _log):
        guard = self.guard(enabled=False)
        self.assertIsNone(guard.check())
        self.assertEqual(self.reader.reads, 0)
        guard = self.guard()
        self.controller.breaks.active = True
        self.assertIsNone(guard.check())
        self.assertEqual((self.reader.reads, self.actions()), (0, []))

    def test_held_modifiers_or_a_focus_change_postpone_the_block(self, _log):
        guard = self.guard()
        self.keyboard.held = True
        self.assertIsNone(guard.check())
        self.keyboard.held = False
        windows = iter([self.window, browser('Notes - Notepad', 'notepad.exe', 202)])
        guard.foreground = lambda: next(windows)
        self.assertIsNone(guard.check())
        self.assertEqual(self.actions(), [])
        guard.foreground = lambda: self.window
        self.assertEqual(guard.check(), 'x')
        self.finish(guard)

    def test_a_tab_that_stays_open_is_sent_to_a_blank_page(self, _log):
        guard = self.guard()
        self.assertEqual(guard.check(), 'x')
        self.now[0] += .3
        self.assertIsNone(guard.check())
        self.now[0] += 1
        self.assertEqual(guard.check(), 'x')
        self.now[0] += 10
        self.assertEqual(guard.check(), 'x')
        self.finish(guard)
        self.assertEqual(self.actions(), [('close_tab', 101), ('leave_page', 101), ('close_tab', 101)])

    def test_a_failed_block_is_logged_and_the_next_try_waits(self, log):
        guard = self.guard()
        self.keyboard.failure = RuntimeError('The browser lost focus')
        self.assertIsNone(guard.check())
        self.assertTrue(self.controller.events.empty())
        self.assertEqual(log.call_args_list[-1].args[0], 'lock_in_failed')
        self.keyboard.failure = None
        self.now[0] += .3
        self.assertIsNone(guard.check())
        self.now[0] += 1
        self.assertEqual(guard.check(), 'x')
        self.finish(guard)
        self.assertEqual(self.actions(), [('close_tab', 101), ('leave_page', 101)])
        self.assertEqual(self.keyboard.log[-1][2], 'address-field')

    def test_muted_or_busy_jarvis_still_blocks_without_speaking(self, _log):
        guard = self.guard()
        self.controller.muted = True
        self.assertEqual(guard.check(), 'x')
        events = self.finish(guard)
        self.assertEqual([event[0] for event in events], ['lock_in', 'lock_in_finished'])
        guard = self.guard()
        self.controller.busy.acquire()
        self.assertEqual(guard.check(), 'x')
        self.finish(guard)
        self.assertEqual(self.controller.spoken, [])
        self.assertTrue(self.controller.busy.locked())

    def test_repeat_visits_each_get_a_line(self, _log):
        guard = self.guard()
        release = threading.Event()
        self.controller.speak = lambda text: (self.controller.spoken.append(text), release.wait(2))
        self.assertEqual(guard.check(), 'x')
        self.now[0] += 10
        self.assertEqual(guard.check(), 'x')
        release.set()
        events = self.finish(guard)
        self.assertEqual(self.controller.spoken, ['X is closed, sir. Attempt 1.', 'X is closed, sir. Attempt 2.'])
        self.assertEqual([event[1]['visit'] for event in events if event[0] == 'lock_in'], [1, 2])
        self.assertEqual([event[1]['attempt'] for event in events if event[0] == 'lock_in'], [1, 2])
        self.assertEqual(events[-1], ('lock_in_finished', 2))

    def test_only_foreground_browsers_are_considered(self, _log):
        rows = {1: browser('Home / X - Opera'), 2: browser('x.com notes', 'notepad.exe', 2)}
        for hwnd, expected in ((1, 'opera.exe'), (2, None)):
            with patch('desktop.focus_guard.USER.GetForegroundWindow', return_value=hwnd), \
                    patch('desktop.focus_guard.window_info', side_effect=lambda handle: rows[handle]):
                found = foreground_browser()
            self.assertEqual(found and found['process'], expected)

    def test_address_reader_caches_fields_and_retries_missing_ones(self, _log):
        now = [0.0]
        found = []
        def finder(window):
            found.append((window['hwnd'], window['process']))
            return None if len(found) == 1 else 'field'
        reader = AddressReader(finder=finder, reader=lambda field: ('https://x.com/', False), clock=lambda: now[0])
        window = browser()
        self.assertIsNone(reader.read(window))
        self.assertIsNone(reader.read(window))
        now[0] += focus_guard.MISS_SECONDS + .1
        self.assertEqual(reader.read(window), ('https://x.com/', False))
        self.assertEqual(reader.read(window), ('https://x.com/', False))
        self.assertEqual(found, [(101, 'opera.exe'), (101, 'opera.exe')])
        reader.reader = lambda field: (_ for _ in ()).throw(RuntimeError('gone'))
        self.assertIsNone(reader.read(window))
        self.assertEqual(reader.fields, {})


class AddressLookupTests(unittest.TestCase):
    def element(self, kind=focus_guard.UIA_EDIT, pid=7, cls='AddressTextfieldView', automation_id='', name='Address field'):
        return SimpleNamespace(CurrentControlType=kind, CurrentProcessId=pid, CurrentClassName=cls,
                               CurrentAutomationId=automation_id, CurrentName=name)

    def test_only_this_browsers_address_field_counts(self):
        matches = focus_guard.Automation.matches
        self.assertTrue(matches(self.element(), 7))
        self.assertTrue(matches(self.element(cls='OmniboxViewViews', name='Address and search bar'), 7))
        self.assertTrue(matches(self.element(cls='', automation_id='urlbar-input', name=''), 7))
        self.assertFalse(matches(self.element(pid=8), 7))
        self.assertFalse(matches(self.element(kind=50030), 7))
        self.assertFalse(matches(self.element(cls='Search', name='Search the web'), 7))
        self.assertFalse(matches(None, 7))

    def test_the_toolbar_band_only_counts_points_on_this_window(self):
        automation = focus_guard.Automation.__new__(focus_guard.Automation)
        probed = []
        def element_at(point):
            probed.append(point)
            return self.element() if point[1] >= 140 else self.element(kind=50033, cls='ToolbarViewGx', name='')
        automation.client = SimpleNamespace(ElementFromPoint=element_at)
        automation.module = SimpleNamespace(tagPOINT=lambda x, y: (x, y))
        covered = {(100 + round(1000 * .35), 124)}
        user = SimpleNamespace(GetDpiForWindow=lambda _hwnd: 96, GetAncestor=lambda hwnd, _flag: hwnd,
                               WindowFromPoint=lambda point: 999 if (point.x, point.y) in covered else 101)
        window = {**browser(), 'rect': [100, 100, 1100, 900]}
        with patch('desktop.focus_guard.USER', user):
            found = automation.field_near_top(window)
        self.assertEqual(found.CurrentClassName, 'AddressTextfieldView')
        self.assertNotIn((450, 124), probed)
        self.assertEqual(probed[-1], (450, 140))
        self.assertTrue(all(y < 140 or (x, y) == (450, 140) for x, y in probed))

    def test_lookup_tries_focus_then_the_toolbar_band_then_the_tree(self):
        automation = focus_guard.Automation.__new__(focus_guard.Automation)
        calls = []
        def strategy(name, result):
            def run(_window):
                calls.append(name)
                if isinstance(result, Exception):
                    raise result
                return result
            return run
        automation.focused_field = strategy('focus', None)
        automation.field_near_top = strategy('toolbar', RuntimeError('0x80040201'))
        automation.walk = strategy('tree', 'field')
        self.assertEqual(automation.find_address_field(browser()), 'field')
        self.assertEqual(calls, ['focus', 'toolbar', 'tree'])
        calls.clear()
        automation.field_near_top = strategy('toolbar', 'toolbar field')
        self.assertEqual(automation.find_address_field(browser()), 'toolbar field')
        self.assertEqual(calls, ['focus', 'toolbar'])


def node(role, name='', kids=(), broken=False):
    def role_of(_child):
        if broken:
            raise OSError('0x80004005')
        return role
    return SimpleNamespace(accRole=role_of, accName=lambda _child: name, kids=list(kids))


def kids_of(element):
    return element.kids


GROUPING, PANE, BUTTON = 0x14, 0x10, 0x2B


class TabReadingTests(unittest.TestCase):
    def setUp(self):
        tab, tab_list = focus_guard.ROLE_PAGE_TAB, focus_guard.ROLE_PAGE_TAB_LIST
        self.strip = node(tab_list, kids=[node(GROUPING, kids=[node(tab, 'Grouped - YouTube')]), node(tab, 'Lecture - YouTube'),
                                          node(tab, 'Feed | LinkedIn'), node(BUTTON, 'New tab')])
        # A web page with its own tab list comes first, and must never be mistaken for the browser's tabs.
        self.page = node(focus_guard.ROLE_DOCUMENT, kids=[node(tab_list, kids=[node(tab, 'Videos')])])
        self.root = node(PANE, kids=[self.page, node(PANE, kids=[node(PANE, broken=True), node(GROUPING, kids=[self.strip])])])

    def test_the_browser_tab_strip_is_found_without_looking_inside_web_pages(self):
        self.assertIs(focus_guard.find_strip(self.root, kids_of), self.strip)

    def test_a_tab_list_without_tabs_is_searched_through(self):
        # Chrome's vertical tab strip container is a tab list too; the tabs sit in another list inside it.
        container = node(focus_guard.ROLE_PAGE_TAB_LIST, kids=[node(PANE, kids=[node(PANE, kids=[self.strip])]),
                                                               node(0x33, 'Vertical Tab Strip Resize Handle')])
        self.assertIs(focus_guard.find_strip(node(PANE, kids=[container]), kids_of), self.strip)

    def test_every_tab_is_listed_including_grouped_ones(self):
        self.assertEqual(focus_guard.strip_titles(self.strip, kids_of), ['Grouped - YouTube', 'Lecture - YouTube', 'Feed | LinkedIn'])

    def test_the_tab_in_front_is_identified_including_inside_groups(self):
        def page_tab(name, selected=False):
            return SimpleNamespace(accRole=lambda _child: focus_guard.ROLE_PAGE_TAB, accName=lambda _child: name, kids=[],
                                   accState=lambda _child: 0x3200000 | (focus_guard.STATE_SYSTEM_SELECTED if selected else 0))
        group = node(GROUPING, kids=[page_tab('Home / X', selected=True)])
        strip = node(focus_guard.ROLE_PAGE_TAB_LIST, kids=[page_tab('Inbox'), group, node(BUTTON, 'New tab')])
        name = lambda tab: tab.accName(0)
        self.assertEqual(focus_guard.selected_tab(strip, kids_of, identify=name), 'Home / X')
        group.kids[0] = page_tab('Home / X')
        self.assertIsNone(focus_guard.selected_tab(strip, kids_of, identify=name))

    def test_the_search_is_bounded_and_survives_broken_nodes(self):
        deep = node(PANE)
        for _ in range(50):
            deep = node(PANE, kids=[deep, node(PANE, broken=True)])
        self.assertIsNone(focus_guard.find_strip(deep, kids_of, budget=30))
        self.assertIsNone(focus_guard.find_strip(node(PANE, broken=True), kids_of))


class TabCensusTests(unittest.TestCase):
    def test_tab_titles_show_which_learning_sites_are_still_open(self):
        now = [0.0]
        windows = [browser('Inbox - Opera', hwnd=1), browser('Docs — Mozilla Firefox', 'firefox.exe', 2),
                   browser('Lecture - YouTube - Opera', hwnd=3)]
        tabs = {1: ['Inbox', '(3) Lecture 3 - YouTube', 'Feed | LinkedIn', 'Home / X'], 2: None, 3: ['Lecture - YouTube']}
        found = []
        def finder(window):
            found.append(window['hwnd'])
            return None if tabs[window['hwnd']] is None else window['hwnd']
        census = TabCensus(windows=lambda: windows, finder=finder, lister=lambda strip: tabs[strip], clock=lambda: now[0])
        self.assertEqual(census.scan(), [(1, {'youtube', 'linkedin'}, False), (2, set(), True), (3, {'youtube'}, False)])
        census.scan()
        self.assertEqual(found, [1, 2, 3])
        now[0] += focus_guard.STRIP_LEARN_SECONDS
        census.learn(windows[1])
        self.assertEqual(found, [1, 2, 3, 2])
        census.scan()
        self.assertEqual(found, [1, 2, 3, 2])
        now[0] += focus_guard.STRIP_RETRY_SECONDS
        census.scan()
        self.assertEqual(found, [1, 2, 3, 2, 2])

    def test_the_tab_in_front_is_read_from_the_cached_strip(self):
        found, picks = [], [('tab', 1), RuntimeError('tab gone')]
        def selector(_strip):
            pick = picks.pop(0)
            if isinstance(pick, Exception):
                raise pick
            return pick
        census = TabCensus(windows=lambda: [], finder=lambda window: found.append(window['hwnd']) or 'strip',
                           selector=selector, clock=lambda: 0.0)
        self.assertEqual(census.selected(browser(hwnd=5)), ('tab', 1))
        self.assertIsNone(census.selected(browser(hwnd=5)), 'An unreadable tab is unknown, not a different tab')
        self.assertEqual((found, census.strips), ([5], {}), 'A broken strip is dropped and looked up again later')

    def test_a_broken_or_empty_strip_is_dropped_and_the_window_counts_as_unreadable(self):
        now = [0.0]
        results = [RuntimeError('element gone'), []]
        def lister(_strip):
            result = results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        census = TabCensus(windows=lambda: [browser('YouTube - Opera', hwnd=1)], finder=lambda _window: 'strip',
                           lister=lister, clock=lambda: now[0])
        for _ in range(2):
            self.assertEqual(census.scan(), [(1, {'youtube'}, True)])
            self.assertEqual(census.strips, {})
            now[0] += focus_guard.STRIP_RETRY_SECONDS
        self.assertEqual(results, [])


class PageTitleTests(unittest.TestCase):
    def test_only_what_the_tab_shows_reaches_the_reminder(self):
        for site, title, page in (('youtube', '(3) How engines work - YouTube - Opera', 'How engines work'),
                                  ('youtube', 'YouTube - Opera', ''), ('youtube', '(12) YouTube', ''),
                                  ('youtube', 'Lecture 1 - YouTube — Mozilla Firefox', 'Lecture 1'),
                                  ('youtube', 'Lecture 1 - YouTube - Google Chrome', 'Lecture 1'),
                                  ('youtube', 'youtube - Google Search - Opera', ''),
                                  ('linkedin', '(5) Feed | LinkedIn - Opera', 'Feed'), ('linkedin', 'Jobs | LinkedIn', 'Jobs')):
            with self.subTest(title=title):
                self.assertEqual(page_title(site, title), page)


class BrowserActionTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.foreground, self.alive = 101, True
        user = SimpleNamespace(GetForegroundWindow=lambda: self.foreground, IsWindow=lambda _hwnd: self.alive)
        for patcher in (patch('desktop.focus_guard.USER', user),
                        patch('desktop.focus_guard.send_inputs', side_effect=lambda *events: self.sent.extend(events))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = browser()

    def keys(self):
        rows = []
        for event in self.sent:
            ki = event.data.ki
            if ki.dwFlags & 2:
                continue
            rows.append(chr(ki.wScan) if ki.dwFlags & 4 else ki.wVk)
        return rows

    def actions(self, field, values):
        reads = iter(values)
        def read(_field):
            value = next(reads, None)
            return value if value is not None else (field.value, field.focused)
        return focus_guard.BrowserActions(read=read, sleep=lambda _seconds: None)

    def test_closing_a_tab_hands_the_address_bar_focus_once_the_tab_is_gone(self):
        field = FakeField()
        self.actions(field, [('https://x.com/home', False), ('https://docs.example/', False)]).close_tab(self.window, field)
        self.assertEqual(self.keys(), [focus_guard.VK_CONTROL, ord('W')])
        self.assertEqual(field.focus_calls, 1)
        field = FakeField()
        self.alive = False
        self.sent.clear()
        self.actions(field, []).close_tab(self.window, field)
        self.assertEqual((self.keys(), field.focus_calls), ([focus_guard.VK_CONTROL, ord('W')], 0))

    def test_closing_refuses_when_the_browser_is_not_in_front(self):
        self.foreground = 999
        with self.assertRaisesRegex(RuntimeError, 'lost focus'):
            self.actions(FakeField(), []).close_tab(self.window, FakeField())
        self.assertEqual(self.sent, [])

    def test_leaving_a_page_never_types_unless_the_address_bar_has_focus(self):
        field = FakeField()
        field.SetFocus = lambda: None
        with self.assertRaisesRegex(RuntimeError, 'did not take focus'):
            self.actions(field, []).leave_page(self.window, field)
        self.assertEqual(self.sent, [])

    def test_leaving_a_page_presses_enter_only_for_an_exact_blank_address(self):
        field = FakeField()
        self.actions(field, [('https://x.com/home', True), ('about:blank', True)]).leave_page(self.window, field)
        self.assertEqual(self.keys(), [focus_guard.VK_CONTROL, ord('A'), *'about:blank', focus_guard.VK_RETURN])
        self.sent.clear()
        field = FakeField()
        with self.assertRaisesRegex(RuntimeError, 'blank page'):
            self.actions(field, [('https://x.com/home', True)] + [('about:blankx', True)] * 40).leave_page(self.window, field)
        self.assertNotIn(focus_guard.VK_RETURN, self.keys())
        self.assertEqual(self.keys()[-1], focus_guard.VK_ESCAPE)


class LockInLineTests(unittest.TestCase):
    def test_a_model_line_is_spoken_instead_of_the_stock_scolding(self):
        with patch('desktop.lock_in_lines.local_request', return_value=model_response('X is shut, sir. Back to the bench.')) as request:
            self.assertEqual(lock_in_line('X', 4, 'lockin-test'), 'X is shut, sir. Back to the bench.')
        body = request.call_args.args[2]
        self.assertEqual(body['model'], 'openclaw/lockin')
        self.assertEqual(json.loads(body['input'])['attempt'], 4)
        self.assertNotIn(MESSAGE, body['input'])

    def test_a_stock_or_broken_line_falls_back_with_the_attempt_count(self):
        for text in ('', MESSAGE, 'You need to lock in.', 'too long ' * 30, 'See https://x.com', 'Back to `work`, sir.'):
            with self.subTest(text=text), patch('desktop.lock_in_lines.local_request', return_value=model_response(text)):
                line = lock_in_line('Instagram', 3, 'lockin-test')
            self.assertEqual(line, 'That is attempt 3, sir. Instagram stays closed.')
            self.assertNotIn('http', line)

    def test_the_attempt_count_survives_a_restart_without_storing_an_address(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lock-in.json'
            self.assertEqual(AttemptLog(path).record(), 1)
            self.assertEqual(AttemptLog(path).record(), 2)
            self.assertEqual(AttemptLog(path).read(), 2)
            self.assertNotIn('http', path.read_text(encoding='utf-8'))

    def test_each_attempt_knows_when_the_earlier_ones_were(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lock-in.json'
            path.write_text(json.dumps({'attempts': 36}), encoding='utf-8')  # a log from before attempts had times
            now = [1_800_000_000.0]
            log = AttemptLog(path, wall=lambda: now[0])
            self.assertEqual(log.record('instagram'), 37, 'The lifetime count carries on')
            self.assertEqual(log.timing()['previousAttempt'], 'none before this one')
            now[0] += 2 * 86400
            log.record('x')
            self.assertEqual((log.timing()['previousAttempt'], log.timing()['attemptsInLastTenMinutes']), ('2 days ago', 1))
            now[0] += 15
            log.record('x')
            timing = log.timing()
            self.assertEqual((timing['previousAttempt'], timing['previousAttemptOnThisSite']), ('15 seconds ago', '15 seconds ago'))
            self.assertEqual((timing['secondsSincePrevious'], timing['attemptsInLastTenMinutes'], timing['previousSite']), (15, 2, 'X'))
            self.assertEqual(AttemptLog(path).read(), 39)

    def test_the_line_hears_when_this_attempt_came_and_a_quick_retry_is_called_out(self):
        timing = {'previousAttempt': '15 seconds ago', 'attemptsInLastTenMinutes': 2, 'secondsSincePrevious': 15, 'localTime': 'Monday 2:41 AM'}
        with patch('desktop.lock_in_lines.local_request', return_value=model_response('Again already, sir? X stays shut.')) as request:
            lock_in_line('X', 5, 'lockin-test', timing)
        sent = json.loads(request.call_args.args[2]['input'])['timing']
        self.assertEqual(sent, {'previousAttempt': '15 seconds ago', 'attemptsInLastTenMinutes': 2, 'localTime': 'Monday 2:41 AM'},
                         'Spoken timing goes to the model, exact seconds do not')
        with patch('desktop.lock_in_lines.local_request', side_effect=OSError('offline')):
            self.assertIn('moment ago', lock_in_line('X', 5, 'lockin-test', timing))
            self.assertEqual(lock_in_line('X', 5, 'lockin-test', {**timing, 'secondsSincePrevious': 7200}),
                             'That is attempt 5, sir. X stays closed.')


class LockInSettingTests(unittest.TestCase):
    def test_lock_in_mode_defaults_on_and_only_accepts_on_or_off(self):
        with tempfile.TemporaryDirectory() as folder:
            prefs = Preferences(Path(folder) / 'settings.json')
            self.assertIs(prefs.read()['lockInMode'], True)
            self.assertIs(prefs.save({'lockInMode': False})['lockInMode'], False)
            self.assertIs(prefs.read()['lockInMode'], False)
            with self.assertRaisesRegex(ValueError, 'Lock in mode'):
                prefs.save({'lockInMode': 'yes'})
            self.assertIs(prefs.read()['lockInMode'], False)


class LockInDesktopTests(unittest.TestCase):
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

    def test_controller_owns_the_guard_without_slowing_voice_warmup(self):
        controller = self.orb.controller
        self.assertIsInstance(controller.focus_guard, FocusGuard)
        self.assertIsNone(controller.focus_guard.thread)
        with patch('phone.voice.speech_voice'), patch('phone.voice.synthesize_wav', return_value=b'voice') as synthesize, \
                patch.object(controller.apps, 'refresh'):
            controller.warmup()
        self.assertNotIn(MESSAGE, [call.args[0] for call in synthesize.call_args_list])
        self.assertTrue(controller.voice_ready)

    def test_orb_flies_to_the_browser_shows_the_card_and_returns_after_the_latest_visit(self):
        row = {'visit': 1, 'site': 'x', 'name': 'X', 'text': MESSAGE, 'rect': [0, 0, 1920, 1040]}
        self.orb.events.put(('lock_in', row))
        self.orb.events.put(('lock_in', {**row, 'visit': 2}))
        self.orb.events.put(('lock_in_finished', 1))
        self.orb.step()
        self.assertEqual(self.sent.count(('lockin', {'site': 'x', 'name': 'X', 'text': MESSAGE})), 2)
        self.assertEqual(self.requests, [('visit_orb', {'rect': [0, 0, 1920, 1040], 'holdMs': 20000})] * 2)
        self.orb.events.put(('lock_in_finished', 2))
        self.orb.step()
        self.assertEqual(self.requests[-1], ('return_orb', {}))
        self.assertEqual(len(self.requests), 3)

    def test_hidden_jarvis_does_not_reveal_the_orb(self):
        self.orb.ui_hidden = True
        self.orb.sync_break_visibility()
        self.orb.events.put(('lock_in', {'visit': 1, 'site': 'x', 'name': 'X', 'text': MESSAGE, 'rect': [0, 0, 10, 10]}))
        self.orb.step()
        self.assertTrue(self.orb.windows_hidden)
        self.assertEqual(self.requests, [])
        self.assertNotIn('lockin', [name for name, _data in self.sent])


if __name__ == '__main__':
    unittest.main()
