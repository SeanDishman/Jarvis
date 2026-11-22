from contextlib import ExitStack
import ctypes
import queue
import time
import unittest
from unittest.mock import Mock, patch

from desktop import computer
from desktop.controller import Controller


class RoutingTests(unittest.TestCase):
    def test_semantic_app_goals_reach_the_worker_unchanged(self):
        for question in ('Open Opera GX and search for gaming laptops',
                         'Bring up Dropbox and find my tax folder',
                         'Open Nebula Shelf and show search results for quarterly notes',
                         'In Nebula Shelf, put quarterly notes in the search box',
                         'Use the app on my screen to write a short birthday greeting'):
            with self.subTest(question=question):
                controller = Controller(queue.Queue())
                controller.tasks = None
                decision = {'action': 'computer', 'model_tier': 'sol', 'needs_clarification': False}
                with patch('desktop.controller.classify_request', return_value=decision), \
                     patch.object(controller.preferences, 'read', return_value={'offlineSpeech': 'auto'}), \
                     patch.object(controller.research, 'start', return_value={'ok': True}) as start, \
                     patch.object(controller.apps, 'launch') as launch, patch.object(controller, 'ask_model') as ask:
                    result = controller.respond(question, speak=False)
                self.assertTrue(result['ok'], result)
                start.assert_called_once_with(question, 'computer', model_tier='sol')
                launch.assert_not_called()
                ask.assert_not_called()
                self.assertEqual(result['tools'], ['start_computer_task'])

    def test_ambiguous_app_goal_does_not_act(self):
        controller = Controller(queue.Queue())
        decision = {'action': 'computer', 'model_tier': 'astra', 'needs_clarification': True}
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Which file?'}]}]}
        capabilities = []
        def ask(*args):
            capabilities.append(controller.request['capabilities'][:])
            return response
        with patch('desktop.controller.classify_request', return_value=decision), \
             patch.object(controller, 'ask_model', side_effect=ask), \
             patch.object(controller.research, 'start') as start:
            result = controller.respond('Change that file in the app', speak=False)
        self.assertTrue(result['ok'])
        start.assert_not_called()
        self.assertEqual(capabilities, [[]])


