import json
import math
from pathlib import Path
import queue
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from desktop.breaks import BreakState
from desktop.orb import Orb
from desktop.orb_visual import VoiceLevels, loudness
from desktop.ui_bridge import UIBridge, plain


class Recorder:
    def __init__(self):
        self.sent = []
        self.replies = []
        self.replied = threading.Event()

    def send(self, name, data=None):
        self.sent.append((name, json.loads(json.dumps(plain(data)))))

    def reply(self, identifier, ok, result=None, error=None):
        self.replies.append((identifier, ok, result, error))
        self.replied.set()

    def named(self, name):
        return [data for kind, data in self.sent if kind == name]


class HostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.breaks = BreakState(self.path / 'break.json')
        patches = [patch('desktop.controller.BreakState', return_value=self.breaks), patch.object(Orb, 'save_settings'),
                   patch('desktop.orb.diagnostic'), patch('desktop.controller.diagnostic'), patch('desktop.listener.diagnostic')]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.orb = Orb(interface=False)
        self.addCleanup(self.orb.controller.research.close)
        self.addCleanup(self.orb.controller.tasks.close)
        self.addCleanup(self.orb.controller.browser.close)
        self.orb.controller.windows_desktop = 'Default'
        self.orb.controller.microphone_ready = True
        self.ui = self.orb.ui = Recorder()

    def pump(self):
        self.orb.step()

    def test_conversation_keeps_questions_answers_notices_and_results_in_order(self):
        self.orb.events.put(('question', 'What time is it?'))
        self.pump()
        entry = self.orb.conversation[-1]
        self.assertTrue(entry['pending'])
        self.orb.events.put(('answer', 'It is noon, sir.', [{'title': 'Clock', 'url': 'https://time.example.com/'},
                                                             {'title': 'Private', 'url': 'http://192.168.1.4/'}]))
        self.orb.events.put(('notice', 'The reply is in the panel, but Windows could not play the voice.'))
        self.orb.events.put(('task_completed', {'question': 'Find flights', 'summary': 'Two nonstop flights.', 'status': 'completed', 'sources': []}))
        self.pump()
        kinds = [row['kind'] for row in self.orb.conversation]
        self.assertEqual(kinds, ['exchange', 'notice', 'result'])
        self.assertEqual(self.orb.conversation[0]['answer'], 'It is noon, sir.')
        self.assertFalse(self.orb.conversation[0]['pending'])
        self.assertEqual([source['url'] for source in self.orb.conversation[0]['sources']], ['https://time.example.com/'])
        self.assertEqual(self.ui.named('conversation')[-1], self.orb.conversation)

    def test_a_notice_answers_a_pending_question_and_history_is_bounded(self):
        self.orb.events.put(('question', 'Cancel that'))
        self.orb.events.put(('notice', 'Task cancelled.'))
        self.pump()
        self.assertEqual(len(self.orb.conversation), 1)
        self.assertEqual(self.orb.conversation[0]['answer'], 'Task cancelled.')
        for index in range(60):
            self.orb.events.put(('question', f'Question {index}'))
        self.pump()
        self.assertEqual(len(self.orb.conversation), 40)
        self.assertEqual(sum(row.get('pending', False) for row in self.orb.conversation), 1)

    def test_insert_hides_every_window_and_stops_listening_then_shows_and_wakes(self):
        self.orb.toggle_jarvis()
        self.assertEqual(self.ui.named('visibility')[-1], {'hidden': True, 'reason': 'toggle'})
        self.assertTrue(self.orb.listener.hidden)
        self.assertFalse(self.orb.controller.ui_visible)
        self.orb.events.put(('monitor_alert', {'id': 'a', 'text': 'New file'}))
        self.orb.events.put(('research', {'id': 'web-1', 'status': 'running'}))
        self.pump()
        self.assertEqual(self.ui.named('alert'), [], 'Hidden Jarvis must not pop alerts')
        self.assertEqual(self.ui.named('open'), [], 'Hidden Jarvis must not pop Activity')
        self.orb.toggle_jarvis()
        self.assertEqual(self.ui.named('visibility')[-1], {'hidden': False, 'reason': 'toggle'})
        self.assertFalse(self.orb.listener.hidden)
        self.assertIn('pulse', [name for name, _ in self.ui.sent])

    def test_breaks_hide_the_interface_and_drop_notifications_until_resumed(self):
        self.orb.controller.take_break({'delay_seconds': 60, 'wake_at': None})
        self.pump()
        self.assertEqual(self.ui.named('visibility')[-1], {'hidden': True, 'reason': 'break'})
        self.orb.events.put(('speech_error', 'A late notification must not reopen Jarvis'))
        self.orb.events.put(('question', 'Ignored'))
        self.pump()
        self.assertEqual(self.orb.conversation, [])
        self.assertEqual(self.ui.named('open'), [])
        self.orb.controller.resume()
        self.pump()
        self.assertEqual(self.ui.named('visibility')[-1], {'hidden': False, 'reason': 'break'})

    def test_tasks_never_open_the_console_unless_popups_were_turned_on(self):
        job = {'id': 'web-1', 'status': 'running', 'scope': 'web', 'question': 'Minimize Discord', 'created': time.time()}
        self.orb.settings.pop('showTaskPopups', None)
        with patch.object(self.orb.controller, 'activity_list', return_value=[job]):
            self.orb.events.put(('research', job))
            self.orb.events.put(('files_found', {'results': []}))
            self.pump()
        self.assertEqual(self.ui.named('open'), [], 'By default the console only opens when the owner opens it')
        self.assertTrue(self.ui.named('jobs')[-1]['busy'], 'The task still shows in the activity list')
        # Turned on in settings, a new task opens the activity view once, without focus.
        self.orb.settings['showTaskPopups'] = True
        with patch.object(self.orb.controller, 'activity_list', return_value=[job]):
            self.orb.events.put(('research', {**job, 'id': 'web-2'}))
            self.orb.events.put(('research', {**job, 'id': 'web-2', 'note': 'Reading'}))
            self.pump()
        self.assertEqual(self.ui.named('open'), [{'window': 'console', 'view': 'activity', 'focus': False, 'select': 'web-2', 'automatic': True}])

    def test_progress_follows_live_work_and_clears_when_hidden(self):
        job = {'id': 'task-1', 'status': 'running', 'scope': 'web', 'question': 'Check the time in Nagasaki', 'note': 'Running browser read'}
        with patch.object(self.orb.controller, 'activity_list', return_value=[job]):
            self.orb.refresh_progress()
            self.assertEqual(self.ui.named('progress')[-1][0]['detail'], 'Reading the page')
            self.orb.ui_hidden = True
            self.orb.sync_break_visibility()
            self.assertEqual(self.ui.named('progress')[-1], [])

    def test_interface_calls_run_on_the_loop_or_in_workers_and_report_errors(self):
        with patch.object(self.orb.controller, 'submit') as submit, patch.object(self.orb.controller, 'interrupt') as interrupt:
            self.orb.ui_call(1, 'submit', {'text': 'Open Notepad'})
            self.assertEqual(self.ui.replies, [], 'Loop calls wait for the host loop')
            self.pump()
            time.sleep(.05)
            self.assertEqual(self.ui.replies[-1][:2], (1, True))
            submit.assert_called_once_with('Open Notepad')
            self.orb.controller.busy.acquire()
            try:
                self.orb.ui_call(2, 'submit', {'text': 'Stop'})
                self.pump()
                time.sleep(.05)
            finally:
                self.orb.controller.busy.release()
            interrupt.assert_called_once_with('Stop')
        self.orb.ui_call(3, 'submit', {'text': '   '})
        self.pump()
        self.assertEqual(self.ui.replies[-1][:2], (3, False))
        self.assertIn('between 1 and 4000', self.ui.replies[-1][3])
        self.orb.ui_call(4, 'no_such_action', {})
        self.assertEqual(self.ui.replies[-1][:2], (4, False))
        self.orb.ui_call(5, 'set_setting', {'key': 'muted', 'value': True})
        self.pump()
        self.assertEqual(self.ui.replies[-1][:2], (5, False))
        self.ui.replied.clear()
        self.orb.ui_call(6, 'reminder_change', {'action': 'delete', 'reminder': 'x'})
        self.assertTrue(self.ui.replied.wait(2))
        self.assertEqual(self.ui.replies[-1][:2], (6, False))

    def test_snapshot_describes_everything_a_new_window_needs(self):
        self.orb.events.put(('question', 'Hello'))
        self.pump()
        snapshot = json.loads(json.dumps(plain(self.orb.call_snapshot())))
        for key in ('state', 'conversation', 'greeting', 'model', 'jobs', 'progress', 'hidden', 'muted', 'screens', 'settings'):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot['conversation'][0]['question'], 'Hello')
        self.assertTrue(snapshot['screens']['screens'], 'Monitor geometry is needed to place the orb')

    def test_plain_drops_private_and_live_values(self):
        self.assertEqual(plain({'id': 'a', '_frame': {'image': 'x'}, 'cancel': threading.Event(), 'rows': [1, object(), {'_x': 1}]}),
                         {'id': 'a', 'rows': [1, {}]})

    def test_voice_meter_streams_the_input_level_only_while_the_settings_page_holds_the_lease(self):
        now = time.monotonic()
        self.orb.voice.microphone, self.orb.voice.microphone_at = .5, now
        self.orb.tick(now)
        self.assertEqual(self.ui.named('mic_level'), [], 'Nothing is read until the settings page asks')
        self.orb.ui_call(1, 'voice_meter', {'on': True})
        self.assertEqual(self.ui.replies, [], 'The lease is taken on the host loop so calls keep their order')
        self.orb.step()
        self.assertEqual(self.ui.replies[-1][:2], (1, True))
        self.assertEqual(self.ui.named('mic_level'), [{'level': .5, 'status': 'live'}])
        paced = self.orb.next_meter
        self.orb.tick(paced - .01)
        self.assertEqual(len(self.ui.named('mic_level')), 1, 'The stream is paced, not sent on every loop turn')
        self.orb.voice.microphone_at = paced
        self.orb.tick(paced)
        self.assertEqual(len(self.ui.named('mic_level')), 2)
        self.orb.tick(self.orb.meter_until + .01)
        self.assertEqual(len(self.ui.named('mic_level')), 2, 'A lease the page stopped renewing ends the stream')

    def test_voice_meter_stops_when_the_page_leaves_or_the_windows_are_hidden(self):
        self.orb.ui_call(1, 'voice_meter', {'on': True})
        self.orb.step()
        self.assertGreater(self.orb.meter_until, time.monotonic())
        self.assertEqual(len(self.ui.named('mic_level')), 1)
        self.orb.ui_call(2, 'voice_meter', {'on': False})
        self.orb.step()
        self.assertEqual(self.orb.meter_until, 0.0)
        self.orb.next_meter = 0.0
        self.orb.tick(time.monotonic())
        self.assertEqual(len(self.ui.named('mic_level')), 1, 'Leaving the page stops the stream at once')
        self.orb.ui_call(3, 'voice_meter', {'on': True})
        self.orb.ui_hidden = True
        self.orb.step()
        self.orb.next_meter = 0.0
        self.orb.tick(time.monotonic())
        self.assertEqual(len(self.ui.named('mic_level')), 1, 'Hidden windows get no level stream')

    def test_voice_meter_reports_a_muted_or_unavailable_microphone_as_silence(self):
        now = time.monotonic()
        self.orb.voice.microphone, self.orb.voice.microphone_at = .9, now
        self.assertEqual(self.orb.microphone_meter(now), {'level': .9, 'status': 'live'})
        self.assertEqual(self.orb.microphone_meter(now + 1), {'level': 0.0, 'status': 'live'}, 'A stale reading reads as silence')
        self.orb.listener.muted = True
        self.assertEqual(self.orb.microphone_meter(now), {'level': 0.0, 'status': 'muted'}, 'A muted microphone never shows a level')
        self.orb.listener.muted = False
        self.orb.controller.microphone_ready = False
        self.assertEqual(self.orb.microphone_meter(now), {'level': 0.0, 'status': 'unavailable'})


