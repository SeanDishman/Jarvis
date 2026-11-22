import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from desktop import self_settings
from desktop.breaks import BreakState
from desktop.orb import Orb
from desktop.preferences import Preferences


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, name, data=None):
        self.sent.append((name, data))

    def reply(self, *_args, **_kwargs):
        pass

    def named(self, name):
        return [data for kind, data in self.sent if kind == name]


def owner(prefs, **overrides):
    values = {'preferences': prefs, 'muted': False, 'microphone_ready': True, 'microphone_name': 'Desk microphone',
              'app_settings': {'microphoneDevice': None, 'monitor': None, 'whisperModel': 'whisper-large-v3', 'showTaskPopups': True},
              'focus_guard': types.SimpleNamespace(attempts=types.SimpleNamespace(read=lambda: 3))}
    values.update(overrides)
    return types.SimpleNamespace(**values)


class TemporaryPreferences(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.prefs = Preferences(self.folder / 'assistant-settings.json')
        for patcher in (patch('desktop.self_settings.startup_enabled', return_value=True),
                        patch('desktop.self_settings.offline_speech.ready', return_value=True),
                        patch('desktop.self_settings.email_policy.enabled', return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)


class DescriptionTests(TemporaryPreferences):
    def text(self, **overrides):
        return self_settings.describe(self_settings.snapshot(owner(self.prefs, **overrides)))

    def test_lock_in_mode_says_what_gets_closed_and_follows_the_live_setting(self):
        text = self.text()
        self.assertIn('Lock in mode is on', text)
        self.assertIn('X (x.com, twitter.com) or Instagram (instagram.com)', text)
        self.assertIn('you close that tab yourself', text)
        self.assertIn('Blocked attempts so far: 3', text)
        self.assertIn('Settings > Lock in', text)
        self.prefs.save({'lockInMode': False})
        text = self.text()
        self.assertIn('Lock in mode is off', text)
        self.assertNotIn('you close that tab', text)

    def test_microphone_speech_and_interface_state_are_described(self):
        text = self.text()
        self.assertIn('Microphone: on, listening on Desk microphone', text)
        self.assertIn('the Windows default microphone', text)
        self.assertIn('Speech recognition: automatic', text)
        self.assertIn('Start when the owner signs in to Windows: on', text)
        self.assertIn('Orb screen: automatic', text)
        self.assertIn('Microphone: muted', self.text(muted=True))
        self.assertIn('Microphone: not available right now', self.text(microphone_ready=False))
        screen = self.text(app_settings={'microphoneDevice': 3, 'monitor': '\\\\.\\DISPLAY2', 'whisperModel': 'whisper-large-v3-turbo'})
        self.assertIn('Orb screen: DISPLAY2', screen)
        self.assertIn('a microphone chosen in settings', screen)
        self.assertIn('Groq whisper-large-v3-turbo', screen)
        self.prefs.save({'offlineSpeech': 'offline'})
        self.assertIn('Speech recognition: offline. Speech stays on this PC', self.text())

    def test_alerts_briefing_folders_and_email_are_described_by_name(self):
        reading = self.folder / 'Reading'
        reading.mkdir()
        self.prefs.save({'briefingTime': '07:30', 'weatherUnits': 'celsius', 'fileRoots': [str(reading)], 'monitorEnabled': False})
        text = self.text()
        self.assertIn('Daily briefing: at 07:30. Weather units: celsius', text)
        self.assertIn(f'Folders you may search and read: Reading ({reading})', text)
        self.assertIn('Proactive alerts: paused', text)
        self.assertIn('Email: off', text)
        self.assertLess(len(text), 4000, 'The settings must stay a small part of every prompt')


class ChangeTests(TemporaryPreferences):
    def test_switches_accept_booleans_and_spoken_words(self):
        result = self_settings.change(self.prefs, 'lock_in_mode', False)
        self.assertEqual({key: result[key] for key in ('ok', 'value', 'previous', 'changed')},
                         {'ok': True, 'value': False, 'previous': True, 'changed': True})
        self.assertIs(self.prefs.read()['lockInMode'], False)
        self.assertIs(self_settings.change(self.prefs, 'lock_in_mode', 'On')['value'], True)
        self.assertIs(self_settings.change(self.prefs, 'battery_alerts', 'off')['value'], False)
        self.assertIs(self.prefs.read()['monitorBattery'], False)
        with self.assertRaisesRegex(ValueError, 'on or off'):
            self_settings.change(self.prefs, 'lock_in_mode', 'maybe')

    def test_briefing_units_and_speech_values_are_normalised_or_refused(self):
        self.assertEqual(self_settings.change(self.prefs, 'daily_briefing', '7:05')['value'], '07:05')
        self.assertEqual(self.prefs.read()['briefingTime'], '07:05')
        self.assertEqual(self_settings.change(self.prefs, 'daily_briefing', 'off')['value'], 'off')
        self.assertEqual(self.prefs.read()['briefingTime'], '')
        with self.assertRaisesRegex(ValueError, 'HH:MM'):
            self_settings.change(self.prefs, 'daily_briefing', '25:00')
        self.assertEqual(self_settings.change(self.prefs, 'weather_units', 'Celsius')['value'], 'celsius')
        with self.assertRaisesRegex(ValueError, 'fahrenheit or celsius'):
            self_settings.change(self.prefs, 'weather_units', 'kelvin')
        result = self_settings.change(self.prefs, 'speech_recognition', 'offline')
        self.assertEqual((result['value'], result['previous']), ('offline', 'automatic'))
        self.assertEqual(self.prefs.read()['offlineSpeech'], 'offline')

    def test_only_listed_settings_can_change(self):
        for setting in ('memoryVault', 'fileRoots', 'startup', 'muted', None):
            with self.assertRaisesRegex(ValueError, 'cannot be changed by voice'):
                self_settings.change(self.prefs, setting, True)
        self.assertEqual(self.prefs.read(), Preferences(self.folder / 'assistant-settings.json').read())


class HostTests(TemporaryPreferences):
    def setUp(self):
        super().setUp()
        patches = [patch('desktop.controller.BreakState', return_value=BreakState(self.folder / 'break.json')), patch.object(Orb, 'save_settings'),
                   patch('desktop.orb.diagnostic'), patch('desktop.controller.diagnostic'), patch('desktop.listener.diagnostic'),
                   patch('desktop.focus_guard.diagnostic')]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.orb = Orb(interface=False)
        self.addCleanup(self.orb.controller.research.close)
        self.addCleanup(self.orb.controller.tasks.close)
        self.addCleanup(self.orb.controller.browser.close)
        self.controller = self.orb.controller
        self.controller.preferences = self.prefs
        self.controller.microphone_ready, self.controller.microphone_name = True, 'Desk microphone'
        self.ui = self.orb.ui = Recorder()

    def start_request(self, capabilities):
        self.controller.request = {'id': 'a' * 32, 'capabilities': capabilities, 'executed': [], 'session': 'desktop-test-settings',
                                   'domain': 'settings', 'ownerText': 'turn off lock in mode'}
        return self.controller.request['id']

    def test_every_desktop_model_request_carries_the_live_settings(self):
        self.start_request([])
        sent = []
        with patch('desktop.controller.local_request', side_effect=lambda port, path, payload, timeout: sent.append(payload) or {'model': 'openai/test'}):
            self.controller.ask_model('openclaw/desktop', 'Why do you keep closing X?', {'model_tier': 'default'})
            self.prefs.save({'lockInMode': False})
            self.controller.ask_model('openclaw/desktop', 'And now?', {'model_tier': 'default'})
            self.controller.ask_model('openclaw/processes', '{}', {'model_tier': 'default'})
        self.assertIn('Lock in mode is on', sent[0]['jarvisSettings'])
        self.assertIn('Microphone: on, listening on Desk microphone', sent[0]['jarvisSettings'])
        self.assertIn('Lock in mode is off', sent[1]['jarvisSettings'], 'The settings are read again for every request')
        self.assertNotIn('jarvisSettings', sent[2], 'Only the conversation model needs them')

    def test_a_broken_setting_never_blocks_a_reply(self):
        self.start_request([])
        sent = []
        with patch('desktop.controller.self_settings.snapshot', side_effect=OSError('disk')), \
             patch('desktop.controller.local_request', side_effect=lambda port, path, payload, timeout: sent.append(payload) or {}):
            self.controller.ask_model('openclaw/desktop', 'Hello', {'model_tier': 'default'})
        self.assertEqual(sent[0]['jarvisSettings'], '')

    def test_the_change_tool_needs_the_owner_grant_saves_and_updates_open_windows(self):
        request = self.start_request([])
        refused = self.controller.tool(request, 'change_jarvis_setting', {'setting': 'lock_in_mode', 'value': False})
        self.assertEqual(refused, {'ok': False, 'error': 'This tool was not requested by the owner'})
        self.assertIs(self.prefs.read()['lockInMode'], True)
        request = self.start_request(['change_jarvis_setting'])
        result = self.controller.tool(request, 'change_jarvis_setting', {'setting': 'lock_in_mode', 'value': False})
        self.assertEqual((result['ok'], result['value'], result['previous']), (True, False, True))
        self.assertIs(self.prefs.read()['lockInMode'], False)
        self.orb.step()
        self.assertIs(self.ui.named('preferences')[-1]['lockInMode'], False)
        failed = self.controller.tool(request, 'change_jarvis_setting', {'setting': 'memoryVault', 'value': 'C:\\'})
        self.assertFalse(failed['ok'])
        self.assertIn('cannot be changed by voice', failed['error'])

    def test_a_new_briefing_time_is_checked_right_away(self):
        request = self.start_request(['change_jarvis_setting'])
        self.controller.next_briefing_check = 10 ** 9
        self.controller.tool(request, 'change_jarvis_setting', {'setting': 'daily_briefing', 'value': '08:15'})
        self.assertEqual(self.controller.next_briefing_check, 0)

    def test_background_task_workers_describe_and_change_the_main_settings(self):
        self.controller.muted = True
        job = {'id': 'task-settings', '_cancel': threading.Event(), '_foreground': 0, '_decision': None,
               '_previousDomain': None, '_previousAnswer': ''}
        worker = self.controller.make_task_agent(self.orb.events, job)
        self.assertIn('Microphone: muted', worker.settings_context(), 'Workers must see the real microphone state')
        worker.change_setting('weather_units', 'celsius')
        self.assertEqual(self.prefs.read()['weatherUnits'], 'celsius')
        self.assertEqual(self.controller.next_briefing_check, 0)
        self.orb.step()
        self.assertEqual(self.ui.named('preferences')[-1]['weatherUnits'], 'celsius')

    def test_a_lock_in_pass_needs_the_owner_grant_and_reaches_the_running_guard(self):
        guard = self.controller.focus_guard
        request = self.start_request([])
        refused = self.controller.tool(request, 'allow_lock_in_visit', {'site': 'instagram'})
        self.assertEqual(refused, {'ok': False, 'error': 'This tool was not requested by the owner'})
        self.assertIsNone(guard.pass_status())
        request = self.start_request(['allow_lock_in_visit'])
        result = self.controller.tool(request, 'allow_lock_in_visit', {'site': 'instagram'})
        self.assertEqual((result['ok'], result['granted'], result['site'], result['visits']), (True, True, 'Instagram', 1))
        self.assertIn('3 minutes', result['note'])
        self.assertIn('10 minutes', result['note'])
        self.assertEqual(guard.pass_status()['state'], 'waiting')
        self.assertIn('The owner has a one-visit lock in pass for Instagram', self.controller.settings_context())
        bad = self.controller.tool(request, 'allow_lock_in_visit', {'site': 'youtube'})
        self.assertFalse(bad['ok'])
        self.assertEqual(guard.pass_status()['site'], 'instagram', 'A refused request leaves the granted pass alone')

    def test_no_pass_is_granted_while_lock_in_mode_is_off(self):
        self.prefs.save({'lockInMode': False})
        request = self.start_request(['allow_lock_in_visit'])
        result = self.controller.tool(request, 'allow_lock_in_visit', {'site': 'x'})
        self.assertEqual((result['ok'], result['granted']), (True, False))
        self.assertIn('No pass is needed', result['note'])
        self.assertIsNone(self.controller.focus_guard.pass_status())

    def test_background_task_workers_grant_the_pass_on_the_running_guard(self):
        job = {'id': 'task-pass', '_cancel': threading.Event(), '_foreground': 0, '_decision': None,
               '_previousDomain': None, '_previousAnswer': ''}
        worker = self.controller.make_task_agent(self.orb.events, job)
        self.assertIsNot(worker.focus_guard, self.controller.focus_guard)
        result = worker.allow_lock_in_visit('any')
        self.assertEqual(result['site'], 'X or Instagram')
        self.assertEqual(self.controller.focus_guard.pass_status()['site'], None)
        self.assertIn('one-visit lock in pass for X or Instagram', worker.settings_context())

    def test_the_host_shares_its_own_settings_file_with_the_description(self):
        self.orb.settings['microphoneDevice'] = 4
        self.assertIs(self.controller.app_settings, self.orb.settings)
        self.assertIn('a microphone chosen in settings', self.controller.settings_context())


if __name__ == '__main__':
    unittest.main()
