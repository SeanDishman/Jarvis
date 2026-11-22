import json
import time
import unittest
from unittest.mock import Mock, patch

from desktop.accessibility import Accessibility, click_control, describe, execute, worker_request

TARGET = {'hwnd': 41, 'pid': 90001, 'process': 'unknown-app.exe', 'title': 'Workspace', 'rect': [0, 0, 800, 600]}
ROW = {'_runtime': [42, 41, 7], 'name': 'Run preview', 'role': 'ButtonControl', 'automationId': 'preview',
       'rect': [20, 20, 120, 50], 'enabled': True, 'offscreen': False, 'actions': ['invoke']}


class SnapshotTests(unittest.TestCase):
    def test_native_controls_are_chosen_from_the_fresh_snapshot_only(self):
        surface = Accessibility()
        with patch('desktop.accessibility.worker_request', return_value={'ok': True, 'controls': [ROW]}) as worker:
            first = surface.inspect(TARGET)
            old_id = first['controls'][0]['id']
            second = surface.inspect(TARGET)
            identifier = second['controls'][0]['id']
            self.assertNotEqual(identifier, old_id)
            self.assertNotIn('_runtime', second['controls'][0])
            worker.reset_mock()
            for target, chosen, action in ((TARGET, old_id, 'invoke'), ({**TARGET, 'pid': 999}, identifier, 'invoke'),
                                           (TARGET, identifier, 'set_value')):
                with self.assertRaises(RuntimeError):
                    surface.perform(target, chosen, action)
            worker.assert_not_called()
            worker.return_value = {'ok': True, 'status': 'action_requested'}
            self.assertEqual(surface.perform(TARGET, identifier, 'invoke')['status'], 'action_requested')
            self.assertEqual(worker.call_args.args[0]['control']['_runtime'], ROW['_runtime'])
            with self.assertRaises(RuntimeError):
                surface.perform(TARGET, identifier, 'invoke')
            self.assertEqual(worker.call_count, 1)

    def test_values_are_literal_and_can_clear_fields(self):
        surface = Accessibility()
        with patch('desktop.accessibility.worker_request', return_value={'ok': True, 'controls': [{**ROW, 'actions': ['set_value']}]}) as worker:
            for text in ('', 'Hello {ENTER} $name; é'):
                identifier = surface.inspect(TARGET)['controls'][0]['id']
                surface.perform(TARGET, identifier, 'set_value', text)
                self.assertEqual(worker.call_args.args[0]['text'], text)
            identifier = surface.inspect(TARGET)['controls'][0]['id']
            with self.assertRaises(RuntimeError):
                surface.perform(TARGET, identifier, 'set_value', '\x00')

    def test_text_a_paused_page_has_not_reported_is_remembered_until_it_is(self):
        # A covered browser page keeps reporting a field's old value, which would invite typing the text twice.
        surface = Accessibility()
        field = {**ROW, 'role': 'EditControl', 'actions': ['set_value'], 'value': ''}
        with patch('desktop.accessibility.worker_request', return_value={'ok': True, 'controls': [field]}) as worker:
            identifier = surface.inspect(TARGET)['controls'][0]['id']
            worker.return_value = {'ok': True, 'status': 'sent_in_background', 'verified': False}
            surface.perform(TARGET, identifier, 'set_value', 'first draft')
            worker.return_value = {'ok': True, 'controls': [field]}
            row = surface.inspect(TARGET)['controls'][0]
            self.assertEqual((row['value'], row['typedByJarvis']), ('', 'first draft'))
            # Replacing it deletes what was typed, even though the page still reports the old value.
            worker.return_value = {'ok': True, 'status': 'sent_in_background', 'verified': False}
            surface.perform(TARGET, row['id'], 'set_value', 'final text')
            self.assertEqual(worker.call_args.args[0]['typed'], 'first draft')
            worker.return_value = {'ok': True, 'controls': [{**field, 'value': 'final text'}]}
            self.assertNotIn('typedByJarvis', surface.inspect(TARGET)['controls'][0])
            self.assertEqual(surface.typed, {})

    def test_unavailable_provider_keeps_visual_fallback_available(self):
        with patch('desktop.accessibility.worker_request', side_effect=RuntimeError('Provider unavailable')):
            snapshot = Accessibility().inspect(TARGET)
        self.assertFalse(snapshot['available'])
        self.assertEqual(snapshot['controls'], [])

    def test_large_tree_is_bounded_for_the_model_request(self):
        with patch('desktop.accessibility.worker_request', return_value={'ok': True, 'controls': [{**ROW, 'name': 'x' * 240}] * 180}):
            snapshot = Accessibility().inspect(TARGET)
        self.assertTrue(snapshot['truncated'])
        self.assertLess(len(json.dumps(snapshot)), 16200)

    def test_failed_action_is_not_repeated_until_the_control_changes(self):
        surface = Accessibility()
        with patch('desktop.accessibility.worker_request', return_value={'ok': True, 'controls': [ROW]}) as worker:
            identifier = surface.inspect(TARGET)['controls'][0]['id']
            worker.side_effect = RuntimeError('App refused action')
            with self.assertRaises(RuntimeError):
                surface.perform(TARGET, identifier, 'invoke')
            worker.side_effect = None
            same = surface.inspect(TARGET)['controls'][0]
            self.assertNotIn('invoke', same['actions'])
            self.assertIn('invoke', same['actionErrors'])
            worker.return_value = {'ok': True, 'controls': [{**ROW, 'name': 'Ready to preview'}]}
            changed = surface.inspect(TARGET)['controls'][0]
            self.assertIn('invoke', changed['actions'])

    def test_cancellation_before_start_launches_no_helper(self):
        with patch('desktop.accessibility.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                worker_request({}, check=Mock(side_effect=RuntimeError('cancelled')))
        spawn.assert_not_called()

    def test_actionable_controls_survive_a_large_decorative_tree(self):
        decoration = {**ROW, 'name': 'Decorative image ' * 12, 'actions': ['scroll_into_view', 'click_control']}
        with patch('desktop.accessibility.worker_request', return_value={'ok': True, 'controls': [decoration] * 120 + [ROW]}):
            snapshot = Accessibility().inspect(TARGET)
        self.assertEqual(snapshot['controls'][0]['name'], ROW['name'])
        self.assertTrue(snapshot['truncated'])


class NativeControlTests(unittest.TestCase):
    def fixture(self):
        import uiautomation as auto
        control = Mock()
        control.IsPassword = False
        control.IsOffscreen = False
        control.IsEnabled = True
        control.Name = ROW['name']
        control.ControlTypeName = ROW['role']
        control.AutomationId = ROW['automationId']
        control.GetRuntimeId.return_value = tuple(ROW['_runtime'])
        control.BoundingRectangle = Mock(left=20, top=20, right=120, bottom=50)
        pattern = Mock()
        pattern.Invoke.return_value = True
        control.GetPattern.side_effect = lambda identifier: pattern if identifier == auto.PatternId.InvokePattern else None
        return auto, control, pattern

    def test_control_invocation_uses_app_patterns_without_foreground_or_coordinates(self):
        auto, control, pattern = self.fixture()
        with patch.object(auto, 'ControlFromHandle'), patch.object(auto, 'WalkControl', return_value=iter([(control, 1)])), \
             patch('desktop.accessibility.validate_window'), patch('desktop.computer.USER') as user:
            result = execute({'action': 'invoke', 'target': TARGET, 'control': ROW})
        pattern.Invoke.assert_called_once_with(waitTime=0)
        user.SendInput.assert_not_called()
        user.SetForegroundWindow.assert_not_called()
        self.assertEqual(result['status'], 'action_requested')

    def test_replaced_control_is_not_invoked(self):
        auto, control, pattern = self.fixture()
        control.Name = 'Different action'
        with patch.object(auto, 'ControlFromHandle'), patch.object(auto, 'WalkControl', return_value=iter([(control, 1)])), \
             patch('desktop.accessibility.validate_window'), self.assertRaisesRegex(RuntimeError, 'changed'):
            execute({'action': 'invoke', 'target': TARGET, 'control': ROW})
        pattern.Invoke.assert_not_called()

    def test_disabled_offscreen_and_password_controls_are_not_actionable(self):
        auto, control, pattern = self.fixture()
        control.IsEnabled = False
        self.assertEqual(describe(control, auto)[0]['actions'], [])
        control.IsOffscreen = True
        self.assertIsNone(describe(control, auto)[0])
        control.IsOffscreen = False
        control.IsPassword = True
        self.assertIsNone(describe(control, auto)[0])

    def test_zero_area_hidden_controls_are_not_exposed_as_actionable(self):
        auto, control, _ = self.fixture()
        control.BoundingRectangle = Mock(left=0, top=0, right=0, bottom=0)
        self.assertIsNone(describe(control, auto)[0])

    def test_broken_uia_tree_uses_msaa_and_retains_item_context(self):
        auto, control, _ = self.fixture()
        parent = Mock(wraps=control)
        parent.Name = 'Quarterly notes'
        parent.ControlTypeName = 'TabItemControl'
        parent.IsPassword, parent.IsOffscreen, parent.IsEnabled = False, False, True
        parent.AutomationId = ''
        parent.GetRuntimeId.return_value = [7, 8, 9]
        parent.GetPattern.return_value = None
        parent.BoundingRectangle = control.BoundingRectangle
        with patch.object(auto, 'ControlFromHandle'), patch.object(auto, 'WalkControl', side_effect=RuntimeError('Broken subtree')), \
             patch('desktop.accessibility_msaa.walk_controls', return_value=iter([(parent, 0), (control, 1)])), \
             patch('desktop.accessibility.validate_window'):
            result = execute({'action': 'inspect', 'target': TARGET})
        child = result['controls'][-1]
        self.assertEqual(child['_provider'], 'msaa')
        self.assertEqual(child['ancestors'], [{'name': 'Quarterly notes', 'role': 'TabItemControl'}])
        self.assertIn('click_control', child['actions'])

    def test_msaa_action_revalidates_the_control_in_the_same_provider(self):
        auto, control, pattern = self.fixture()
        with patch.object(auto, 'WalkControl') as standard, \
             patch('desktop.accessibility_msaa.walk_controls', return_value=iter([(control, 0)])), \
             patch('desktop.accessibility.validate_window'):
            result = execute({'action': 'invoke', 'target': TARGET, 'control': {**ROW, '_provider': 'msaa'}})
        self.assertTrue(result['ok'])
        standard.assert_not_called()
        pattern.Invoke.assert_called_once_with(waitTime=0)


class ChromiumControlTests(unittest.TestCase):
    """Chrome, Edge, Opera and Electron apps come forward when UI Automation invokes or edits their content, which
    would send the owner's typing into them, so Jarvis uses his own background pointer and typing there."""

    def fixture(self):
        import uiautomation as auto
        control = Mock(NativeWindowHandle=0, IsPassword=False, IsOffscreen=False, IsEnabled=True, Name=ROW['name'],
                       ControlTypeName=ROW['role'], AutomationId=ROW['automationId'])
        control.GetRuntimeId.return_value = tuple(ROW['_runtime'])
        control.BoundingRectangle = Mock(left=20, top=20, right=120, bottom=50)
        invoke, field = Mock(), Mock(Value='old', IsReadOnly=False)
        invoke.Invoke.return_value = field.SetValue.return_value = True
        control.GetPattern.side_effect = {auto.PatternId.InvokePattern: invoke, auto.PatternId.ValuePattern: field}.get
        return auto, control, invoke, field

    def run_action(self, auto, control, payload, **patches):
        with patch.object(auto, 'ControlFromHandle'), patch.object(auto, 'WalkControl', return_value=iter([(control, 1)])), \
             patch('desktop.accessibility.validate_window'), patch('desktop.computer.chromium_window', return_value=True), \
             patch('desktop.accessibility.click_control') as click, \
             patch('desktop.computer.background_keys', side_effect=patches.get('keys')), \
             patch('desktop.accessibility.time', **patches.get('clock', {'monotonic.return_value': 0})):
            return click, execute({'target': TARGET, 'control': {**ROW, 'actions': ['invoke', 'set_value']}, **payload})

    def test_invoke_is_a_background_click_instead_of_ui_automation(self):
        auto, control, invoke, _ = self.fixture()
        click, result = self.run_action(auto, control, {'action': 'invoke'})
        self.assertEqual(click.call_args.args[3], 'left')
        self.assertIs(click.call_args.args[1], control)
        invoke.Invoke.assert_not_called()
        self.assertEqual((result['status'], result['method']), ('sent_in_background', 'own_pointer'))

    def editing(self, field, sent, types=True):
        """Background keys as a field with its caret at the end handles them."""
        def keys(hwnd, action):
            sent.append((hwnd, action.get('key', action.get('text'))))
            if action['action'] == 'key' and action['key'] == 'backspace':
                field.Value = field.Value[:-1]
            elif action['action'] == 'type' and types:
                field.Value = action['text']
        return keys

    def test_set_value_clears_the_field_and_lets_the_deletions_drain_before_typing(self):
        # Chromium pulls queued text ahead of queued key presses, so typing at once would mix old and new text.
        auto, control, _, field = self.fixture()
        sent = []
        clock = {'monotonic.return_value': 0, 'sleep.side_effect': lambda seconds: sent.append(('pause', seconds))}
        click, result = self.run_action(auto, control, {'action': 'set_value', 'text': 'new text'}, keys=self.editing(field, sent), clock=clock)
        click.assert_called_once()
        self.assertEqual(sent[:6], [(41, 'backspace')] * 3 + [(41, 'delete')] * 3)
        self.assertEqual(sent[6][0], 'pause')
        self.assertGreaterEqual(sent[6][1], .2)
        self.assertEqual(sent[7:], [(41, 'new text')])
        field.SetValue.assert_not_called()
        self.assertEqual((result['verified'], result['value']), (True, 'new text'))

    def test_text_typed_earlier_is_deleted_even_when_the_page_reports_less(self):
        auto, control, _, field = self.fixture()
        sent = []
        self.run_action(auto, control, {'action': 'set_value', 'text': 'new', 'typed': 'first draft'}, keys=self.editing(field, sent))
        self.assertEqual(sent, [(41, 'backspace')] * 11 + [(41, 'delete')] * 11 + [(41, 'new')])

    def test_an_empty_field_gets_no_deletions(self):
        # In tag inputs a backspace in an empty field removes the previous entry.
        auto, control, _, field = self.fixture()
        field.Value = ''
        sent = []
        clock = {'monotonic.return_value': 0, 'sleep.side_effect': lambda seconds: sent.append(('pause', seconds))}
        self.run_action(auto, control, {'action': 'set_value', 'text': 'new text'}, keys=self.editing(field, sent), clock=clock)
        self.assertEqual(sent, [(41, 'new text')])

    def test_a_field_that_has_not_reported_the_text_is_not_called_a_failure(self):
        # Saying it failed would invite typing the text a second time.
        auto, control, _, field = self.fixture()
        clock = {'monotonic.side_effect': [0, 0, 0, 100]}
        _, result = self.run_action(auto, control, {'action': 'set_value', 'text': 'new text'},
                                    keys=self.editing(field, [], types=False), clock=clock)
        self.assertTrue(result['ok'])
        self.assertFalse(result['verified'])
        self.assertEqual(result['reported'], '')
        self.assertIn('before typing again', result['note'])
        field.SetValue.assert_not_called()


class WindowMessageTests(unittest.TestCase):
    def fixture(self):
        from desktop import computer
        user = Mock()
        user.GetAncestor.return_value = TARGET['hwnd']
        user.IsIconic.return_value = False
        user.SendMessageTimeoutW.return_value = 1
        user.ScreenToClient.return_value = 1
        control = Mock(NativeWindowHandle=42)
        return computer, user, control

    def test_background_click_targets_only_the_control_window(self):
        computer, user, control = self.fixture()
        with patch.object(computer, 'USER', user), patch.object(computer, 'window_info', return_value=TARGET):
            click_control(TARGET, control, ROW, 'middle')
        self.assertEqual([call.args[1:3] for call in user.SendMessageTimeoutW.call_args_list],
                         [(0x0200, 0), (0x0207, 0x10), (0x0208, 0)])
        self.assertTrue(all(call.args[0] == 42 for call in user.SendMessageTimeoutW.call_args_list))
        user.SetForegroundWindow.assert_not_called()
        user.SendInput.assert_not_called()
        user.SetCursorPos.assert_not_called()

    def test_foreign_moved_minimized_and_invalid_controls_cannot_receive_clicks(self):
        computer, user, control = self.fixture()
        cases = [('foreign', TARGET, ROW), ('moved', {**TARGET, 'rect': [9, 0, 800, 600]}, ROW),
                 ('minimized', TARGET, ROW), ('empty', TARGET, {**ROW, 'rect': [0, 0, 0, 0]}),
                 ('outside', TARGET, {**ROW, 'rect': [900, 0, 950, 25]})]
        for kind, current, row in cases:
            with self.subTest(kind=kind):
                user.GetAncestor.return_value = 99 if kind == 'foreign' else TARGET['hwnd']
                user.IsIconic.return_value = kind == 'minimized'
                with patch.object(computer, 'USER', user), patch.object(computer, 'window_info', return_value=current), \
                     self.assertRaises(RuntimeError):
                    click_control(TARGET, control, row, 'left')
        user.SendMessageTimeoutW.assert_not_called()

    def test_unresponsive_window_does_not_report_a_successful_click(self):
        computer, user, control = self.fixture()
        user.SendMessageTimeoutW.return_value = 0
        with patch.object(computer, 'USER', user), patch.object(computer, 'window_info', return_value=TARGET), \
             self.assertRaisesRegex(RuntimeError, 'did not accept'):
            click_control(TARGET, control, ROW, 'left')
        user.PostMessageW.assert_called_once()


if __name__ == '__main__':
    unittest.main()
