import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from desktop import process_control as pc
from desktop.controller import Controller
from desktop.intent import classify_request
from tests.desktop.intent_fixtures import decision


def row(pid, name):
    return {'pid': pid, 'parentPid': 9999, 'name': name + '.exe', 'path': 'C:\\Apps\\' + name + '.exe'}


class ProcessControlTests(unittest.TestCase):
    def setUp(self):
        self.rows = [row(51, 'Nebula'), row(52, 'Lark'), row(53, 'Quartz'), row(54, 'Other')]
        self.live = self.rows[:]
        self.context = {'capabilities': ['list_running_processes', 'terminate_processes'],
                        'processNames': ['Nebula', 'Lark', 'Quartz']}
        self.cancelled = threading.Event()
        self.kernel = Mock()
        self.kernel.OpenProcess.side_effect = lambda _rights, _inherit, pid: pid
        self.stopped = set()
        self.kernel.WaitForSingleObject.side_effect = lambda handle, timeout: 0 if handle in self.stopped else 258
        def stop(handle, _code):
            self.stopped.add(handle)
            self.live = [row for row in self.live if row['pid'] != handle]
            return True
        self.kernel.TerminateProcess.side_effect = stop
        self.patch('KERNEL', self.kernel)
        self.patch('enumerate_processes', side_effect=lambda: self.live[:])
        self.patch('session_id', return_value=1)
        self.patch('process_sessions', return_value={})
        self.patch('identity', side_effect=lambda handle: {
            'created': handle * 100, 'path': next(row['path'] for row in self.rows if row['pid'] == handle),
            'critical': False})

    def patch(self, name, *args, **kwargs):
        active = patch.object(pc, name, *args, **kwargs)
        self.addCleanup(active.stop)
        return active.start()

    def targets(self):
        return [item['id'] for item in pc.listing(self.context)['processes'] if item['id']]

    def test_three_arbitrary_apps_exit_without_touching_unrequested_process(self):
        result = pc.terminate(self.context, self.targets(), self.cancelled)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['verified'])
        self.assertEqual(self.stopped, {51, 52, 53})
        self.assertEqual([row['pid'] for row in self.live], [54])
        self.assertEqual([row['state'] for row in result['results']], ['terminated'] * 3)

    def test_app_references_expand_to_all_helpers_without_copying_each_process_id(self):
        helpers = [{**row(pid, 'Worker'), 'parentPid': 51} for pid in range(60, 95)]
        self.rows.extend(helpers)
        self.live.extend(helpers)
        found = pc.listing(self.context)
        result = pc.terminate(self.context, [item['id'] for item in found['queries']], self.cancelled)
        self.assertTrue(result['verified'], result)
        self.assertEqual(self.stopped, {51, 52, 53, *range(60, 95)})
        self.assertEqual([item['pid'] for item in self.live], [54])

    def test_group_and_member_overlap_does_not_terminate_twice(self):
        found = pc.listing(self.context)
        targets = [item['id'] for item in found['queries']] + [found['processes'][0]['id']]
        self.assertTrue(pc.terminate(self.context, targets, self.cancelled)['ok'])
        self.assertEqual(self.kernel.TerminateProcess.call_count, 3)

    def test_old_app_reference_expires_when_inventory_is_refreshed(self):
        old = pc.listing(self.context)['queries'][0]['id']
        pc.listing(self.context)
        with self.assertRaises(ValueError):
            pc.terminate(self.context, [old], self.cancelled)
        self.kernel.TerminateProcess.assert_not_called()

    def test_unlisted_target_and_other_requests_reference_cannot_execute(self):
        targets = self.targets()
        for values in ([targets[0], '54'], ['process:invented']):
            with self.assertRaises(ValueError):
                pc.terminate(self.context, values, self.cancelled)
        other = {**self.context, '_processReferences': {}}
        with self.assertRaises(ValueError):
            pc.terminate(other, targets, self.cancelled)
        self.kernel.TerminateProcess.assert_not_called()

    def test_reused_pid_is_never_killed(self):
        targets = self.targets()
        self.patch('identity', return_value={'created': 999, 'path': self.rows[0]['path'], 'critical': False})
        result = pc.terminate(self.context, targets, self.cancelled)
        self.assertFalse(result['ok'])
        self.assertIn('reused', result['results'][0]['error'])
        self.kernel.TerminateProcess.assert_not_called()

    def test_failure_does_not_prevent_other_requested_processes_from_stopping(self):
        targets = self.targets()
        self.kernel.OpenProcess.side_effect = lambda _rights, _inherit, pid: None if pid == 52 else pid
        result = pc.terminate(self.context, targets, self.cancelled)
        self.assertFalse(result['ok'])
        self.assertEqual(self.stopped, {51, 53})
        self.assertEqual(result['remaining'], [{'pid': 52, 'name': 'Lark.exe'}])
        self.assertIn('Windows could not open', result['results'][1]['error'])

    def test_accepting_termination_without_exit_is_not_success(self):
        targets = self.targets()
        self.kernel.TerminateProcess.side_effect = lambda *_args: True
        result = pc.terminate(self.context, targets, self.cancelled)
        self.assertFalse(result['verified'])
        self.assertTrue(all('not verified' in item['error'] for item in result['results']))

    def test_helper_exiting_with_its_parent_is_verified_after_an_initial_denial(self):
        targets = self.targets()
        self.kernel.OpenProcess.side_effect = lambda _rights, _inherit, pid: None if pid == 52 else pid
        stop = self.kernel.TerminateProcess.side_effect
        def stop_family(handle, code):
            if handle == 51:
                self.live = [row for row in self.live if row['pid'] != 52]
            return stop(handle, code)
        self.kernel.TerminateProcess.side_effect = stop_family
        result = pc.terminate(self.context, targets, self.cancelled)
        self.assertTrue(result['verified'], result)
        self.assertEqual(result['results'][1]['state'], 'exited')

    def test_already_exited_process_is_verified_without_reusing_its_pid(self):
        targets = self.targets()
        self.live = [self.rows[-1]]
        result = pc.terminate(self.context, targets, self.cancelled)
        self.assertTrue(result['verified'])
        self.assertTrue(all(item['state'] == 'already_exited' for item in result['results']))
        self.kernel.TerminateProcess.assert_not_called()

    def test_critical_process_and_jarvis_are_not_issued_termination_references(self):
        self.patch('protected_pids', return_value={51})
        self.patch('identity', return_value={'created': 1, 'path': self.rows[1]['path'], 'critical': True})
        result = pc.listing(self.context)
        self.assertTrue(all(item['id'] is None for item in result['processes']))

    def test_cancelled_expired_or_ungranted_actions_never_terminate(self):
        targets = self.targets()
        self.cancelled.set()
        self.assertFalse(pc.terminate(self.context, targets, self.cancelled)['ok'])
        self.context['_processSnapshotTime'] = time.monotonic() - 61
        with self.assertRaises(ValueError):
            pc.terminate(self.context, targets, threading.Event())
        with self.assertRaises(ValueError):
            pc.terminate({**self.context, 'capabilities': []}, targets, threading.Event())
        self.kernel.TerminateProcess.assert_not_called()

    def test_empty_targets_do_not_grant_access_to_every_process(self):
        with self.assertRaises(ValueError):
            pc.listing({**self.context, 'processNames': []})

    def test_named_apps_exclude_services_in_another_windows_session(self):
        self.patch('session_id', side_effect=lambda pid: 0 if pid == 52 else 1)
        self.assertEqual([item['pid'] for item in pc.listing(self.context)['processes']], [51, 53])

    def test_absent_pid_cannot_match_digits_in_an_executable_name(self):
        self.context['processNames'] = ['999']
        self.live.append(row(55, 'Nebula999'))
        self.assertEqual(pc.listing(self.context)['matchingProcesses'], 0)

    def test_service_session_is_known_even_when_its_process_handle_is_protected(self):
        self.patch('session_id', side_effect=lambda pid: None if pid == 52 else 1)
        self.patch('process_sessions', return_value={52: 0})
        self.assertEqual([item['pid'] for item in pc.listing(self.context)['processes']], [51, 53])

    def test_app_tree_includes_helpers_but_not_similarly_named_service(self):
        helper = {**row(55, 'ChromiumWorker'), 'parentPid': 51}
        service = row(56, 'NebulaService')
        self.rows.extend([helper, service])
        self.live.extend([helper, service])
        result = pc.terminate(self.context, self.targets(), self.cancelled)
        self.assertTrue(result['verified'], result)
        self.assertEqual(self.stopped, {51, 52, 53, 55})
        self.assertEqual([row['pid'] for row in self.live], [54, 56])

    def test_orphaned_helper_prevents_false_completion_after_parent_exits(self):
        helper = {**row(55, 'ChromiumWorker'), 'parentPid': 51}
        self.rows.append(helper)
        self.live.append(helper)
        targets = self.targets()
        self.kernel.OpenProcess.side_effect = lambda _rights, _inherit, pid: None if pid == 55 else pid
        result = pc.terminate(self.context, targets, self.cancelled)
        self.assertFalse(result['verified'])
        self.assertEqual(result['remaining'], [{'pid':55, 'name':'ChromiumWorker.exe'}])


