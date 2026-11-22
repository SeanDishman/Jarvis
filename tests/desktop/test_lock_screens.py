import threading
import unittest
from unittest.mock import patch

from desktop import lock_screens
from desktop.computer import OWN_PROCESSES
from desktop.lock_screens import alpenlock_up, covered, wait_until_clear

MAIN = {'device': r'\\.\DISPLAY1', 'primary': True, 'rect': [0, 0, 1920, 1080], 'work': [0, 0, 1920, 1032]}
SIDE = {'device': r'\\.\DISPLAY2', 'primary': False, 'rect': [-1920, 52, 0, 1132], 'work': [-1920, 52, 0, 1084]}
SCREENS = [MAIN, SIDE]


def window(title='Alpenlock', rect=(0, 0, 1920, 1080), pid=4242, process='electron.exe'):
    return {'hwnd': 1, 'pid': pid, 'title': title, 'process': process, 'path': '', 'rect': list(rect)}


class AlpenlockWindowTests(unittest.TestCase):
    def test_a_lock_window_over_any_display_counts(self):
        self.assertTrue(alpenlock_up([window()], SCREENS))
        self.assertTrue(alpenlock_up([window(rect=SIDE['rect'])], SCREENS))
        self.assertTrue(alpenlock_up([window(rect=(-2, -2, 1922, 1082))], SCREENS), 'a window a little larger than its display')

    def test_the_tray_panel_and_preview_do_not_count(self):
        self.assertFalse(alpenlock_up([window(rect=(1500, 640, 1880, 1030))], SCREENS), 'tray panel')
        self.assertFalse(alpenlock_up([window(title='Alpenlock preview')], SCREENS))
        self.assertFalse(alpenlock_up([window(rect=(200, 100, 1400, 900))], SCREENS), 'a windowed preview')

    def test_other_full_screen_windows_and_jarvis_itself_do_not_count(self):
        self.assertFalse(alpenlock_up([window(title='Minecraft', process='javaw.exe')], SCREENS))
        self.assertFalse(alpenlock_up([window(title='Alpenlock trailer - YouTube', process='opera.exe')], SCREENS))
        self.assertFalse(alpenlock_up([window(pid=next(iter(OWN_PROCESSES)))], SCREENS))
        self.assertFalse(alpenlock_up([], SCREENS))


class CoveredTests(unittest.TestCase):
    def check(self, desktop, windows=()):
        with patch.object(lock_screens, 'input_desktop', return_value=desktop), \
                patch.object(lock_screens, 'titled', return_value=list(windows)), \
                patch('desktop.windows.monitors', return_value=SCREENS):
            return covered()

    def test_the_windows_lock_screen_counts_even_when_its_desktop_is_hidden(self):
        self.assertEqual(self.check(None), 'windows')
        self.assertEqual(self.check('Winlogon'), 'windows')
        self.assertEqual(self.check('Screen-saver'), 'windows')

    def test_alpenlock_counts_on_the_normal_desktop(self):
        self.assertEqual(self.check('Default', [window(rect=SIDE['rect'])]), 'alpenlock')

    def test_a_clear_desktop_is_clear(self):
        self.assertIsNone(self.check('Default'))
        self.assertIsNone(self.check('Default', [window(rect=(1500, 640, 1880, 1030))]))


class WaitUntilClearTests(unittest.TestCase):
    def run_wait(self, covers, settle=1.5, stop=None):
        now = [0.0]
        calls = []

        def check():
            calls.append(now[0])
            return covers[min(len(calls) - 1, len(covers) - 1)]

        def sleep(seconds):
            now[0] += seconds
            return stop.is_set() if stop else False

        result = wait_until_clear(stop, settle=settle, poll=.5, check=check, clock=lambda: now[0], sleep=sleep)
        return result, now[0], calls

    def test_waits_for_the_unlock_then_a_short_settle(self):
        result, finished, _calls = self.run_wait(['alpenlock'] * 6 + [None] * 10)
        self.assertTrue(result)
        self.assertEqual(finished, 3.0 + 1.5, 'clear at 3.0s, greeted only after 1.5s of staying clear')

    def test_a_blink_while_alpenlock_rebuilds_its_windows_does_not_count_as_unlocked(self):
        result, finished, _calls = self.run_wait(['alpenlock', None, 'alpenlock', 'alpenlock'] + [None] * 10)
        self.assertTrue(result)
        self.assertEqual(finished, 2.0 + 1.5)

    def test_windows_lock_then_alpenlock_both_hold_it(self):
        result, finished, _calls = self.run_wait(['windows'] * 4 + ['alpenlock'] * 4 + [None] * 10)
        self.assertTrue(result)
        self.assertEqual(finished, 4.0 + 1.5)

    def test_a_check_that_breaks_never_holds_the_greeting_forever(self):
        def broken():
            raise OSError('no desktop')
        self.assertTrue(wait_until_clear(settle=1.0, poll=.5, check=broken, clock=iter([0, .5, 1.0, 1.5]).__next__,
                                         sleep=lambda _seconds: None))

    def test_shutting_down_stops_the_wait(self):
        stop = threading.Event()
        stop.set()
        self.assertFalse(wait_until_clear(stop, check=lambda: 'alpenlock'))
        stop = threading.Event()
        polls = []

        def sleep(seconds):
            polls.append(seconds)
            if len(polls) == 3:
                stop.set()

        self.assertFalse(wait_until_clear(stop, check=lambda: 'alpenlock', sleep=sleep))
        self.assertEqual(len(polls), 3)


if __name__ == '__main__':
    unittest.main()
