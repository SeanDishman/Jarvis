from contextlib import ExitStack
import copy
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from desktop import computer
from desktop.controller import Controller


class AppClosingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.windows = {n: {'hwnd': n, 'pid': 90000 + n, 'process': f'app-{n}.exe',
                           'title': title, 'rect': [10, 10, 700, 500]}
                        for n, title in enumerate(('Quartz Notes', 'Blue Music', 'Orbit Launcher', 'Unrelated'), 11)}
        self.original = copy.deepcopy(self.windows)
        self.stack.enter_context(patch.object(computer, 'require_desktop'))
        self.stack.enter_context(patch.object(computer.time, 'sleep'))
        self.user = self.stack.enter_context(patch.object(computer, 'USER'))
        self.user.GetForegroundWindow.return_value = 0
        self.user.SetThreadDpiAwarenessContext.return_value = 0
        self.user.IsIconic.return_value = False
        self.user.IsZoomed.return_value = False
        self.user.IsWindowVisible.side_effect = lambda hwnd: hwnd in self.windows
        self.user.PostMessageW.side_effect = lambda hwnd, *_: bool(self.windows.pop(hwnd, None))
        self.stack.enter_context(patch.object(computer, 'window_info', side_effect=self.windows.get))
        self.stack.enter_context(patch.object(computer, 'visible_windows', side_effect=lambda: list(self.windows.values())))
        self.stack.enter_context(patch.object(computer, 'check_pointer_stop'))
        self.stack.enter_context(patch.object(computer, 'monitors', return_value=[
            {'device': 'display', 'rect': [0, 0, 1920, 1080], 'work': [0, 0, 1920, 1040], 'primary': True}]))
        frame = {'image': 'pixels', 'width': 700, 'height': 500, 'rect': [10, 10, 700, 500]}
        self.stack.enter_context(patch.object(computer, 'capture_rect', return_value=frame))
        self.stack.enter_context(patch.object(computer, 'capture_window', return_value=frame))
        self.stack.enter_context(patch.object(computer.Accessibility, 'inspect', return_value={'controls': []}))
        self.control = computer.ComputerControl(Mock(apps=[{'Name': 'Installed app'}]))

    def run_actions(self, actions):
        actions = iter(actions)
        scenes = []
        job = {'question': 'Close Quartz Notes, Blue Music and Orbit Launcher.'}
        def ask(job, *_):
            scenes.append(copy.deepcopy(job['_desktop']))
            return next(actions)
        self.control.run(job, lambda job, **values: job.update(values), lambda job: None, ask)
        return job, scenes

    def test_native_close_is_a_normal_close_without_focus_or_process_kill(self):
        result = computer.close_window(self.windows[11])
        self.assertEqual(result['state'], 'closed')
        self.assertTrue(result['verified'])
        self.user.PostMessageW.assert_called_once_with(11, 0x0010, 0, 0)
        self.user.SetForegroundWindow.assert_not_called()
        self.user.SendInput.assert_not_called()

    def test_changed_window_identity_is_never_closed(self):
        old = self.original[11]
        self.windows[11]['pid'] += 1
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            computer.close_window(old)
        self.user.PostMessageW.assert_not_called()

    def test_normal_close_does_not_require_permission_to_type_in_an_app(self):
        target = self.windows[11]
        target.update(process='outlook.exe', title='Outlook Inbox')
        self.assertFalse(computer.controllable(target))
        self.assertTrue(computer.close_window(target)['verified'])
        self.user.SendInput.assert_not_called()

    def test_secure_desktop_windows_remain_protected_from_close(self):
        target = self.windows[11]
        target.update(process='consent.exe', title='User Account Control')
        with self.assertRaisesRegex(RuntimeError, 'protected'):
            computer.close_window(target)
        self.user.PostMessageW.assert_not_called()

    def test_unsaved_prompt_is_not_discarded_or_reported_closed(self):
        self.user.PostMessageW.side_effect = None
        self.user.PostMessageW.return_value = True
        result = computer.close_window(self.windows[11])
        self.assertFalse(result['verified'])
        self.assertEqual(result['state'], 'still_open')
        self.assertIn('unsaved', result['error'])
        self.user.PostMessageW.assert_called_once()
        self.user.SendInput.assert_not_called()

    def test_tray_hide_is_distinguished_from_window_destruction(self):
        self.user.PostMessageW.side_effect = None
        self.user.PostMessageW.return_value = True
        self.user.IsWindowVisible.return_value = False
        self.user.IsWindowVisible.side_effect = None
        self.assertEqual(computer.close_window(self.windows[11])['state'], 'hidden')
        self.assertIn(11, self.windows)

    def test_cancellation_prevents_close(self):
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            computer.close_window(self.windows[11], check=Mock(side_effect=RuntimeError('cancelled')))
        self.user.PostMessageW.assert_not_called()

    def test_all_three_targets_receive_verified_results_after_disappearing(self):
        job, scenes = self.run_actions([
            {'action': 'close_window', 'target': f'window:{n}'} for n in (13, 11, 12)
        ] + [{'action': 'done', 'message': 'All three requested windows are closed, sir.'}])
        self.assertEqual(list(self.windows), [14])
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(len(job['windowResults']), 3)
        self.assertTrue(all(row['verified'] for row in job['windowResults']))
        self.assertEqual(scenes[1]['actionHistory'][0]['window']['title'], 'Orbit Launcher')
        self.assertEqual(scenes[1]['actionHistory'][0]['result']['windowState'], 'closed')
        self.assertEqual(len(scenes[-1]['actionHistory']), 3)
        self.control.apps.launch.assert_not_called()

    def test_one_refusal_does_not_prevent_remaining_targets_or_claim_success(self):
        self.user.PostMessageW.side_effect = lambda hwnd, *_: False if hwnd == 11 else bool(self.windows.pop(hwnd, None))
        with patch.object(computer.ctypes, 'get_last_error', return_value=5):
            job, scenes = self.run_actions([
                {'action': 'close_window', 'target': f'window:{n}'} for n in (11, 12, 13)
            ] + [{'action': 'done', 'message': 'Closed two; Quartz Notes refused.'}])
        self.assertEqual(list(self.windows), [11, 14])
        self.assertEqual(job['status'], 'failed')
        self.assertIn('denied access', job['summary'])
        self.assertFalse(scenes[1]['lastWindowAction']['verified'])

    def test_control_closing_a_window_is_recorded_as_an_observation(self):
        def invoke(*_args, **_kwargs):
            self.windows.pop(13)
            return {'ok': True, 'status': 'action_requested'}
        with patch.object(computer.Accessibility, 'perform', side_effect=invoke):
            job, scenes = self.run_actions([
                {'action': 'inspect_window', 'target': 'window:13'},
                {'action': 'invoke', 'target': 'close-button'},
                {'action': 'close_window', 'target': 'window:11'},
                {'action': 'close_window', 'target': 'window:12'},
                {'action': 'done', 'message': 'Closed all three.'}])
        self.assertEqual(scenes[2]['actionHistory'][-1]['result']['windowState'], 'closed')
        self.assertIsNone(scenes[2]['selectedWindow'])
        self.assertEqual(list(self.windows), [14])


