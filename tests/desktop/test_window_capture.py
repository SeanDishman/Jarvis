from tests.desktop.intent_fixtures import decision
import ctypes
import os
import queue
import unittest
from unittest.mock import Mock, patch

from desktop import computer, screen
from desktop.controller import Controller, capabilities_for
from desktop.windows import monitors


PROTON = {'hwnd': 41, 'pid': 7, 'title': 'Proton VPN', 'process': 'protonvpn.client.exe', 'rect': [325, 98, 1595, 920]}


class ProtonRequestTests(unittest.TestCase):
    def test_open_and_connect_starts_a_pc_task_with_the_complete_request(self):
        controller = Controller(queue.Queue())
        controller.tasks = None
        controller.apps.apps = [{'Name': 'Proton VPN', 'AppID': 'proton-fixture'}]
        commands = ('Open Proton VPN and then connect me', 'Open Proton VPN and connect',
                    'Open ProtonVPN then connect me', 'Open Proton VPN and disconnect me')
        decision = {'action': 'computer', 'model_tier': 'default', 'needs_clarification': False}
        with patch('desktop.controller.classify_request', return_value=decision), \
             patch.object(controller.preferences, 'read', return_value={'offlineSpeech': 'auto'}), \
             patch.object(controller.research, 'start', return_value={'ok': True}) as start, \
             patch.object(controller.apps, 'launch') as launch, \
             patch.object(controller, 'ask_model') as model:
            for command in commands:
                with self.subTest(command=command):
                    start.reset_mock()
                    result = controller.respond(command, speak=False)
                    self.assertTrue(result['ok'], result)
                    self.assertEqual(result['tools'], ['start_computer_task'])
                    start.assert_called_once_with(command, 'computer', model_tier='default')
            launch.assert_not_called()
            model.assert_not_called()

    def test_opening_or_asking_about_vpn_does_not_enable_clicks(self):
        for command in ('Open Proton VPN', 'Is Proton VPN connected?',
                        'What does Proton VPN say?', 'How does Proton VPN connect?'):
            with self.subTest(command=command):
                tools, _, _ = capabilities_for(decision(action='screen') if 'say' in command else decision('list_apps') if command.startswith('Open') else decision())
                self.assertNotIn('start_computer_task', tools)


class MonitorTests(unittest.TestCase):
    def test_real_monitor_list_has_full_display_bounds(self):
        screens = monitors()
        self.assertTrue(screens)
        for display in screens:
            left, top, right, bottom = display['rect']
            self.assertTrue(right > left and bottom > top, display)
            work = display['work']
            self.assertTrue(left <= work[0] and top <= work[1] and work[2] <= right and work[3] <= bottom, display)

    def test_screen_question_works_with_real_monitor_list(self):
        with patch('desktop.screen.desktop_name', return_value='Default'), \
             patch('desktop.screen.capture_rect', return_value={'image': 'png'}) as capture:
            self.assertEqual(screen.capture_screen('current')['image'], 'png')
            self.assertEqual(screen.capture_screen('all')['display'], 'All displays')
        for call in capture.call_args_list:
            self.assertEqual(len(call.args[0]), 4)


