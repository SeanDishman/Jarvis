from contextlib import ExitStack
import time
import unittest
from unittest.mock import Mock, patch

from desktop import computer, screen
from desktop.displays import containing_display, display_bounds, display_inventory, select_display

APPLY_ACTION = computer.apply_action

SCREENS = [
    {'device': r'\\.\DISPLAY1', 'primary': True, 'internal': False, 'rect': [0, 0, 1920, 1080]},
    {'device': r'\\.\DISPLAY2', 'primary': False, 'internal': False, 'rect': [-1920, 52, 0, 1132]},
]
FIRST = {'hwnd': 11, 'pid': 99991, 'title': 'First screen', 'process': 'notepad.exe', 'rect': [100, 100, 800, 600]}
SECOND = {'hwnd': 22, 'pid': 99992, 'title': 'Second screen', 'process': 'vibecode.exe', 'rect': [-1800, 100, -900, 700]}


class DisplayTests(unittest.TestCase):
    def test_number_and_position_resolve_independently_of_enumeration_order(self):
        screens = SCREENS[::-1]
        for selector in ('2', 'second', 'monitor 2', 'secondary', 'left', r'\\.\DISPLAY2'):
            self.assertEqual(select_display(selector, screens), SCREENS[1], selector)
        self.assertEqual([row['number'] for row in display_inventory(screens)], [2, 1])
        self.assertEqual(select_display('current', screens, SECOND['rect']), SCREENS[1])
        self.assertEqual(select_display('other', screens, SECOND['rect']), SCREENS[0])
        self.assertEqual(display_bounds(screens), [-1920, 0, 1920, 1132])

    def test_unavailable_or_ambiguous_display_never_falls_back_to_primary(self):
        for selector in ('3', 'laptop', 'unknown', 'external'):
            with self.subTest(selector=selector), self.assertRaises(RuntimeError):
                select_display(selector, SCREENS)
        with self.assertRaises(RuntimeError):
            select_display('secondary', SCREENS[:1])
        with self.assertRaises(RuntimeError):
            select_display('all', [])

    def test_window_monitor_uses_largest_intersection(self):
        self.assertEqual(containing_display([-1400, 60, 100, 900], SCREENS), SCREENS[1])

    def test_explicit_second_display_overrides_unrelated_named_app_and_focus(self):
        with patch.object(screen, 'desktop_name', return_value='Default'), patch.object(screen, 'USER'), \
             patch.object(screen, 'monitors', return_value=SCREENS), \
             patch.object(screen, 'window_info', return_value=FIRST), \
             patch.object(screen, 'capture_app') as app, \
             patch.object(screen, 'capture_rect', return_value={'image': 'png'}) as capture:
            frame = screen.capture_screen('2', FIRST)
            capture.assert_called_once_with(SCREENS[1]['rect'])
            app.assert_not_called()
            self.assertEqual(frame['display'], SCREENS[1]['device'])
            capture.reset_mock()
            screen.capture_screen('all', FIRST)
            capture.assert_called_once_with([-1920, 0, 1920, 1132])


class MultiMonitorControlTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.minimized = set()
        self.windows = {11: dict(FIRST), 22: dict(SECOND)}
        apps = Mock(apps=[{'Name': 'Notepad'}])
        self.control = computer.ComputerControl(apps)
        stack.enter_context(patch.object(computer, 'require_desktop'))
        self.user = stack.enter_context(patch.object(computer, 'USER'))
        self.user.GetForegroundWindow.return_value = 11
        self.user.SetThreadDpiAwarenessContext.return_value = 0
        self.user.IsIconic.side_effect = lambda hwnd: hwnd in self.minimized
        self.user.GetMonitorInfoW.return_value = False
        self.user.ShowWindowAsync.side_effect = lambda hwnd, mode: self.minimized.add(hwnd) or True
        stack.enter_context(patch.object(computer, 'window_info', side_effect=lambda hwnd: self.windows.get(hwnd)))
        stack.enter_context(patch.object(computer, 'visible_windows', side_effect=lambda: list(self.windows.values())))
        stack.enter_context(patch.object(computer, 'monitors', return_value=SCREENS))
        self.capture = stack.enter_context(patch.object(computer, 'capture_rect', side_effect=lambda rect:
            {'image': 'png', 'rect': rect, 'width': 1400, 'height': 800, 'captured': time.monotonic()}))
        self.inputs = stack.enter_context(patch.object(computer, 'apply_action'))
        self.focus = stack.enter_context(patch.object(computer, 'bring_to_front'))
        stack.enter_context(patch.object(computer.time, 'sleep'))

    def run_actions(self, actions, readonly=False):
        actions = iter(actions)
        job = {'question': 'Minimize all windows on the second monitor', '_foreground': 11, '_readOnly': readonly}
        scenes = []
        def ask(job, *_args):
            scenes.append(job['_desktop'])
            action = next(actions)
            return action() if callable(action) else action
        self.control.run(job, lambda job, **values: job.update(values), lambda _: None, ask)
        return job, scenes

    def test_overview_contains_both_screens_and_can_inspect_second_without_focus(self):
        job, scenes = self.run_actions([{'action': 'view_display', 'target': SCREENS[1]['device']},
                                       {'action': 'done'}], readonly=True)
        self.assertEqual(self.capture.call_args_list[0].args[0], [-1920, 0, 1920, 1132])
        self.assertEqual(self.capture.call_args_list[1].args[0], SCREENS[1]['rect'])
        self.assertEqual(scenes[0]['windows'][1]['monitor'], SCREENS[1]['device'])
        self.assertFalse(scenes[0]['windows'][1]['inputAllowed'])
        self.assertEqual(len(scenes[0]['monitors']), 2)
        self.assertEqual(job['status'], 'completed')
        self.focus.assert_not_called()
        self.inputs.assert_not_called()

    def test_background_window_on_second_monitor_is_minimized_and_read_back(self):
        job, scenes = self.run_actions([{'action': 'minimize_window', 'target': 'window:22'}, {'action': 'done'}])
        self.assertEqual(self.minimized, {22})
        self.user.ShowWindowAsync.assert_called_once_with(22, 7)
        self.assertTrue(scenes[1]['lastWindowAction']['verified'])
        self.assertTrue(scenes[1]['windows'][1]['minimized'])
        self.assertFalse(scenes[1]['windows'][0]['minimized'])
        self.assertEqual(job['status'], 'completed')
        self.focus.assert_not_called()
        self.inputs.assert_not_called()

    def test_minimized_window_retains_monitor_from_native_restore_placement(self):
        self.minimized.add(22)
        self.windows[22]['rect'] = [-32000, -32000, -31800, -31950]
        def info(handle, pointer):
            pointer._obj.device = SCREENS[1]['device']
            return True
        self.user.GetMonitorInfoW.side_effect = info
        self.assertEqual(computer.window_display(self.windows[22], SCREENS), SCREENS[1])

    def test_unknown_or_readonly_window_action_never_minimizes(self):
        for target, readonly in (('window:999', False), ('window:22', True)):
            with self.subTest(target=target, readonly=readonly), self.assertRaises(RuntimeError):
                self.run_actions([{'action': 'minimize_window', 'target': target}], readonly)
        self.user.ShowWindowAsync.assert_not_called()

    def test_stale_window_identity_or_position_is_rejected(self):
        for field, value in (('pid', 88), ('process', 'replacement.exe'), ('rect', FIRST['rect'])):
            snapshot = dict(SECOND)
            self.windows[22] = {**SECOND, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'changed or moved'):
                computer.minimize_window(snapshot)
        self.user.ShowWindowAsync.assert_not_called()

    def test_failed_minimization_is_never_reported_completed(self):
        self.user.ShowWindowAsync.side_effect = lambda *_: True
        self.user.SendMessageTimeoutW.return_value = 0
        job, _ = self.run_actions([{'action': 'minimize_window', 'target': 'window:22'}, {'action': 'done'}])
        self.assertEqual(job['status'], 'failed')
        self.assertIn('Second screen', job['summary'])
        self.assertIn('could not be verified', job['summary'])
        self.assertFalse(job['windowResults'][0]['verified'])

    def test_access_denied_uses_system_minimize_and_verifies_actual_state(self):
        self.user.ShowWindowAsync.side_effect = lambda *_: False
        def minimize(hwnd, message, command, *_args):
            self.assertEqual((hwnd, message, command), (22, 0x0112, 0xF020))
            self.minimized.add(hwnd)
            return 1
        self.user.SendMessageTimeoutW.side_effect = minimize
        with patch.object(computer.ctypes, 'get_last_error', side_effect=[5, 0]):
            computer.minimize_window(SECOND)
        self.assertEqual(self.minimized, {22})
        self.assertEqual(self.user.SendMessageTimeoutW.call_args.args[5], 1000)
        self.focus.assert_not_called()

    def test_one_refusal_does_not_skip_other_requested_windows(self):
        self.windows[33] = {**SECOND, 'hwnd': 33, 'pid': 99993, 'title': 'Another second-screen window'}
        self.user.ShowWindowAsync.side_effect = lambda hwnd, _: (self.minimized.add(hwnd) or True) if hwnd == 33 else False
        self.user.SendMessageTimeoutW.return_value = 0
        with patch.object(computer.ctypes, 'get_last_error', return_value=5):
            job, scenes = self.run_actions([{'action': 'minimize_window', 'target': 'window:22'},
                                           {'action': 'minimize_window', 'target': 'window:33'}, {'action': 'done'}])
        self.assertEqual(self.minimized, {33})
        self.assertEqual(job['status'], 'failed')
        self.assertIn('Minimized 1 window', job['summary'])
        self.assertIn('denied access', job['summary'])
        self.assertFalse(scenes[1]['lastWindowAction']['verified'])
        self.assertEqual([row['verified'] for row in job['windowResults']], [False, True])

    def test_cancelled_minimization_never_sends_native_action(self):
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            computer.minimize_window(SECOND, check=Mock(side_effect=RuntimeError('cancelled')))
        self.user.ShowWindowAsync.assert_not_called()

    def test_negative_screen_click_maps_into_virtual_desktop_coordinates(self):
        target = {**SECOND, 'process': 'notepad.exe'}
        self.windows[22] = target
        self.user.GetForegroundWindow.return_value = 22
        self.user.GetSystemMetrics.side_effect = {76: -1920, 77: 0, 78: 3840, 79: 1132}.get
        self.user.GetAncestor.return_value = 22
        frame = {'rect': target['rect'], 'width': 900, 'height': 600, 'captured': time.monotonic()}
        with patch.object(computer, 'send_inputs') as send:
            APPLY_ACTION(target, frame, {'action': 'click', 'x': 450, 'y': 300})
        move = send.call_args_list[0].args[0].data.mi
        self.assertEqual(move.dwFlags, 0x8000 | 0x4000 | 0x0001)
        self.assertEqual(move.dx, round(570 * 65535 / 3839))
        self.assertEqual(move.dy, round(400 * 65535 / 1131))


if __name__ == '__main__':
    unittest.main()
