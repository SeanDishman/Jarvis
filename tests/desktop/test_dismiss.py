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


def decision(action):
    result = {'action': action, 'delay_seconds': None, 'wake_at': None}
    return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]}


class DismissTests(unittest.TestCase):
    """'We're not doing it, go away' drops what Jarvis is asking about or doing. It never sends Jarvis away."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='dismiss-test-')
        self.addCleanup(folder.cleanup)
        self.breaks = BreakState(Path(folder.name) / 'break.json', lambda: 1000.0)
        self.controller = Controller(queue.Queue())
        self.controller.breaks = self.breaks

    def notices(self):
        rows = []
        while not self.controller.events.empty():
            event = self.controller.events.get()
            if event[0] == 'notice':
                rows.append(event[1])
        return rows

    def test_dropping_the_question_he_is_asking_keeps_jarvis_here(self):
        self.controller.questions.raised('Plan my week', 'Should I put the gym on Monday or Tuesday?')
        with patch('desktop.controller.local_request', return_value=decision('dismiss')) as classify:
            self.controller.interrupt("Jarvis, we're not doing it, go away")
        sent = json.loads(classify.call_args.args[2]['input'])
        self.assertEqual(sent['openQuestion'], 'Should I put the gym on Monday or Tuesday?')
        self.assertFalse(self.breaks.active, 'Jarvis did not hide or stop listening')
        self.assertEqual(self.controller.questions.open(), [], 'The question is dropped, so it is not asked again')
        self.assertTrue(self.controller.cancelled.is_set(), 'He stopped talking')
        self.assertIn('Dropped it.', self.notices())

    def test_a_task_paused_on_the_question_is_cancelled_with_it(self):
        row = {'id': 'task-1', 'taskId': 'task-1', 'request': 'Book it', 'question': 'Which day?', 'raised': time.time()}
        with patch.object(self.controller.tasks, 'questions', return_value=[row]), \
             patch.object(self.controller.tasks, 'cancel', return_value={'ok': True}) as cancel, \
             patch('desktop.controller.local_request', return_value=decision('dismiss')):
            self.controller.interrupt('never mind, forget it')
        cancel.assert_called_once_with('task-1')
        self.assertFalse(self.breaks.active)

    def test_without_a_question_the_task_he_just_started_is_cancelled(self):
        self.controller.last_task_id = 'task-2'
        running = {'id': 'task-2', 'status': 'running', 'scope': 'computer', 'question': 'Reorder my desktop', 'created': time.time()}
        with patch.object(self.controller, 'activity_list', return_value=[running]), \
             patch.object(self.controller, 'activity_cancel', return_value={'ok': True}) as cancel, \
             patch('desktop.controller.local_request', return_value=decision('dismiss')):
            self.controller.interrupt("we're not doing that")
        cancel.assert_called_once_with('task-2')
        self.assertIn('Dropped it.', self.notices())

    def test_an_older_task_is_left_running(self):
        self.controller.last_task_id = 'task-3'
        old = {'id': 'task-3', 'status': 'running', 'scope': 'web', 'question': 'Research', 'created': time.time() - 3600}
        with patch.object(self.controller, 'activity_list', return_value=[old]), \
             patch.object(self.controller, 'activity_cancel') as cancel, \
             patch('desktop.controller.local_request', return_value=decision('dismiss')):
            self.controller.interrupt('never mind')
        cancel.assert_not_called()
        self.assertIn('Stopped.', self.notices())

    def test_an_explicit_request_for_jarvis_to_go_is_confirmed_first(self):
        with patch('desktop.controller.local_request', return_value=decision('break')), \
             patch('desktop.controller.acknowledge', return_value='Sir, you want me to shut off for now?'), \
             patch.object(self.controller, 'speak') as speak:
            self.controller.interrupt('Jarvis, power off for now')
        speak.assert_called_once_with('Sir, you want me to shut off for now?')
        self.assertFalse(self.breaks.active)
        with patch('desktop.controller.classify_request', return_value={'action': 'break', 'delay_seconds': None, 'wake_at': None}):
            self.controller.respond('confirm', speak=False)
        self.assertTrue(self.breaks.active)

    def test_a_no_keeps_jarvis_here_and_an_old_question_lapses(self):
        reply = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Alright, sir, I will stay.'}]}]}
        with patch('desktop.controller.classify_request', side_effect=[{'action': 'break', 'delay_seconds': None, 'wake_at': None},
                                                                       {'action': 'normal'}]), \
             patch('desktop.controller.acknowledge', return_value='Sir, you really want me to shut off?'), \
             patch('desktop.controller.local_request', return_value=reply):
            self.controller.respond('Jarvis power off', speak=False)
            self.assertIsNotNone(self.controller.break_confirmation_pending())
            self.controller.respond('no, stay', speak=False)
        self.assertFalse(self.breaks.active)
        self.assertIsNone(self.controller.break_confirmation_pending())
        # A yes long after the question is a new request, so he would ask again.
        self.controller.pending_break = {'decision': {'action': 'break', 'delay_seconds': None, 'wake_at': None},
                                         'at': time.monotonic() - 120}
        self.assertIsNone(self.controller.break_confirmation_pending())


if __name__ == '__main__':
    unittest.main()
