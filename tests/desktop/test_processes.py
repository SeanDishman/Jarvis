import ctypes
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from desktop.breaks import BreakState
from desktop.common import ROOT
from desktop.controller import Controller
from desktop.intent import classify_request
from desktop.processes import (enumerate_processes, summarize_processes, window_inventory,
                               system_memory, process_memory, inspect_processes, MemoryStatus, ProcessMemoryCounters)


ROWS = [{'pid': 101, 'name': 'msedge.exe', 'path': r'C:\Program Files\Microsoft\Edge\msedge.exe'},
        {'pid': 102, 'name': 'msedge.exe', 'path': r'C:\Program Files\Microsoft\Edge\msedge.exe'},
        {'pid': 201, 'name': 'msedgewebview2.exe', 'path': None},
        {'pid': 301, 'name': 'System', 'path': None}]
WINDOWS = {'available': True, 'byPid': {101: {'open': 1, 'minimized': 1}}}


def reply(text):
    return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


class InventoryTests(unittest.TestCase):
    def test_edge_window_and_minimized_state_exclude_webview(self):
        data = summarize_processes(ROWS, WINDOWS, ['Microsoft Edge'])
        self.assertEqual(data['matchingProcesses'], 2)
        self.assertEqual(len(data['groups']), 1)
        self.assertEqual(data['groups'][0]['name'], 'msedge.exe')
        self.assertEqual(data['groups'][0]['openWindows'], 1)
        self.assertEqual(data['groups'][0]['minimizedWindows'], 1)

    def test_background_only_and_absent_browser_are_different(self):
        background = summarize_processes(ROWS, {'available': True, 'byPid': {}}, ['Edge'])
        self.assertEqual(background['matchingProcesses'], 2)
        self.assertEqual(background['groups'][0]['openWindows'], 0)
        absent = summarize_processes(ROWS[2:], WINDOWS, ['Edge'])
        self.assertEqual(absent['queries'][0]['processCount'], 0)
        self.assertEqual(absent['groups'], [])

    def test_inaccessible_desktop_never_reports_zero_windows(self):
        with patch('desktop.processes.desktop_name', return_value='CodexSandboxDesktop'), \
             patch('desktop.processes.USER') as user:
            windows = window_inventory()
        user.EnumWindows.assert_not_called()
        data = summarize_processes(ROWS, windows, ['Edge'])
        self.assertEqual(data['matchingProcesses'], 2)
        self.assertIsNone(data['groups'][0]['openWindows'])
        self.assertFalse(data['windowsAvailable'])

    def test_protected_process_is_retained_without_a_path(self):
        data = summarize_processes(ROWS, WINDOWS, ['301'])
        self.assertEqual(data['groups'][0]['name'], 'System')
        self.assertEqual(data['groups'][0]['pathsUnavailable'], 1)
        self.assertEqual(data['groups'][0]['paths'], [])

    def test_large_inventory_is_bounded_and_specific_queries_search_every_row(self):
        rows = [{'pid': i, 'name': f'app{i:04d}.exe', 'path': 'C:/' + 'a' * 800} for i in range(600)]
        data = summarize_processes(rows, WINDOWS)
        self.assertGreater(data['omittedGroups'], 0)
        self.assertEqual(data['matchingProcesses'], 600)
        self.assertLessEqual(len(json.dumps(data, ensure_ascii=False)), 35000)
        target = summarize_processes(rows, WINDOWS, ['app0599.exe'])
        self.assertEqual(target['queries'][0]['processCount'], 1)
        self.assertEqual(target['groups'][0]['pids'], [599])

    def test_invalid_query_does_not_become_a_shell_command(self):
        for names in ('Edge', [''], [None], ['x' * 121], ['a'] * 9):
            with self.subTest(names=names), self.assertRaises(RuntimeError):
                summarize_processes(ROWS, WINDOWS, names)
        self.assertEqual(summarize_processes(ROWS, WINDOWS, ['$(whoami)'])['matchingProcesses'], 0)

    def test_snapshot_failure_is_reported_as_unknown(self):
        with patch('desktop.processes.KERNEL') as kernel:
            kernel.CreateToolhelp32Snapshot.return_value = ctypes.c_void_p(-1).value
            with self.assertRaisesRegex(RuntimeError, 'could not provide'):
                enumerate_processes()
            kernel.CloseHandle.assert_not_called()

    def test_snapshot_handle_is_closed_on_read_failure(self):
        with patch('desktop.processes.KERNEL') as kernel:
            kernel.CreateToolhelp32Snapshot.return_value = 123
            kernel.Process32FirstW.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'could not read'):
                enumerate_processes()
            kernel.CloseHandle.assert_called_once_with(123)


