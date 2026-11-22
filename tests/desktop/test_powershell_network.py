from tests.desktop.intent_fixtures import decision
import json
import queue
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from desktop.controller import Controller, capabilities_for
from desktop.network import inspect_network, public_address
from desktop.powershell import execute
from desktop.research import ResearchJobs


class PowerShellTests(unittest.TestCase):
    def test_real_powershell_literal_pipeline_and_multiple_commands(self):
        result = execute('Get-Date -Format yyyy; Get-Service | Where-Object -Property Status -EQ Running | Select-Object -Property Name,Status -First 2')
        self.assertTrue(result['ok'], result)
        self.assertIn('Running', result['output'])
        self.assertTrue(result['readOnly'])
        self.assertFalse(result['truncated'])

    def test_no_script_execution_remote_access_email_or_writes(self):
        commands = [
            'Get-Date; Write-Output forbidden',
            'Get-Date | Out-File .cache/should-not-exist.txt',
            'Get-Date > .cache/should-not-exist.txt',
            'Get-Process -ComputerName example.com',
            'Get-CimInstance -ClassName Win32_Process',
            'Get-CimInstance -ClassName Win32_OperatingSystem -CimSession remote',
            'Get-Date; Send-MailMessage',
            'Get-Process | ForEach-Object { $_.Kill() }',
            'Get-Date -Format $(Write-Output forbidden)',
            'Get-Date | Select-Object @{Name="bad"; Expression={Write-Output forbidden}}',
            '& "Get-Date"',
            '$x = Get-Date',
            'Get-Content .cache/not-a-real-file',
        ]
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(execute, commands))
        for command, result in zip(commands, results):
            with self.subTest(command=command):
                self.assertFalse(result['ok'], result)
                self.assertNotIn('output', result)

    def test_timeout_kills_the_host_and_precancel_does_not_launch(self):
        result = execute('Get-Date', timeout=.01)
        self.assertTrue(result.get('timedOut'), result)
        cancel = threading.Event()
        cancel.set()
        with patch('desktop.powershell.subprocess.Popen') as launch:
            result = execute('Get-Date', cancel)
        self.assertTrue(result['cancelled'])
        launch.assert_not_called()

    def test_background_status_chat_and_cancellation(self):
        controller = Controller(queue.Queue())
        started = threading.Event()
        def delayed_command(_command, cancel):
            started.set()
            cancel.wait(5)
            return {'ok':False, 'cancelled':True, 'error':'Cancelled'}
        with patch('desktop.powershell.execute', side_effect=delayed_command), \
             patch('desktop.controller.classify_request', side_effect=lambda text,*_: decision(action='task_status') if text == 'How is PowerShell going?' else decision(action='task_cancel',task_id=controller.research.list()[0]['id']) if text == 'Cancel the PowerShell command' else decision()), \
             patch.object(controller, 'ask_model', return_value={'output':[{'type':'message','content':[{'type':'output_text','text':'Four, sir.'}]}]}):
            try:
                result = controller.research.start('Use PowerShell to check services', 'powershell', powershell_command='Get-Service')
                self.assertTrue(result['ok'])
                self.assertTrue(started.wait(2))
                controller.last_domain = 'powershell'
                controller.last_command_time = time.monotonic()
                progress = controller.respond('How is PowerShell going?', speak=False)
                self.assertIn('Running PowerShell', progress['text'])
                self.assertEqual(controller.respond('What is two plus two?', speak=False)['text'], 'Four, sir.')
                stopped = controller.respond('Cancel the PowerShell command', speak=False)
                self.assertIn('cancelled', stopped['text'])
                self.assertEqual(controller.research.status(result['id'])['status'], 'cancelled')
            finally:
                controller.research.close()

    def test_finished_job_contains_actual_output(self):
        jobs = ResearchJobs(queue.Queue(), Mock())
        with patch('desktop.powershell.execute', return_value={'ok':True,'output':'Windows test output','truncated':False}):
            try:
                result = jobs.start('Run Get-Date', 'powershell', powershell_command='Get-Date')
                for _ in range(100):
                    result = jobs.status(result['id'])
                    if result['status'] in ('completed','failed'):
                        break
                    time.sleep(.01)
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual(result['output'], 'Windows test output')
                self.assertEqual(result['command'], 'Get-Date')
                self.assertNotIn('_powershellCommand', result)
            finally:
                jobs.close()

    def test_network_tools_do_not_implicitly_grant_shell_or_email(self):
        self.assertEqual(capabilities_for(decision('get_network_info'))[0], ['get_network_info'])
        self.assertEqual(capabilities_for(decision('start_powershell'))[2], 'powershell')
        self.assertEqual(capabilities_for(decision('read_email'))[2], 'email')

    def test_shell_output_followup_does_not_capture_a_terminal(self):
        controller = Controller(queue.Queue())
        with patch('desktop.controller.classify_request', return_value=decision(action='task_status')), \
             patch('desktop.controller.capture_screen') as capture, \
             patch.object(controller, 'activity_status', return_value={'ok':True,'scope':'powershell','status':'completed','summary':'The year is 2026, sir.'}):
            result = controller.respond('Read the PowerShell output', speak=False)
        self.assertTrue(result['ok'], result)
        self.assertIn('2026', result['text'])
        capture.assert_not_called()


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.network = {'adapters':[{'name':'Wi-Fi','addresses':[{'address':'192.168.1.23'}]}], 'vpnDetected':True}

    def test_public_and_raw_are_never_confused(self):
        with patch('desktop.network.adapter_snapshot', return_value=self.network), \
             patch('desktop.network.public_address', side_effect=lambda family:'203.0.113.7' if family == 4 else '2001:db8::1'):
            result = inspect_network('isp')
        self.assertTrue(result['ok'])
        self.assertEqual(result['publicIPv4'], '203.0.113.7')
        self.assertIsNone(result['ispPublicIP'])
        self.assertFalse(result['vpnExitConfirmed'])
        self.assertIn('not independently verified', result['ispMeaning'])

    def test_local_does_not_contact_public_lookup(self):
        with patch('desktop.network.adapter_snapshot', return_value=self.network), \
             patch('desktop.network.public_address') as lookup:
            result = inspect_network('local')
        self.assertTrue(result['ok'])
        lookup.assert_not_called()

    def test_failed_lookup_and_unknown_vpn_remain_unknown(self):
        with patch('desktop.network.adapter_snapshot', side_effect=RuntimeError), \
             patch('desktop.network.basic_adapter_snapshot', side_effect=RuntimeError), \
             patch('desktop.network.public_address', side_effect=RuntimeError):
            result = inspect_network('public')
        self.assertFalse(result['ok'])
        self.assertIsNone(result['vpnDetected'])
        self.assertIsNone(result['publicIPv4'])
        self.assertIsNone(result['ispPublicIP'])

    def test_native_adapter_fallback_retains_local_addresses(self):
        with patch('desktop.network.adapter_snapshot', side_effect=RuntimeError), \
             patch('desktop.network.basic_adapter_snapshot', return_value=self.network):
            result = inspect_network('local')
        self.assertTrue(result['ok'])
        self.assertEqual(result['adapters'], self.network['adapters'])
        self.assertTrue(result['warnings'])

    def test_service_cannot_report_private_or_wrong_family_as_public(self):
        for address, family in [('192.168.1.1',4),('127.0.0.1',4),('8.8.8.8',6)]:
            stream = Mock()
            stream.__enter__ = Mock(return_value=stream)
            stream.__exit__ = Mock()
            stream.read.return_value = json.dumps({'ip':address}).encode()
            with patch('desktop.network.urllib.request.urlopen', return_value=stream):
                with self.assertRaises(ValueError):
                    public_address(family)


if __name__ == '__main__':
    unittest.main()
