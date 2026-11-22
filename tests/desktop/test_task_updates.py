import json
import queue
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop.controller import Controller
from desktop.intent import classify_request


def decision(identifier):
    return {'capabilities': [], 'system_actions': [], 'browser': '', 'action': 'task_status', 'task_id': identifier, 'model_tier': 'default', 'needs_clarification': False}


class TaskUpdateTests(unittest.TestCase):
    def setUp(self):
        self.c = Controller(queue.Queue())
        self.c.breaks = SimpleNamespace(active=False)
        self.c.preferences.read = lambda: {'offlineSpeech': 'automatic'}
        self.addCleanup(self.c.browser.close)
        self.addCleanup(self.c.research.close)
        self.addCleanup(self.c.tasks.close)
        prepared = self.c.tasks.prepare('Minimize everything on my second monitor', 'computer')
        self.identifier = prepared['id']
        job = self.c.tasks.jobs[self.identifier]
        self.c.tasks.update(job, status='failed', stage='failed', note='Task failed',
                            summary='Could not minimize Task Manager. Windows denied access.', finished=time.time(),
                            childId='child-computer', steps=['Minimized Opera (verified)'],
                            windowResults=[{'title': 'Opera', 'action':'minimize_window', 'state':'minimized', 'verified': True},
                                           {'title': 'Task Manager', 'verified': False, 'error': 'Windows denied access.'}])

    def test_update_after_topic_timeout_reads_partial_failure_without_rerunning(self):
        self.c.last_domain = 'web'
        self.c.last_answer_text = 'Four, sir.'
        self.c.last_command_time = time.monotonic() - 120
        self.c.last_task_id = self.identifier
        def classify(text, session, context):
            self.assertIsNone(context['topic'])
            self.assertEqual(context['recentTasks'][0]['id'], self.identifier)
            self.assertIn('Task Manager', context['recentTasks'][0]['summary'])
            return decision(self.identifier)
        with patch('desktop.controller.classify_request', side_effect=classify), \
             patch.object(self.c.research, 'start') as start, patch.object(self.c, 'ask_model') as answer:
            result = self.c.submit('Can I get an update?', speak=False)
        self.assertTrue(result['ok'], result)
        self.assertIn('Minimized 1 window', result['text'])
        self.assertIn('Task Manager', result['text'])
        self.assertEqual(result['tools'], ['task_status'])
        self.assertEqual(result['taskReference'], self.identifier)
        start.assert_not_called()
        answer.assert_not_called()

    def test_status_tool_resolves_parent_and_child_ids(self):
        for identifier in (self.identifier, 'child-computer', ''):
            result = self.c.execute_tool({'capabilities':['computer_status']}, 'computer_status', {'id': identifier})
            self.assertTrue(result['ok'])
            self.assertEqual(result['id'], self.identifier)
            self.assertEqual(result['status'], 'failed')

    def test_semantic_followup_does_not_become_its_own_task_context(self):
        def classify(text, session, context):
            self.assertEqual([row['id'] for row in context['recentTasks']], [self.identifier])
            return decision(self.identifier)
        with patch('desktop.controller.acknowledge', return_value='Checking your task, sir.'), \
             patch('desktop.controller.classify_request', side_effect=classify), \
             patch.object(self.c.research, 'start') as start:
            result = self.c.submit('Could you tell me how that went?', speak=False)
            self.c.tasks.pending.join()
        self.assertNotIn('taskId', result)
        self.assertEqual(result['taskReference'], self.identifier)
        self.assertIn('Task Manager', result['text'])
        self.assertEqual([row['id'] for row in self.c.task_context()], [self.identifier])
        self.assertEqual(self.c.task_context()[0]['started'], 'just now', 'Recent tasks say when they happened')
        self.assertEqual(self.c.activity_status()['id'], self.identifier)
        start.assert_not_called()

    def test_explicit_older_task_selection_survives_newer_unrelated_job(self):
        newer = self.c.tasks.prepare('Check the weather', 'weather')
        self.c.last_task_id = newer['id']
        with patch('desktop.controller.classify_request', return_value=decision(self.identifier)):
            result = self.c.respond('What happened with minimizing the windows?', speak=False)
        self.assertIn('Task Manager', result['text'])
        self.assertEqual(self.c.last_task_id, self.identifier)

    def test_spoken_completion_updates_conversation_reference(self):
        self.c.voice_ready = True
        self.c.completed_tasks.put(self.c.activity_status(self.identifier))
        with patch.object(self.c, 'speak') as speak:
            self.c.deliver_task_completion()
        self.assertIn('Task Manager', speak.call_args.args[0])
        self.assertIn('Task Manager', self.c.last_answer_text)
        self.assertEqual(self.c.last_task_id, self.identifier)
        self.assertEqual(self.c.last_domain, 'computer')

    def test_classifier_rejects_invented_task_reference(self):
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(decision('invented'))}]}]}
        with patch('desktop.intent.local_request', return_value=response), self.assertRaisesRegex(RuntimeError, 'task reference'):
            classify_request('Give me an update', 'fixture', {'recentTasks': self.c.task_context()})

    def test_spoken_status_query_keeps_the_original_task_domain(self):
        self.c.voice_ready = True
        self.c.completed_tasks.put({'id': 'status-query', 'scope': 'task_status',
                                   'taskReference': self.identifier, 'summary': 'Task Manager could not be minimized.'})
        with patch.object(self.c, 'speak'):
            self.c.deliver_task_completion()
        self.assertEqual(self.c.last_answer_domain, 'computer')
        self.assertEqual(self.c.last_task_id, self.identifier)
        self.assertIn(self.c.last_answer_domain, self.c.sessions)

if __name__ == '__main__':
    unittest.main()
