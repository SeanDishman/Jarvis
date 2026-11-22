from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop import focus_guard
from desktop.focus_guard import PASS_AWAY_SECONDS, PASS_WAIT_SECONDS, AttemptLog, FocusGuard
from tests.desktop.test_focus_guard import FakeActions, FakeCensus, FakeController, FakeReader, browser, line_for

INSTAGRAM = ('Instagram - Opera', 'https://www.instagram.com/')
X = ('Home / X - Opera', 'https://x.com/home')


@patch.object(focus_guard, 'LINGER_SECONDS', 0)
@patch.object(focus_guard, 'SILENT_VISIT_SECONDS', 0)
@patch('desktop.focus_guard.diagnostic')
class LockInPassTests(unittest.TestCase):
    def guard(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        voice = patch('phone.voice.synthesize_wav', return_value=b'RIFF')
        voice.start()
        self.addCleanup(voice.stop)
        self.controller = FakeController()
        self.window = None
        self.reader = FakeReader()
        self.keyboard = FakeActions()
        self.census = FakeCensus()
        self.now = [100.0]
        guard = FocusGuard(self.controller, reader=self.reader, foreground=lambda: self.window, actions=self.keyboard,
                           clock=lambda: self.now[0], attempts=AttemptLog(Path(folder.name) / 'lock-in.json'), liner=line_for,
                           census=self.census)
        self.addCleanup(lambda: guard.voice_thread and guard.voice_thread.join(2))
        return guard

    def visit(self, guard, page, seconds=0.0, tab=None):
        self.now[0] += seconds
        self.window, self.reader.address = browser(page[0]), page[1]
        self.census.tabs[self.window['hwnd']] = tab
        return guard.check()

    def stay(self, guard, page, seconds):
        results = {self.visit(guard, page, 30) for _ in range(int(seconds // 30))}
        return results

    def away(self, guard, seconds):
        self.now[0] += seconds
        self.window = None
        return guard.check()

    def test_a_pass_keeps_the_next_visit_open_until_the_owner_has_left_it(self, log):
        guard = self.guard()
        self.assertEqual(guard.grant_pass('instagram'), {'state': 'waiting', 'site': 'instagram', 'expiresInSeconds': round(PASS_WAIT_SECONDS)})
        self.assertIsNone(self.visit(guard, INSTAGRAM))
        self.assertEqual(self.stay(guard, INSTAGRAM, 900), {None}, 'A long scroll on the allowed visit stays open')
        self.assertEqual(guard.pass_status(), {'state': 'in_use', 'site': 'instagram'})
        self.assertIsNone(self.away(guard, 60))
        self.assertIsNone(self.visit(guard, INSTAGRAM), 'A short look elsewhere does not end the visit')
        self.assertIsNone(self.away(guard, PASS_AWAY_SECONDS + 1))
        self.assertIsNone(guard.pass_status(), 'Leaving uses the pass up')
        self.assertEqual(self.keyboard.log, [], 'Nothing was closed during the allowed visit')
        self.assertEqual(guard.attempts.read(), 0, 'The allowed visit is not an attempt')
        self.assertEqual(self.visit(guard, INSTAGRAM), 'instagram', 'The next visit is closed again')
        self.assertEqual([entry[:2] for entry in self.keyboard.log], [('close_tab', 101)])
        self.assertEqual(guard.attempts.read(), 1)
        names = [call.args[0] for call in log.call_args_list]
        for name in ('lock_in_pass_granted', 'lock_in_pass_started', 'lock_in_pass_used', 'lock_in_blocked'):
            self.assertIn(name, names)

    def test_a_pass_covers_one_tab_and_never_a_second_one(self, log):
        guard = self.guard()
        guard.grant_pass('x')
        self.assertIsNone(self.visit(guard, X, tab='first'))
        self.assertEqual(self.visit(guard, X, 1, tab='second'), 'x', 'A new X tab is closed as usual')
        self.assertEqual([entry[:2] for entry in self.keyboard.log], [('close_tab', 101)])
        self.assertIsNone(self.visit(guard, X, 1, tab='first'), 'The tab the pass began in stays open')
        self.assertIsNone(self.visit(guard, X, 1, tab=None), 'A moment when the tabs cannot be read does not close it')
        self.assertEqual(guard.pass_status(), {'state': 'in_use', 'site': 'x'})
        self.assertEqual(guard.attempts.read(), 1)
        started = next(call for call in log.call_args_list if call.args[0] == 'lock_in_pass_started')
        self.assertEqual(started.kwargs, {'domain': 'x', 'tab_tracked': True})

    def test_time_on_other_tabs_does_not_keep_the_pass_alive(self, _log):
        guard = self.guard()
        guard.grant_pass('x')
        self.assertIsNone(self.visit(guard, X, tab='first'))
        for _ in range(4):
            self.assertEqual(self.visit(guard, X, 60, tab='another'), 'x')
        self.assertIsNone(guard.pass_status(), 'Off the allowed tab for over three minutes, the pass is used up')
        self.assertEqual(self.visit(guard, X, 1, tab='first'), 'x', 'Even the allowed tab is closed afterwards')

    def test_a_pass_for_one_site_never_opens_the_other(self, _log):
        guard = self.guard()
        guard.grant_pass('instagram')
        self.assertEqual(self.visit(guard, X), 'x')
        self.assertEqual(guard.pass_status()['state'], 'waiting', 'Blocking X does not spend the Instagram pass')
        self.assertIsNone(self.visit(guard, INSTAGRAM, 1))

    def test_a_pass_for_either_site_belongs_to_the_first_one_visited(self, _log):
        guard = self.guard()
        guard.grant_pass(None)
        self.assertEqual(guard.pass_status()['site'], None)
        self.assertIsNone(self.visit(guard, X))
        self.assertEqual(guard.pass_status(), {'state': 'in_use', 'site': 'x'})
        self.assertEqual(self.visit(guard, INSTAGRAM, 1), 'instagram')
        self.assertIsNone(self.visit(guard, X, 1))

    def test_an_unused_pass_lapses_and_a_new_pass_replaces_the_old_one(self, _log):
        guard = self.guard()
        guard.grant_pass('x')
        self.now[0] += PASS_WAIT_SECONDS + 1
        self.assertIsNone(guard.pass_status())
        self.assertEqual(self.visit(guard, X), 'x')
        guard.grant_pass('x')
        guard.grant_pass('instagram')
        self.assertEqual(guard.pass_status()['site'], 'instagram')
        with self.assertRaises(ValueError):
            guard.grant_pass('youtube')

    def test_a_pass_is_used_up_even_while_lock_in_is_paused(self, _log):
        guard = self.guard()
        guard.grant_pass('instagram')
        self.assertIsNone(self.visit(guard, INSTAGRAM))
        self.controller.breaks.active = True
        self.assertIsNone(self.away(guard, PASS_AWAY_SECONDS + 1))
        self.assertIsNone(guard.pass_status())


if __name__ == '__main__':
    unittest.main()
