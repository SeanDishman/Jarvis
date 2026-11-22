import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop import focus_guard
from desktop.focus_guard import SECOND_CHANCE, AttemptLog, FocusGuard, SecondChance, cooldown_line
from desktop.lock_in_lines import fallback_line, lock_in_line
from tests.desktop.test_focus_guard import FakeActions, FakeCensus, FakeController, FakeReader, browser, line_for, model_response

DOORDASH = ('Order Food Delivery Online | DoorDash - Opera', 'https://www.doordash.com/')
X = ('Home / X - Opera', 'https://x.com/home')
REPEAT, COOLDOWN = SECOND_CHANCE['doordash']['repeat_seconds'], SECOND_CHANCE['doordash']['cooldown_seconds']


@patch.object(focus_guard, 'LINGER_SECONDS', 0)
@patch.object(focus_guard, 'SILENT_VISIT_SECONDS', 0)
@patch('desktop.focus_guard.diagnostic')
class SecondChanceTests(unittest.TestCase):
    """DoorDash: the first visit is closed; going straight back leaves it open and starts a 20-minute cooldown."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        voice = patch('phone.voice.synthesize_wav', return_value=b'RIFF')
        voice.start()
        self.addCleanup(voice.stop)
        self.now = [1_800_000_000.0]
        self.window, self.reader, self.keyboard = None, FakeReader(), FakeActions()

    def guard(self):
        self.controller = FakeController()
        guard = FocusGuard(self.controller, reader=self.reader, foreground=lambda: self.window, actions=self.keyboard,
                           clock=lambda: self.now[0], wall=lambda: self.now[0], attempts=AttemptLog(self.folder / 'lock-in.json'),
                           liner=line_for, census=FakeCensus(), second_chance=SecondChance(self.folder / 'cooldowns.json'))
        self.addCleanup(lambda: guard.voice_thread and guard.voice_thread.join(2))
        return guard

    def visit(self, guard, page, seconds=0.0):
        self.now[0] += seconds
        self.window, self.reader.address = browser(page[0]), page[1]
        return guard.check()

    def announced(self):
        rows = []
        while not self.controller.events.empty():
            event = self.controller.events.get()
            if event[0] == 'lock_in':
                rows.append(event[1])
        return rows

    def closes(self):
        return [entry[0] for entry in self.keyboard.log]

    def test_doordash_is_closed_first_then_left_open_with_a_cooldown(self, log):
        guard = self.guard()
        self.assertEqual(self.visit(guard, DOORDASH), 'doordash')
        first = self.announced()
        self.assertEqual((len(first), first[0]['name'], first[0]['attempt']), (1, 'DoorDash', 1))
        self.assertIsNone(self.visit(guard, DOORDASH, 60), 'Going straight back leaves it open')
        granted = self.announced()
        self.assertEqual(len(granted), 1)
        self.assertTrue(granted[0]['cooldown'])
        self.assertEqual(granted[0]['text'], cooldown_line('DoorDash', 20))
        self.assertNotIn('attempt', granted[0], 'Letting it through is not a blocked attempt')
        for minutes in (1, 5, 12):
            self.assertIsNone(self.visit(guard, DOORDASH, 60 * minutes / 3), 'It stays open during the cooldown')
        self.assertEqual(self.announced(), [], 'Nothing is said again during the cooldown')
        self.assertEqual(self.closes(), ['close_tab'])
        self.assertEqual(self.visit(guard, DOORDASH, COOLDOWN), 'doordash', 'After the cooldown it is closed again')
        self.assertEqual(guard.attempts.read(), 2)
        names = [call.args[0] for call in log.call_args_list]
        self.assertIn('lock_in_cooldown_started', names)

    def test_a_visit_long_after_the_close_is_a_fresh_first_visit(self, _log):
        guard = self.guard()
        self.assertEqual(self.visit(guard, DOORDASH), 'doordash')
        self.assertEqual(self.visit(guard, DOORDASH, REPEAT + 60), 'doordash')
        self.assertEqual(self.closes(), ['close_tab', 'close_tab'])

    def test_the_cooldown_is_only_for_doordash(self, _log):
        guard = self.guard()
        self.visit(guard, DOORDASH)
        self.assertIsNone(self.visit(guard, DOORDASH, 30))
        self.assertEqual(self.visit(guard, X, 30), 'x', 'X stays closed while DoorDash cools down')

    def test_a_lock_in_pass_never_opens_doordash(self, _log):
        # Passes are for the feeds; DoorDash has its own second chance.
        guard = self.guard()
        guard.grant_pass(None)
        self.assertEqual(self.visit(guard, DOORDASH), 'doordash')
        with self.assertRaises(ValueError):
            guard.grant_pass('doordash')

    def test_the_cooldown_survives_a_restart(self, _log):
        guard = self.guard()
        self.visit(guard, DOORDASH)
        self.visit(guard, DOORDASH, 30)
        saved = json.loads((self.folder / 'cooldowns.json').read_text(encoding='utf-8'))
        self.assertAlmostEqual(saved['doordash']['until'], self.now[0] + COOLDOWN)
        restarted = self.guard()
        self.assertIsNone(self.visit(restarted, DOORDASH, 120))
        self.assertEqual(self.closes(), ['close_tab'])


class DoorDashLineTests(unittest.TestCase):
    def test_the_line_is_written_around_the_reason_and_the_backup_is_the_owners_own(self):
        with patch('desktop.lock_in_lines.local_request', return_value=model_response('Sir, that is the craving ordering, and the macros say no.')) as request:
            lock_in_line('DoorDash', 1, 'lockin-test')
        sent = json.loads(request.call_args.args[2]['input'])
        self.assertEqual(sent['site'], 'DoorDash')
        self.assertIn('macros', sent['reason'])
        backup = fallback_line('DoorDash', 3)
        self.assertIn('brain wants', backup)
        self.assertIn('macros', backup)
        with patch('desktop.lock_in_lines.local_request', side_effect=OSError('offline')):
            self.assertEqual(lock_in_line('DoorDash', 1, 'lockin-test'), backup)


if __name__ == '__main__':
    unittest.main()