class SemanticDispatchTests(unittest.TestCase):
    def test_ai_classified_request_gets_generated_ack_and_completion(self):
        controller = Controller(queue.Queue())
        controller.preferences.read = lambda: {'offlineSpeech': 'auto'}
        controller.voice_ready = True
        self.addCleanup(controller.browser.close)
        self.addCleanup(controller.research.close)
        self.addCleanup(controller.tasks.close)
        decision = {'action': 'computer', 'model_tier': 'sol', 'needs_clarification': False}
        question = 'Close Quartz Notes, Blue Music and Orbit Launcher.'
        acknowledgement = 'I will close those three apps, sir.'
        def run(job, update, *_):
            self.assertEqual(job['question'], question)
            self.assertEqual(job['_modelTier'], 'sol')
            update(job, status='completed', summary='All three windows are closed, sir.', finished=time.time())
        with patch('desktop.controller.classify_request', return_value=decision) as classify, \
             patch('desktop.controller.acknowledge', return_value=acknowledgement) as ack, \
             patch('desktop.research.local_request', return_value={'conversationModel': 'gpt-6-luna'}), \
             patch.object(controller.computer, 'run', side_effect=run), \
             patch.object(controller, 'speak') as speak:
            result = controller.submit(question, speak=False)
            self.assertEqual(result['text'], acknowledgement)
            self.assertEqual(result['status'], 'queued')
            controller.tasks.pending.join()
            classify.assert_called_once()
            ack.assert_called_once()
            self.assertEqual(controller.activity_status(result['taskId'])['status'], 'completed')
            controller.deliver_task_completion()
            speak.assert_called_once_with('All three windows are closed, sir.')
            self.assertEqual(controller.last_task_id, result['taskId'])


if __name__ == '__main__':
    unittest.main()