class WindowCaptureTests(unittest.TestCase):
    def test_target_window_is_rendered_directly_before_screen_pixels(self):
        frame = {'image': 'proton-pixels'}
        with patch.object(computer, 'require_desktop'), patch.object(computer, 'matches_screen_dpi', return_value=True), \
             patch.object(computer, 'USER') as user, patch.object(computer, 'GDI'), \
             patch.object(computer, 'frame_from', return_value=frame) as render, \
             patch.object(computer, 'capture_rect') as screen_pixels:
            user.PrintWindow.return_value = 1
            self.assertIs(computer.capture_window(PROTON), frame)
        self.assertEqual(user.PrintWindow.call_args.args[2], 2)
        self.assertEqual(render.call_args.args[2:4], ((0, 0), (1270, 822)))
        self.assertTrue(render.call_args.kwargs['require_detail'])
        screen_pixels.assert_not_called()

    def test_blank_unrendered_or_dpi_scaled_window_uses_visible_pixels(self):
        cases = ((True, 0, None), (True, 1, None), (False, 1, {'image': 'unused'}))
        for same_dpi, printed, rendered in cases:
            with patch.object(computer, 'require_desktop'), patch.object(computer, 'matches_screen_dpi', return_value=same_dpi), \
                 patch.object(computer, 'USER') as user, patch.object(computer, 'GDI'), \
                 patch.object(computer, 'frame_from', return_value=rendered), \
                 patch.object(computer, 'capture_rect', return_value={'image': 'screen'}) as screen_pixels:
                user.PrintWindow.return_value = printed
                self.assertEqual(computer.capture_window(PROTON)['image'], 'screen')
                screen_pixels.assert_called_once_with(PROTON['rect'])

    def test_bring_to_front_restores_and_earns_foreground_without_moving_pointer(self):
        foreground = iter([5, 5, 41, 41])
        with patch.object(computer, 'USER') as user, patch.object(computer.time, 'sleep'):
            user.IsIconic.return_value = True
            user.GetForegroundWindow.side_effect = lambda: next(foreground)
            computer.bring_to_front(41)
        user.ShowWindow.assert_called_once_with(41, 9)
        count, inputs, size = user.SendInput.call_args.args
        empty = inputs._obj
        self.assertEqual((count, size, empty.type), (1, ctypes.sizeof(computer.Input), 0))
        self.assertEqual((empty.data.mi.dx, empty.data.mi.dy, empty.data.mi.dwFlags), (0, 0, 0))
        user.SetForegroundWindow.assert_called_once_with(41)
        self.assertEqual(user.SetWindowPos.call_args.args[-1], 0x13)

    def test_foreground_app_is_only_kept_on_top(self):
        with patch.object(computer, 'USER') as user, patch.object(computer.time, 'sleep'):
            user.IsIconic.return_value = False
            user.GetForegroundWindow.return_value = 41
            computer.bring_to_front(41)
        user.SendInput.assert_not_called()
        user.SetForegroundWindow.assert_not_called()
        user.SetWindowPos.assert_called_once()


class PcTaskTests(unittest.TestCase):
    def run_task(self, foregrounds):
        apps = Mock()
        apps.apps = [{'Name': 'Proton VPN'}]
        control = computer.ComputerControl(apps)
        updates = []
        job = {'question': 'Can you turn on Proton VPN', '_foreground': 5}
        foregrounds = [None, *foregrounds]
        def info(hwnd):
            return PROTON if hwnd == 41 else foregrounds.pop(0)
        actions = iter([{'action': 'focus_window', 'target': 'window:41'},
                        {'action': 'done', 'message': 'Proton VPN is connected, sir.'}])
        with patch.object(computer, 'require_desktop'), patch.object(computer, 'USER') as user, \
             patch.object(computer, 'visible_windows', return_value=[PROTON]), \
             patch.object(computer, 'bring_to_front') as front, patch.object(computer, 'keep_on_top'), \
             patch.object(computer, 'window_info', side_effect=info), patch.object(computer.time, 'sleep'), \
             patch.object(computer.Accessibility, 'inspect', return_value={'available': False, 'controls': []}), \
             patch.object(computer, 'capture_rect', return_value={'image': 'desktop'}), \
             patch.object(computer, 'capture_window', return_value={'image': 'vpn'}) as capture:
            user.SetThreadDpiAwarenessContext.return_value = 0
            user.GetForegroundWindow.return_value = 5
            user.IsIconic.return_value = False
            control.run(job, lambda _job, **values: updates.append(values), lambda _job: None,
                        lambda *_args: next(actions))
        return front, capture, updates

    def test_task_captures_the_app_window_directly(self):
        front, capture, updates = self.run_task([PROTON])
        front.assert_called_once_with(41)
        capture.assert_called_once_with(PROTON)
        self.assertEqual(updates[-1]['status'], 'completed')

    def test_jarvis_popup_does_not_prevent_background_inspection(self):
        popup = {**PROTON, 'hwnd': 77, 'pid': os.getpid(), 'title': 'Jarvis activity', 'process': 'python.exe'}
        front, capture, updates = self.run_task([popup, PROTON])
        self.assertEqual(front.call_count, 1)
        capture.assert_called_once_with(PROTON)
        self.assertEqual(updates[-1]['status'], 'completed')

    def test_another_app_taking_focus_does_not_prevent_background_inspection(self):
        other = {**PROTON, 'hwnd': 88, 'pid': 99, 'title': 'Notepad', 'process': 'notepad.exe'}
        front, capture, updates = self.run_task([other])
        front.assert_called_once_with(41)
        capture.assert_called_once_with(PROTON)
        self.assertEqual(updates[-1]['status'], 'completed')