class DesktopLoopTests(unittest.TestCase):
    def fixture(self, name='Nebula Shelf'):
        stack = ExitStack()
        self.addCleanup(stack.close)
        apps = Mock()
        apps.apps = [{'Name': name, 'AppID': 'installed-entry'}]
        control = computer.ComputerControl(apps)
        target = {'hwnd': 41, 'pid': 98765, 'process': 'arbitrary-ui.exe', 'title': 'Home - ' + name,
                  'rect': [30, 40, 830, 640]}
        state = {'opened': False, 'foreground': 0, 'phase': 'home'}
        def launch(_name):
            state['opened'] = True
            return {'ok': True}
        apps.launch.side_effect = launch
        stack.enter_context(patch.object(computer, 'require_desktop'))
        user = stack.enter_context(patch.object(computer, 'USER'))
        user.GetForegroundWindow.side_effect = lambda: state['foreground']
        user.IsIconic.return_value = False
        user.IsZoomed.return_value = False
        user.GetCursorPos.side_effect = lambda pointer: setattr(pointer._obj, 'x', 100)
        user.GetSystemMetrics.return_value = 0
        user.SetThreadDpiAwarenessContext.return_value = 0
        stack.enter_context(patch.object(computer, 'window_info', side_effect=lambda hwnd: target if hwnd == 41 and state['opened'] else None))
        stack.enter_context(patch.object(computer, 'visible_windows', side_effect=lambda: [target] if state['opened'] else []))
        stack.enter_context(patch.object(computer, 'bring_to_front', side_effect=lambda hwnd: state.update(foreground=hwnd)))
        stack.enter_context(patch.object(computer, 'keep_on_top'))
        stack.enter_context(patch.object(computer.time, 'sleep'))
        stack.enter_context(patch.object(computer, 'monitors', return_value=[{'device': 'fixture', 'rect': [0, 0, 1200, 800], 'work': [0, 0, 1200, 760], 'primary': True, 'internal': True}]))
        stack.enter_context(patch.object(computer, 'capture_rect', return_value={'image': 'overview'}))
        stack.enter_context(patch.object(computer.Accessibility, 'inspect', return_value={'available': False, 'controls': []}))
        capture = stack.enter_context(patch.object(computer, 'capture_window', side_effect=lambda row: {'image': state['phase']}))
        def apply(_target, frame, action, owner, check):
            check()
            if action['action'] == 'type':
                state['phase'] = 'typed'
            if action.get('key') == 'enter':
                state['phase'] = 'results'
        inputs = stack.enter_context(patch.object(computer, 'apply_action', side_effect=apply))
        return control, apps, target, state, capture, inputs

    def test_open_select_type_submit_and_verify_with_unrelated_app_names(self):
        for name in ('Opera GX Browser', 'Dropbox', 'Nebula Shelf'):
            with self.subTest(app=name):
                control, apps, target, state, capture, inputs = self.fixture(name)
                actions = iter([{'action': 'open_app', 'target': 'app:0'},
                                {'action': 'focus_window', 'target': 'window:41'},
                                {'action': 'key', 'key': 'ctrl+f'},
                                {'action': 'type', 'text': 'quarterly notes'},
                                {'action': 'key', 'key': 'enter'}, {'action': 'done', 'message': 'Found the results.'}])
                job = {'question': 'Use ' + name + ' to find quarterly notes', '_foreground': 0}
                frames, scenes = [], []
                def ask(job, frame, steps, readonly):
                    frames.append(frame['image'])
                    scenes.append(job['_desktop'])
                    return next(actions)
                control.run(job, lambda job, **values: job.update(values), lambda job: None, ask)
                apps.launch.assert_called_once_with(name)
                self.assertEqual(job['status'], 'completed')
                self.assertEqual(frames, ['overview', 'overview', 'home', 'home', 'typed', 'results'])
                self.assertEqual(scenes[0]['applications'], [{'id': 'app:0', 'name': name}])
                self.assertEqual(scenes[0]['windows'], [])
                self.assertEqual(scenes[2]['selectedWindow'], 'window:41')
                self.assertEqual(inputs.call_count, 3)
                self.assertEqual(capture.call_count, 4)

    def test_fabricated_app_id_never_launches(self):
        control, apps, _, _, _, inputs = self.fixture()
        with self.assertRaisesRegex(RuntimeError, 'installed-app list'):
            control.run({'question': 'Open my app'}, lambda job, **values: job.update(values), lambda job: None,
                        lambda *args: {'action': 'open_app', 'target': 'app:999'})
        apps.launch.assert_not_called()
        inputs.assert_not_called()

    def test_repeated_launch_request_starts_one_process(self):
        control, apps, _, _, _, _ = self.fixture()
        actions = iter([{'action': 'open_app', 'target': 'app:0'}, {'action': 'open_app', 'target': 'app:0'}, {'action': 'done'}])
        job = {'question': 'Open Nebula Shelf'}
        control.run(job, lambda job, **values: job.update(values), lambda job: None, lambda *_: next(actions))
        apps.launch.assert_called_once_with('Nebula Shelf')
        self.assertEqual(job['_desktop']['launchRequests'][0]['id'], 'app:0')

    def test_partial_completion_cannot_mark_a_desktop_task_successful(self):
        control, _, _, _, _, inputs = self.fixture()
        job = {'question': 'Close the windows and terminate their processes'}
        with self.assertRaisesRegex(RuntimeError, 'process termination is unavailable'):
            control.run(job, lambda job, **values: job.update(values), lambda job: None,
                        lambda *_: {'action': 'done', 'outcome': 'partial',
                                    'message': 'Windows closed; process termination is unavailable.'})
        self.assertNotEqual(job.get('status'), 'completed')
        inputs.assert_not_called()

    def test_one_window_is_minimized_and_another_put_where_it_was_with_the_pointer_shown(self):
        control, _, target, state, _, inputs = self.fixture()
        state['opened'] = True
        was = {'rect': [30, 40, 830, 640], 'maximized': False, 'monitor': 'fixture'}
        moved = {'verified': True, 'state': 'moved', 'rect': [30, 40, 830, 640], 'monitor': 'fixture', 'maximized': False, 'error': ''}
        place = {'left': 30, 'top': 40, 'right': 830, 'bottom': 640, 'maximized': False}
        actions = iter([{'action': 'minimize_window', 'target': 'window:41'},
                        {'action': 'move_window', 'target': 'window:41', **place}, {'action': 'done'}])
        scenes, shown = [], []
        control.pointer = lambda **row: shown.append(row)
        def ask(job, *_):
            scenes.append(job['_desktop'])
            return next(actions)
        job = {'question': 'Minimize this and put the other window where it was'}
        with patch.object(computer, 'minimize_window') as minimize, patch.object(computer, 'window_place', return_value=was), \
             patch.object(computer, 'window_spot', return_value=(700, 55)), \
             patch('desktop.window_moves.place_window', return_value=moved) as placing:
            control.run(job, lambda job, **values: job.update(values), lambda job: None, ask)
        minimize.assert_called_once()
        self.assertEqual(shown[0], {'x': 700, 'y': 55, 'action': 'click'}, 'His pointer goes to the minimize button first')
        self.assertEqual(shown[-1], {'hide': True})
        self.assertEqual(scenes[1]['lastAction']['result']['was'], was, 'The result keeps where the minimized window was')
        self.assertEqual({key: placing.call_args.args[1][key] for key in place}, place)
        self.assertEqual(placing.call_args.kwargs['point'], control.point, 'The move is drawn with his own pointer')
        self.assertEqual(scenes[2]['lastAction']['result']['state'], 'moved')
        self.assertEqual((job['status'], state['foreground']), ('completed', 0))
        inputs.assert_not_called()

    def test_a_window_that_could_not_be_placed_fails_the_task_unless_a_later_move_succeeds(self):
        moved = {'verified': True, 'state': 'moved', 'rect': [0, 0, 800, 600], 'monitor': 'fixture', 'maximized': False, 'error': ''}
        move = {'action': 'move_window', 'target': 'window:41', 'left': 0, 'top': 0, 'right': 800, 'bottom': 600, 'maximized': False}
        for outcomes, status in (([RuntimeError('Most of that place is off the connected displays.')], 'failed'),
                                 ([RuntimeError('Most of that place is off the connected displays.'), moved], 'completed')):
            with self.subTest(status=status):
                control, _, _, state, _, _ = self.fixture()
                state['opened'] = True
                actions = iter([move] * len(outcomes) + [{'action': 'done', 'message': 'Placed it.'}])
                job = {'question': 'Put that window on the left'}
                with patch('desktop.window_moves.place_window', side_effect=outcomes):
                    control.run(job, lambda job, **values: job.update(values), lambda job: None, lambda *_: next(actions))
                self.assertEqual(job['status'], status)
                if status == 'failed':
                    self.assertIn('off the connected displays', job['summary'])

    def test_code_is_never_typed_into_an_app_to_show_a_drawing(self):
        control, _, _, state, _, inputs = self.fixture()
        state['opened'] = True
        picture = "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'><title>Amplitude illustrated</title></svg>"
        actions = iter([{'action': 'inspect_window', 'target': 'window:41'},
                        {'action': 'set_value', 'target': 'address-bar', 'text': picture},
                        {'action': 'type', 'text': '  JavaScript:alert(1)'},
                        {'action': 'blocked', 'message': 'A drawing needs a drawn page, not PC control.'}])
        scenes = []
        def ask(job, *_):
            scenes.append(job['_desktop'])
            return next(actions)
        with patch.object(computer.Accessibility, 'perform') as perform, \
             self.assertRaisesRegex(RuntimeError, 'needs a drawn page'):
            control.run({'question': 'Draw what amplitude is'}, lambda job, **values: job.update(values), lambda job: None, ask)
        perform.assert_not_called()
        inputs.assert_not_called()
        refused = [scene['lastAction']['result'] for scene in scenes[2:]]
        self.assertEqual([row['status'] for row in refused], ['input_not_sent', 'input_not_sent'])
        self.assertIn('not allowed', refused[0]['error'])

    def test_native_control_works_while_another_window_has_focus(self):
        control, _, target, state, capture, inputs = self.fixture()
        state['opened'] = True
        actions = iter([{'action': 'inspect_window', 'target': 'window:41'},
                        {'action': 'invoke', 'target': 'live-control'}, {'action': 'done'}])
        job = {'question': 'Use the app\'s Preview button'}
        with patch.object(computer.Accessibility, 'perform', return_value={'ok': True, 'status': 'action_requested'}) as perform:
            control.run(job, lambda job, **values: job.update(values), lambda job: None, lambda *_: next(actions))
        self.assertEqual(state['foreground'], 0)
        self.assertEqual(perform.call_args.args[:3], (target, 'live-control', 'invoke'))
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(job['status'], 'completed')
        inputs.assert_not_called()

    def test_pointer_input_after_focus_loss_waits_for_a_fresh_observation(self):
        control, _, _, state, capture, inputs = self.fixture()
        state['opened'] = True
        actions = iter([{'action': 'focus_window', 'target': 'window:41'},
                        {'action': 'click', 'x': 10, 'y': 10}, {'action': 'click', 'x': 15, 'y': 20}, {'action': 'done'}])
        job = {'question': 'Use the app\'s button'}
        def ask(*_):
            action = next(actions)
            if action.get('x') == 10:
                state['foreground'] = 0
            return action
        control.run(job, lambda job, **values: job.update(values), lambda job: None, ask)
        inputs.assert_called_once()
        self.assertEqual(inputs.call_args.args[2]['x'], 15)
        self.assertEqual(state['foreground'], 41)
        self.assertEqual(capture.call_count, 3)

    def test_inspecting_a_covered_window_cannot_implicitly_steal_focus(self):
        control, _, _, state, _, inputs = self.fixture()
        state['opened'] = True
        actions = iter([{'action': 'inspect_window', 'target': 'window:41'},
                        {'action': 'click', 'x': 10, 'y': 10}, {'action': 'done'}])
        job = {'question': 'Use the app in the background'}
        control.run(job, lambda job, **values: job.update(values), lambda job: None, lambda *_: next(actions))
        self.assertEqual(state['foreground'], 0)
        self.assertFalse(job['_desktop']['foregroundInput'])
        self.assertEqual(job['_desktop']['lastAction']['result']['status'], 'input_not_sent')
        inputs.assert_not_called()

    def test_native_failure_is_visible_on_the_next_fresh_observation(self):
        control, _, _, state, _, inputs = self.fixture()
        state['opened'] = True
        actions = iter([{'action': 'inspect_window', 'target': 'window:41'},
                        {'action': 'invoke', 'target': 'old-control'}, {'action': 'blocked', 'message': 'Control is unavailable'}])
        job = {'question': 'Use the app\'s button'}
        with patch.object(computer.Accessibility, 'perform', side_effect=RuntimeError('Control changed')):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                control.run(job, lambda job, **values: job.update(values), lambda job: None, lambda *_: next(actions))
        self.assertFalse(job['_desktop']['lastControlAction']['ok'])
        self.assertIn('Control changed', job['_desktop']['lastControlAction']['error'])
        inputs.assert_not_called()

    def test_native_control_obeys_the_pointer_stop(self):
        control, _, _, state, _, inputs = self.fixture()
        state['opened'] = True
        actions = iter([{'action': 'inspect_window', 'target': 'window:41'}, {'action': 'invoke', 'target': 'control'}])
        job = {'question': 'Use the app\'s button'}
        def perform(*args, check=None, **kwargs):
            check()
        with patch.object(computer, 'check_pointer_stop', side_effect=computer.InputStopped('pointer stop')), \
             patch.object(computer.Accessibility, 'perform', side_effect=perform):
            with self.assertRaisesRegex(computer.InputStopped, 'pointer stop'):
                control.run(job, lambda job, **values: job.update(values), lambda job: None, lambda *_: next(actions))
        inputs.assert_not_called()

    def test_input_requires_a_selected_window(self):
        control, _, _, _, _, inputs = self.fixture()
        with self.assertRaisesRegex(RuntimeError, 'Select a window'):
            control.run({'question': 'Search in the app'}, lambda job, **values: job.update(values), lambda job: None,
                        lambda *args: {'action': 'type', 'text': 'quarterly notes'})
        inputs.assert_not_called()

    def test_cancelled_task_does_not_wait_for_another_desktop_job(self):
        control, _, _, _, _, inputs = self.fixture()
        control.lock.acquire()
        self.addCleanup(control.lock.release)
        def cancelled(job):
            raise RuntimeError('cancelled by owner')
        with self.assertRaisesRegex(RuntimeError, 'cancelled by owner'):
            control.run({'question': 'Open my app'}, Mock(), cancelled, Mock())
        inputs.assert_not_called()


class GeneralInputTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.target = {'hwnd': 11, 'pid': 98765, 'process': 'arbitrary-app.exe', 'title': 'Workspace', 'rect': [0, 0, 800, 600]}
        self.frame = {'rect': self.target['rect'], 'width': 800, 'height': 600, 'captured': time.monotonic()}
        stack.enter_context(patch.object(computer, 'require_desktop'))
        stack.enter_context(patch.object(computer, 'window_info', return_value=self.target))
        self.user = stack.enter_context(patch.object(computer, 'USER'))
        self.user.GetForegroundWindow.return_value = 11
        self.user.GetSystemMetrics.return_value = 0
        self.user.GetAncestor.return_value = 11
        self.user.GetCursorPos.side_effect = lambda pointer: setattr(pointer._obj, 'x', 100)
        self.send = stack.enter_context(patch.object(computer, 'send_inputs'))

    def test_generated_task_text_and_unicode_work_in_an_unlisted_app(self):
        text = 'Happy birthday! \U0001f389'
        computer.apply_action(self.target, self.frame, {'action': 'type', 'text': text}, 'Write a birthday greeting')
        encoded = b''.join(int(call.args[0].data.ki.wScan).to_bytes(2, 'little') for call in self.send.call_args_list)
        self.assertEqual(encoded.decode('utf-16-le'), text)

    def test_typing_checks_cancellation_between_characters(self):
        check = Mock(side_effect=[None, RuntimeError('cancelled')])
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            computer.apply_action(self.target, self.frame, {'action': 'type', 'text': 'hello'}, check=check)
        self.assertEqual(self.send.call_count, 1)

    def test_shortcut_releases_modifiers_when_focus_changes(self):
        self.user.GetForegroundWindow.side_effect = [11, 11, 22]
        with self.assertRaisesRegex(RuntimeError, 'Focus changed'):
            computer.apply_action(self.target, self.frame, {'action': 'key', 'key': 'ctrl+l'})
        actual = [(call.args[0].data.ki.wVk, call.args[0].data.ki.dwFlags) for call in self.send.call_args_list]
        self.assertEqual(actual, [(17, 0), (17, 2)])

    def test_shortcuts_send_down_then_release_in_reverse_order(self):
        computer.apply_action(self.target, self.frame, {'action': 'key', 'key': 'ctrl+l'})
        actual = [(call.args[0].data.ki.wVk, call.args[0].data.ki.dwFlags) for call in self.send.call_args_list]
        self.assertEqual(actual, [(17, 0), (76, 0), (76, 2), (17, 2)])

    def test_right_click_uses_right_mouse_button(self):
        computer.apply_action(self.target, self.frame, {'action': 'right_click', 'x': 20, 'y': 30})
        self.assertEqual([value.data.mi.dwFlags for value in self.send.call_args_list[-1].args], [8, 16])

    def test_unavailable_shortcut_never_sends_input(self):
        for key in ('win+r', ['ctrl', 'l']):
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, 'not available'):
                computer.apply_action(self.target, self.frame, {'action': 'key', 'key': key})
        self.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