class ProcessRoutingTests(unittest.TestCase):
    def test_rejected_tool_arguments_retain_the_actual_failure(self):
        controller = Controller(queue.Queue())
        self.addCleanup(controller.tasks.close)
        self.addCleanup(controller.research.close)
        self.addCleanup(controller.browser.close)
        controller.request = {'id': 'test', 'capabilities': ['terminate_processes'], 'executed': []}
        with patch.object(controller, 'execute_tool', side_effect=ValueError('Choose current app or process IDs.')):
            result = controller.tool('test', 'terminate_processes', {'targets': ['wrong']})
        self.assertFalse(result['ok'])
        self.assertEqual(controller.request['toolFailures'], [
            {'tool': 'terminate_processes', 'error': 'Choose current app or process IDs.'}])

    def test_classifier_requires_concrete_force_quit_targets(self):
        value = decision('list_running_processes', 'terminate_processes')
        import json
        response = {'output': [{'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}]}
        with patch('desktop.intent.local_request', return_value=response):
            result = classify_request('Force quit those', 'test')
        self.assertTrue(result['needs_clarification'])
        self.assertEqual(result['capabilities'], [])

    def test_controller_uses_interpreted_targets_and_rejects_unverified_success(self):
        controller = Controller(queue.Queue())
        controller.tasks.close()
        controller.tasks = None
        self.addCleanup(controller.research.close)
        self.addCleanup(controller.browser.close)
        selected = decision('list_running_processes', 'terminate_processes', process_names=['Nebula', 'Lark', 'Quartz'])
        def answer(*_args):
            self.assertEqual(controller.request['processNames'], selected['process_names'])
            return {'taskStatus': 'completed', 'output':[{'type':'message','content':[{'type':'output_text','text':'All stopped.'}]}]}
        with patch('desktop.controller.classify_request', return_value=selected), patch.object(controller, 'ask_model', side_effect=answer):
            result = controller.respond('Force quit these three apps', speak=False)
        self.assertFalse(result['ok'])
        self.assertEqual(result['outcome'], 'failed')
        self.assertIn('not verified', result['text'])


if __name__ == '__main__':
    unittest.main()