class VoiceLevelsTests(unittest.TestCase):
    def test_tapped_microphone_blocks_become_a_level_that_goes_quiet_when_they_stop(self):
        levels, received = VoiceLevels(), []
        listener = types.SimpleNamespace(callback=lambda data, frames, moment, status: received.append(len(data)))
        levels.tap(listener)
        block = (np.sin(np.linspace(0, 200, 1280)) * 3000).astype('<i2').tobytes()
        listener.callback(block, 1280, None, None)
        self.assertEqual(received, [len(block)], 'The listener still receives every block')
        expected = loudness(3000 / math.sqrt(2) / 32768)
        self.assertAlmostEqual(levels.input_level(levels.microphone_at), expected, delta=.03)
        self.assertEqual(levels.input_level(levels.microphone_at + .5), 0.0)
        listener.callback(bytes(2560), 1280, None, None)
        self.assertEqual(levels.input_level(levels.microphone_at), 0.0)


FAKE_CLIENT = textwrap.dedent('''
    import json, os, socket, sys
    key = sys.argv[1] if len(sys.argv) > 1 else os.environ['JARVIS_UI_KEY']
    connection = socket.create_connection(('127.0.0.1', int(os.environ['JARVIS_UI_PORT'])), timeout=10)
    stream = connection.makefile('rwb')
    stream.write((key + '\\n').encode()); stream.flush()
    stream.write(b'{"t": "ready"}\\n{"t": "call", "id": 7, "method": "echo", "params": {"text": "hi"}}\\n'); stream.flush()
    for raw in stream:
        message = json.loads(raw)
        if message.get('t') == 'event' and message.get('name') == 'quit':
            break
        if message.get('t') == 'reply':
            stream.write(json.dumps({'t': 'call', 'id': 8, 'method': 'got', 'params': message}).encode() + b'\\n'); stream.flush()
''')


class BridgeTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.script = Path(temporary.name) / 'client.py'
        self.script.write_text(FAKE_CLIENT, encoding='utf-8')
        patcher = patch('desktop.ui_bridge.diagnostic')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_authenticated_client_exchanges_calls_events_and_replies(self):
        calls = queue.Queue()
        ready = threading.Event()
        bridge = UIBridge(lambda ident, method, params: calls.put((ident, method, params)), ready.set,
                          command=[sys.executable, str(self.script)])
        bridge.start()
        self.addCleanup(bridge.stop)
        self.assertTrue(ready.wait(15), 'The client never reported ready')
        self.assertEqual(calls.get(timeout=5), (7, 'echo', {'text': 'hi'}))
        bridge.reply(7, True, {'echo': 'hi'})
        ident, method, params = calls.get(timeout=5)
        self.assertEqual((ident, method, params['result']), (8, 'got', {'echo': 'hi'}))
        self.assertTrue(bridge.send('state', {'state': 'ready'}))
        bridge.stop()
        self.assertFalse(bridge.running, 'The interface must exit when Jarvis quits')

    def test_a_client_with_the_wrong_key_is_refused(self):
        ready = threading.Event()
        bridge = UIBridge(lambda *_args: None, ready.set, arguments=['not-the-key'],
                          command=[sys.executable, str(self.script)])
        bridge.start()
        self.addCleanup(bridge.stop)
        self.assertFalse(ready.wait(3), 'A client without the launch key was accepted')
        self.assertIsNone(bridge.connection)


if __name__ == '__main__':
    unittest.main()
