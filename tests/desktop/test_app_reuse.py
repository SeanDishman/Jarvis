from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

from desktop import app_identity, computer
from desktop.app_targets import app_windows, window_score


def window(process, title, hwnd=41, pid=98765):
    return {'hwnd': hwnd, 'pid': pid, 'process': process, 'title': title, 'rect': [30, 40, 830, 640]}


class AppWindowMatchTests(unittest.TestCase):
    def test_running_apps_are_recognised_by_their_executable(self):
        for name, process, title in (('Opera GX Browser', 'opera.exe', 'Home / X - Opera'),
                                     ('Microsoft Edge', 'msedge.exe', 'New tab - Personal - Microsoft​ Edge'),
                                     ('Visual Studio Code', 'Code.exe', 'notes.md - jarvis'),
                                     ('Google Chrome', 'chrome.exe', 'Inbox - Google Chrome'),
                                     ('Spotify', 'Spotify.exe', 'Spotify Premium')):
            with self.subTest(app=name):
                self.assertTrue(window_score({'Name': name}, window(process, title)))

    def test_unrelated_apps_do_not_claim_a_window(self):
        for name, process in (('Opera GX Browser', 'chrome.exe'), ('Spotify', 'opera.exe'), ('Microsoft Store', 'msedge.exe'),
                              ('Browser Tools', 'opera.exe')):
            with self.subTest(app=name, process=process):
                self.assertFalse(window_score({'Name': name}, window(process, 'Untitled')))
        windows = {'window:1': window('opera.exe', 'Home / X - Opera', 1), 'window:2': window('notepad.exe', 'notes - Notepad', 2)}
        self.assertEqual(app_windows({'Name': 'Opera GX Browser'}, windows), ['window:1'])


class AppReuseLoopTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.apps = Mock()
        self.apps.apps = [{'Name': 'Opera GX Browser', 'AppID': 'opera'}, {'Name': 'Notepad', 'AppID': 'notepad'}]
        self.apps.launch.return_value = {'ok': True}
        self.control = computer.ComputerControl(self.apps)
        self.target = window('opera.exe', 'Home / X - Opera')
        self.state = {'minimized': False, 'foreground': 7}
        stack.enter_context(patch.object(computer, 'require_desktop'))
        self.user = user = stack.enter_context(patch.object(computer, 'USER'))
        user.GetForegroundWindow.side_effect = lambda: self.state['foreground']
        user.IsIconic.side_effect = lambda hwnd: self.state['minimized']
        user.IsZoomed.return_value = False
        user.SetThreadDpiAwarenessContext.return_value = 0
        def show(hwnd, command):
            if command in (4, 9):
                self.state['minimized'] = False
            elif command in (6, 7):
                self.state['minimized'] = True
        user.ShowWindow.side_effect = show
        stack.enter_context(patch.object(computer, 'window_info', side_effect=lambda hwnd: self.target if hwnd == 41 else None))
        stack.enter_context(patch.object(computer, 'visible_windows', side_effect=lambda: [self.target]))
        self.focus = stack.enter_context(patch.object(computer, 'bring_to_front'))
        stack.enter_context(patch.object(computer.time, 'sleep'))
        stack.enter_context(patch.object(computer, 'monitors', return_value=[{'device': 'fixture', 'rect': [0, 0, 1200, 800],
                                                                               'work': [0, 0, 1200, 760], 'primary': True, 'internal': True}]))
        stack.enter_context(patch.object(computer, 'capture_rect', return_value={'image': 'overview'}))
        stack.enter_context(patch.object(computer, 'capture_window', return_value={'image': 'opera', 'source': 'window'}))
        stack.enter_context(patch.object(computer.Accessibility, 'inspect', return_value={'available': True, 'controls': []}))

    def run_actions(self, question, *actions):
        script = iter(actions)
        scenes = []
        job = {'question': question, '_foreground': 7}
        def ask(job, frame, steps, readonly):
            scenes.append(job['_desktop'])
            return next(script)
        self.control.run(job, lambda job, **values: job.update(values), lambda job: None, ask)
        return job, scenes

    def test_an_open_app_is_reused_instead_of_launching_another_copy(self):
        job, scenes = self.run_actions("Close all the tabs on my Opera GX but the one I'm currently using",
                                       {'action': 'open_app', 'target': 'app:0'},
                                       {'action': 'inspect_window', 'target': 'window:41'},
                                       {'action': 'done', 'message': 'Done.'})
        self.apps.launch.assert_not_called()
        self.assertEqual(scenes[0]['windows'][0]['app'], 'Opera GX Browser')
        refused = scenes[1]['lastAction']['result']
        self.assertEqual((refused['status'], refused['windows']), ('already_open', ['window:41']))
        self.assertEqual(scenes[2]['selectedWindow'], 'window:41')
        self.assertEqual(job['status'], 'completed')
        self.focus.assert_not_called()

    def test_the_agent_is_told_which_installed_apps_are_the_owner_s_defaults(self):
        browser = r'C:\Programs\Opera GX\opera.exe'
        with patch.object(app_identity, 'default_program', side_effect=lambda scheme: browser if scheme == 'https' else None), \
             patch.object(app_identity, 'declared_names', side_effect=lambda path: ('Opera GX Internet Browser',) if path == browser else ()):
            _, scenes = self.run_actions('What is open in my browser?', {'action': 'done', 'message': 'Done.'})
        self.assertEqual(scenes[0]['defaults'], {'browser': 'Opera GX Browser', 'email': None})

    def test_an_explicit_new_window_request_still_launches(self):
        job, _ = self.run_actions('Open a new Opera GX window', {'action': 'open_app', 'target': 'app:0'}, {'action': 'done'})
        self.apps.launch.assert_called_once_with('Opera GX Browser')
        self.assertEqual(job['status'], 'completed')

    def test_a_minimized_window_is_worked_behind_the_owner_and_minimized_again(self):
        self.state['minimized'] = True
        job, scenes = self.run_actions('Close my other Opera GX tabs', {'action': 'inspect_window', 'target': 'window:41'},
                                       {'action': 'done', 'message': 'Done.'})
        self.assertEqual(scenes[1]['selectedWindow'], 'window:41')
        self.user.SetWindowPos.assert_any_call(41, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        commands = [call.args[1] for call in self.user.ShowWindow.call_args_list]
        self.assertEqual(commands, [4, 7], 'Restore without activation, then minimize again without activation')
        self.assertTrue(self.state['minimized'])
        self.focus.assert_not_called()
        self.assertEqual(job['status'], 'completed')

    def test_a_window_the_owner_brought_forward_is_left_open(self):
        self.state['minimized'] = True
        def ask_after_owner_returns(job, frame, steps, readonly):
            if job['_desktop']['selectedWindow']:
                self.state['foreground'] = 41
                return {'action': 'done'}
            return {'action': 'inspect_window', 'target': 'window:41'}
        job = {'question': 'Close my other tabs', '_foreground': 7}
        self.control.run(job, lambda job, **values: job.update(values), lambda job: None, ask_after_owner_returns)
        self.assertEqual([call.args[1] for call in self.user.ShowWindow.call_args_list], [4])
        self.assertFalse(self.state['minimized'])

    def test_jarvis_interface_windows_are_never_offered_to_the_agent(self):
        computer.OWN_PROCESSES.add(4242)
        self.addCleanup(computer.OWN_PROCESSES.discard, 4242)
        jarvis = window('electron.exe', 'Jarvis', 9, 4242)
        self.assertFalse(computer.manageable(jarvis))
        self.assertFalse(computer.controllable(jarvis))
        self.assertTrue(computer.manageable(window('electron.exe', 'Other Electron app', 10, 4243)))


if __name__ == '__main__':
    unittest.main()
