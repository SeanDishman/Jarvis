import json
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop.breaks import BreakState
from desktop.common import ROOT
from desktop.controller import Controller
from desktop.listener import Listener


class BreakTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='break-test-')
        self.addCleanup(self.temp.cleanup)
        self.clock = [1000.0]
        self.state = BreakState(Path(self.temp.name) / 'break.json', lambda: self.clock[0])
        self.controller = Controller(queue.Queue())
        self.controller.breaks = self.state

    def test_deadline_survives_restart_and_sleep(self):
        self.state.start(60)
        restored = BreakState(self.state.path, lambda: self.clock[0])
        self.assertTrue(restored.active)
        self.clock[0] += 120
        self.assertTrue(restored.finish_due())
        self.assertFalse(restored.active)
        self.assertFalse(BreakState(self.state.path, lambda: self.clock[0]).active)

    def test_indefinite_break_requires_explicit_resume(self):
        self.state.start()
        self.clock[0] += 86400
        self.assertFalse(self.state.finish_due())
        self.controller.resume()
        self.assertFalse(self.state.active)

    def test_background_loop_wakes_when_the_deadline_passes(self):
        self.state.start(60)
        self.clock[0] += 61
        with patch.object(self.controller.closed, 'wait', side_effect=[False, True]), \
             patch.object(self.controller.reminders, 'poll', return_value=[]), \
             patch.object(self.controller.briefing, 'scheduled_today', return_value=False):
            self.controller.background_loop()
        self.assertFalse(self.state.active)
        self.assertEqual(self.controller.events.get_nowait(), ('break_changed',))

    def test_request_waiting_on_a_reply_cannot_run_after_a_break_begins(self):
        def acquire(**_kwargs):
            self.state.start(60)
            return True
        with patch.object(self.controller, 'busy') as busy, patch('desktop.controller.classify_request') as intent:
            busy.acquire.side_effect = acquire
            self.assertFalse(self.controller.respond('Click that button', speak=False, wait=True)['ok'])
            busy.release.assert_called_once()
        intent.assert_not_called()

    def test_invalid_duration_does_not_hide_assistant(self):
        for value in (True, 0, -1, float('nan'), float('inf'), '60'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.state.start(value)
        self.assertFalse(self.state.active)
        with self.assertRaises(ValueError):
            self.controller.take_break({'action': 'break'})

    def test_natural_request_uses_ai_and_cancels_tasks(self):
        command = 'Give me some space until this meeting wraps up in twenty minutes'
        decision = {'action':'break', 'delay_seconds':1200, 'wake_at':None}
        with patch('desktop.controller.classify_request', return_value=decision) as intent, \
             patch('desktop.controller.acknowledge', return_value='Sir, you want me gone for twenty minutes?'), \
             patch.object(self.controller.research, 'list', return_value=[{'id':'pc', 'status':'running', 'scope':'computer', 'question':'Click the requested button', 'created':time.time()}]), \
             patch.object(self.controller.research, 'cancel') as cancel, patch('desktop.controller.local_request') as reply:
            asked = self.controller.respond(command, speak=False)
            # He asks first, and nothing stops until the owner confirms.
            self.assertEqual((asked['confirmBreak'], asked['text']), (True, 'Sir, you want me gone for twenty minutes?'))
            self.assertFalse(self.state.active)
            cancel.assert_not_called()
            result = self.controller.respond('Yes, I am sure', speak=False)
        self.assertEqual(intent.call_args_list[0].args[:2], (command, self.controller.session))
        self.assertIsNone(intent.call_args_list[0].args[2]['topic'])
        self.assertEqual(intent.call_args_list[0].args[2]['recentTasks'][0]['id'], 'pc')
        self.assertTrue(intent.call_args_list[1].args[2]['awaitingBreakConfirmation'])
        cancel.assert_called_once_with('pc')
        reply.assert_not_called()
        self.assertTrue(result['ok'])
        self.assertTrue(self.state.active)
        self.assertEqual(self.state.snapshot()['wakeAt'], 2200)

    def test_break_suppresses_microphone_new_commands_and_tools(self):
        listener = Listener(self.controller, {})
        self.state.start(60)
        listener.callback(b'audio', 1, None, None)
        self.assertTrue(listener.audio.empty())
        listener.activate()
        self.assertEqual(listener.listening_until, 0)
        with patch('desktop.controller.classify_request') as intent:
            self.assertFalse(self.controller.respond('Read my screen')['ok'])
        intent.assert_not_called()
        self.controller.request = {'id':'old', 'capabilities':['get_weather'], 'executed':[]}
        with patch.object(self.controller, 'execute_tool') as execute:
            self.assertFalse(self.controller.tool('old', 'get_weather', {})['ok'])
        execute.assert_not_called()

    def test_break_can_interrupt_an_answer(self):
        result = {'action':'break', 'delay_seconds':90, 'wake_at':None}
        response = {'output':[{'type':'message', 'content':[{'type':'output_text', 'text':json.dumps(result)}]}]}
        with patch('desktop.controller.local_request', return_value=response), \
             patch('desktop.controller.acknowledge', return_value='Sir, a minute and a half away, you are sure?'), \
             patch.object(self.controller, 'speak') as speak:
            self.controller.interrupt('Disappear for a minute and a half')
        self.assertFalse(self.state.active, 'An interruption alone never sends him away')
        speak.assert_called_once_with('Sir, a minute and a half away, you are sure?')
        self.assertFalse(self.controller.speech_paused.is_set())
        with patch('desktop.controller.classify_request', return_value={'action': 'break', 'delay_seconds': None, 'wake_at': None}):
            self.controller.respond('Yes, go ahead', speak=False)
        self.assertTrue(self.state.active)
        self.assertEqual(self.state.snapshot()['wakeAt'], 1090, 'The time he asked about is kept')


if __name__ == '__main__':
    unittest.main()