class NamedWindowTests(unittest.TestCase):
    def setUp(self):
        apps = Mock()
        apps.apps = [{'Name': 'Proton VPN'}, {'Name': 'Word'}, {'Name': 'Notepad'}]
        apps.match.side_effect = lambda name: [row for row in apps.apps if name in row['Name'].lower()]
        self.control = computer.ComputerControl(apps)
        self.windows = [PROTON, {**PROTON, 'hwnd': 42, 'title': 'Document1 - Word', 'process': 'winword.exe'}]

    def test_look_requests_find_the_named_open_app_only(self):
        with patch.object(computer, 'visible_windows', return_value=self.windows):
            for text in ('What does my Proton VPN say?', "what's ProtonVPN showing", 'Is my VPN connected?'):
                self.assertEqual(self.control.open_window(text)['hwnd'], 41, text)
            self.assertEqual(self.control.open_window('Read my Word document')['hwnd'], 42)
            for text in ('Can you read the words on my screen?', 'What is on my screen?'):
                self.assertIsNone(self.control.open_window(text), text)

    def test_closed_app_is_not_launched_for_a_look(self):
        with patch.object(computer, 'visible_windows', return_value=[]):
            self.assertIsNone(self.control.open_window('What does Proton VPN say?'))
        self.control.apps.launch.assert_not_called()

    def test_named_app_is_captured_directly_without_moving_focus(self):
        with patch('desktop.screen.desktop_name', return_value='Default'), patch.object(screen, 'USER') as user, \
             patch.object(screen, 'window_info', return_value=PROTON), \
             patch.object(screen, 'capture_window', return_value={'image': 'vpn'}) as capture, \
             patch.object(screen.time, 'sleep'):
            user.IsIconic.side_effect = [True, False]
            frame = screen.capture_screen('current', PROTON)
        self.assertEqual(frame, {'image': 'vpn', 'display': 'Proton VPN'})
        user.ShowWindow.assert_called_once_with(41, 4)
        user.SetForegroundWindow.assert_not_called()
        capture.assert_called_once_with(PROTON)

    def test_screen_question_passes_the_named_window_to_capture(self):
        controller = Controller(queue.Queue())
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Proton VPN is disconnected, sir.'}]}]}
        with patch('desktop.controller.classify_request', return_value={'action': 'screen', 'display': 'current'}), \
             patch.object(controller.computer, 'open_window', return_value=PROTON) as named, \
             patch('desktop.controller.capture_screen', return_value={'image': 'vpn', 'display': 'Proton VPN'}) as capture, \
             patch('desktop.controller.local_request', return_value=response):
            result = controller.respond('What does Proton VPN say?', speak=False)
        self.assertTrue(result['ok'])
        named.assert_called_once_with('What does Proton VPN say?')
        capture.assert_called_once_with('current', PROTON)


if __name__ == '__main__':
    unittest.main()