class MemoryUsageTests(unittest.TestCase):
    def test_system_ram_comes_from_physical_counters_with_correct_units(self):
        def fill(pointer):
            status = ctypes.cast(pointer, ctypes.POINTER(MemoryStatus)).contents
            self.assertEqual(status.dwLength, ctypes.sizeof(MemoryStatus))
            status.ullTotalPhys = 16 * 1024 ** 3
            status.ullAvailPhys = 6 * 1024 ** 3
            status.ullTotalPageFile = 99 * 1024 ** 3
            return True
        with patch('desktop.processes.KERNEL') as kernel:
            kernel.GlobalMemoryStatusEx.side_effect = fill
            memory = system_memory()
        self.assertEqual(memory['totalGiB'], 16)
        self.assertEqual(memory['usedGiB'], 10)
        self.assertEqual(memory['availableGiB'], 6)
        self.assertEqual(memory['usedPercent'], 62.5)
        self.assertEqual(memory['usedBytes'], memory['totalBytes'] - memory['availableBytes'])

    def test_unavailable_system_ram_has_no_invented_zero(self):
        with patch('desktop.processes.KERNEL') as kernel:
            kernel.GlobalMemoryStatusEx.return_value = False
            memory = system_memory()
        self.assertFalse(memory['available'])
        self.assertNotIn('usedBytes', memory)

    def test_process_ram_uses_working_set_not_committed_bytes_and_closes_handle(self):
        def fill(_handle, pointer, _size):
            counters = ctypes.cast(pointer, ctypes.POINTER(ProcessMemoryCounters)).contents
            counters.WorkingSetSize = 200 * 1024 ** 2
            counters.PrivateUsage = 900 * 1024 ** 2
            return True
        with patch('desktop.processes.KERNEL') as kernel:
            kernel.OpenProcess.return_value = 123
            kernel.K32GetProcessMemoryInfo.side_effect = fill
            memory = process_memory(456)
            kernel.OpenProcess.assert_called_once_with(0x1000, False, 456)
            kernel.CloseHandle.assert_called_once_with(123)
        self.assertEqual(memory['workingSetBytes'], 200 * 1024 ** 2)

    def test_failed_process_measurement_is_unknown_and_closes_handle(self):
        with patch('desktop.processes.KERNEL') as kernel:
            kernel.OpenProcess.return_value = 123
            kernel.K32GetProcessMemoryInfo.return_value = False
            self.assertIsNone(process_memory(456))
            kernel.CloseHandle.assert_called_once_with(123)
            kernel.reset_mock()
            kernel.OpenProcess.return_value = None
            self.assertIsNone(process_memory(456))
            kernel.K32GetProcessMemoryInfo.assert_not_called()
            kernel.CloseHandle.assert_not_called()

    def test_app_ram_sums_all_matching_processes_and_excludes_webview(self):
        rows = [dict(row, memory={'workingSetBytes': value * 1024 ** 2})
                for row, value in zip(ROWS, [100, 300, 900, 20])]
        data = summarize_processes(rows, WINDOWS, ['Edge'])
        self.assertEqual(data['groups'][0]['memory']['workingSetMiB'], 400)
        self.assertEqual(data['queries'][0]['memory']['workingSetMiB'], 400)
        self.assertTrue(data['groups'][0]['memory']['complete'])

    def test_partial_app_ram_retains_missing_count_and_lower_bound(self):
        rows = [dict(ROWS[0], memory={'workingSetBytes': 100 * 1024 ** 2}), ROWS[1]]
        data = summarize_processes(rows, WINDOWS, ['Edge'])
        memory = data['groups'][0]['memory']
        self.assertEqual(memory['workingSetMiB'], 100)
        self.assertEqual(memory['unavailableProcesses'], 1)
        self.assertFalse(memory['complete'])
        unknown = summarize_processes([ROWS[1]], WINDOWS, ['Edge'])['groups'][0]['memory']
        self.assertIsNone(unknown['workingSetMiB'])

    def test_largest_memory_consumer_is_retained_before_truncation(self):
        rows = [{'pid': i, 'name': f'app{i:04d}.exe', 'memory': {'workingSetBytes': 1024}} for i in range(150)]
        rows.append({'pid': 999, 'name': 'largest.exe', 'memory': {'workingSetBytes': 2 * 1024 ** 3}})
        data = summarize_processes(rows, WINDOWS)
        self.assertEqual(data['groups'][0]['name'], 'largest.exe')
        self.assertGreater(data['omittedGroups'], 0)
        self.assertEqual(data['processMemoryCoverage']['measuredProcesses'], 151)

    def test_system_ram_remains_usable_when_process_enumeration_fails(self):
        physical = {'available': True, 'totalGiB': 16, 'usedGiB': 10, 'usedPercent': 62.5}
        with patch('desktop.processes.system_memory', return_value=physical), \
             patch('desktop.processes.enumerate_processes', side_effect=RuntimeError('Process list failed')):
            data = inspect_processes(['Edge'])
        self.assertEqual(data['systemMemory'], physical)
        self.assertFalse(data['processInventoryAvailable'])
        self.assertNotIn('matchingProcesses', data)

    def test_process_ram_survives_unavailable_global_counter_and_desktop(self):
        rows = [dict(ROWS[0], memory={'workingSetBytes': 200 * 1024 ** 2})]
        with patch('desktop.processes.system_memory', return_value={'available': False}), \
             patch('desktop.processes.enumerate_processes', return_value=rows), \
             patch('desktop.processes.window_inventory', return_value={'available': False, 'byPid': {}}):
            data = inspect_processes(['Edge'])
        self.assertEqual(data['queries'][0]['memory']['workingSetMiB'], 200)
        self.assertFalse(data['systemMemory']['available'])
        self.assertFalse(data['windowsAvailable'])


class ProcessRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='process-test-')
        self.addCleanup(temporary.cleanup)
        self.controller = Controller(queue.Queue())
        self.controller.breaks = BreakState(Path(temporary.name) / 'break.json')

    def test_semantic_process_question_uses_fresh_read_only_inventory(self):
        decision = {'action': 'processes', 'process_names': ['Microsoft Edge'], 'model_tier': 'sol'}
        data = summarize_processes(ROWS, WINDOWS, decision['process_names'])
        with patch('desktop.controller.classify_request', return_value=decision), \
             patch('desktop.controller.inspect_processes', return_value=data) as inspect, \
             patch('desktop.controller.local_request', return_value=reply('Edge has an open window.')) as model, \
             patch.object(self.controller.research, 'start') as research, \
             patch.object(self.controller.apps, 'launch') as launch, \
             patch('desktop.controller.capture_screen') as screen:
            result = self.controller.respond('Yo, is Microsoft Edge open on my computer?', speak=False)
        self.assertTrue(result['ok'], result)
        inspect.assert_called_once_with(['Microsoft Edge'])
        research.assert_not_called()
        launch.assert_not_called()
        screen.assert_not_called()
        self.assertEqual(result['tools'], ['inspect_processes'])
        payload = model.call_args.args[2]
        self.assertEqual(payload['model'], 'openclaw/processes')
        self.assertEqual(payload['modelTier'], 'sol')
        self.assertNotIn('capabilities', payload)
        self.assertEqual(payload['user'], self.controller.sessions['processes'])
        self.assertNotEqual(payload['user'], self.controller.sessions['web'])
        self.assertEqual(json.loads(payload['input'])['snapshot'], data)

    def test_followup_rechecks_processes_and_supplies_prior_answer(self):
        self.controller.last_domain = 'processes'
        self.controller.last_answer_text = 'Edge is running in the background.'
        import time
        self.controller.last_command_time = time.monotonic()
        with patch('desktop.controller.classify_request', return_value={'action': 'processes', 'process_names': ['Edge']}), \
             patch('desktop.controller.inspect_processes', return_value={'queries': []}) as inspect, \
             patch('desktop.controller.local_request', return_value=reply('It is still running.')) as model:
            self.assertTrue(self.controller.respond('And is it still there now?', speak=False)['ok'])
        inspect.assert_called_once_with(['Edge'])
        self.assertEqual(json.loads(model.call_args.args[2]['input'])['priorAnswer'], 'Edge is running in the background.')

    def test_failed_inventory_cannot_produce_an_invented_answer(self):
        with patch('desktop.controller.classify_request', return_value={'action': 'processes', 'process_names': []}), \
             patch('desktop.controller.inspect_processes', side_effect=RuntimeError('Windows could not provide a process list.')), \
             patch('desktop.controller.local_request') as model:
            result = self.controller.respond('Find hidden processes on my computer', speak=False)
        self.assertFalse(result['ok'])
        self.assertIn('could not provide', result['error'])
        model.assert_not_called()

    def test_classifier_rejects_malformed_app_selection(self):
        decision = {'action': 'processes', 'process_names': 'Edge', 'model_tier': 'default', 'needs_clarification': False}
        with patch('desktop.intent.local_request', return_value=reply(json.dumps(decision))), self.assertRaises(RuntimeError):
            classify_request('Is Edge open?', 'test')


if __name__ == '__main__':
    unittest.main()
