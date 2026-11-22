import copy
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from desktop.controller import Controller
from desktop.intent import validate_decision
from tests.desktop.intent_fixtures import decision


def mixed():
    return decision(action='compound', steps=[
        decision('list_running_processes', 'terminate_processes', request='Force quit Nebula and Lark.',
                 process_names=['Nebula', 'Lark'], depends_on=[]),
        decision(action='computer', request='Open example.com in Quartz Browser.', browser='Quartz Browser', depends_on=[]),
    ])


class CompoundValidationTests(unittest.TestCase):
    def test_each_step_receives_only_its_own_targets_and_capabilities(self):
        selected = mixed()
        selected.update(capabilities=['terminate_processes'], process_names=['Quartz Browser'])
        result = validate_decision(selected)
        self.assertEqual(result['capabilities'], [])
        self.assertEqual(result['process_names'], [])
        self.assertEqual(result['steps'][0]['process_names'], ['Nebula', 'Lark'])
        self.assertEqual(result['steps'][1]['capabilities'], ['start_computer_task', 'computer_status'])
        self.assertEqual(result['steps'][1]['process_names'], [])

    def test_invalid_steps_and_forward_dependencies_never_dispatch(self):
        malformed = []
        for change in ({'action': 'compound'}, {'request': ''}, {'depends_on': [1]},
                       {'depends_on': [True]}, {'capabilities': ['invented_tool']}):
            selected = mixed()
            selected['steps'][0].update(change)
            malformed.append(selected)
        malformed.append(decision(action='compound', steps=[]))
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                validate_decision(value)

    def test_ambiguous_termination_requires_clarification_before_any_step(self):
        selected = mixed()
        selected['steps'][0]['process_names'] = []
        self.assertTrue(validate_decision(selected)['needs_clarification'])


class CompoundExecutionTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(queue.Queue())
        self.addCleanup(self.controller.tasks.close)
        self.addCleanup(self.controller.research.close)
        self.addCleanup(self.controller.browser.close)

    def execute(self, selected, process_ok=True):
        c = self.controller
        seen = []
        def model(worker, _model, text, _decision, **_kwargs):
            seen.append(copy.deepcopy(worker.request))
            self.assertEqual(worker.request['ownerText'], selected['steps'][0]['request'])
            self.assertNotIn('Quartz', text)
            worker.request['processControlResult'] = {'ok': process_ok, 'error': 'Windows denied termination.'}
            return {'taskStatus': 'completed', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': 'Requested processes stopped.'}]}]}
        def start(text, scope, **kwargs):
            seen.append({'ownerText': text, 'domain': scope})
            self.assertNotIn('Nebula', text)
            self.assertNotIn('Lark', text)
            return {'ok': True, 'id': 'child-test', 'scope': scope, 'note': 'Started'}
        with patch('desktop.controller.classify_request', return_value=validate_decision(selected)), \
             patch('desktop.controller.acknowledge', return_value='I will handle both actions.') as ack, \
             patch.object(Controller, 'ask_model', model), \
             patch.object(c.research, 'start', side_effect=start), \
             patch.object(c.research, 'status', return_value={'ok': True, 'id': 'child-test', 'scope': 'computer',
                 'status': 'completed', 'summary': 'The page is open.'}):
            result = c.respond('Force quit Nebula and Lark. Open example.com in Quartz Browser.', speak=False)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                job = c.activity_status(result['taskId'])
                if job['status'] in ('completed', 'failed', 'cancelled'):
                    break
                time.sleep(.01)
            else:
                self.fail('Compound task did not finish')
            self.assertEqual(ack.call_args.args[2]['plan']['action'], 'compound')
        return job, seen

    def test_actions_execute_with_separate_text_targets_and_verified_results(self):
        job, seen = self.execute(mixed())
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]['processNames'], ['Nebula', 'Lark'])
        self.assertEqual(seen[0]['capabilities'], ['list_running_processes', 'terminate_processes'])
        self.assertEqual(seen[1]['ownerText'], 'Open example.com in Quartz Browser.')
        self.assertEqual(len(job['actionResults']), 2)
        self.assertIn('stopped', job['summary'])
        self.assertIn('page is open', job['summary'])
        self.assertEqual(job['childIds'], ['child-test'])
        self.assertEqual(job['scope'], 'tasks')

    def test_independent_browser_action_runs_after_process_failure_and_failure_is_preserved(self):
        job, seen = self.execute(mixed(), process_ok=False)
        self.assertEqual(job['status'], 'failed', job)
        self.assertEqual(len(seen), 2)
        self.assertIn('Windows denied termination', job['summary'])
        self.assertIn('page is open', job['summary'])

    def test_dependent_action_is_skipped_when_its_prerequisite_fails(self):
        selected = mixed()
        selected['steps'][1]['depends_on'] = [0]
        job, seen = self.execute(selected, process_ok=False)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(len(seen), 1)
        self.assertIn('Skipped', job['actionResults'][1]['error'])

    def test_cancellation_between_steps_prevents_further_actions(self):
        tasks = self.controller.tasks
        prepared = tasks.prepare('Several actions', 'tasks', decision=mixed())
        job = tasks.jobs[prepared['id']]
        def first(*_args):
            job['_cancel'].set()
            return {'ok': True, 'text': 'First done'}
        with patch.object(tasks, 'run_step', side_effect=first) as run:
            result = tasks.run_plan(job)
        self.assertFalse(result['ok'])
        self.assertEqual(run.call_count, 1)

    def test_clarification_is_not_success_and_cannot_satisfy_a_dependency(self):
        tasks = self.controller.tasks
        selected = mixed()
        selected['steps'][1]['depends_on'] = [0]
        prepared = tasks.prepare('Several actions', 'tasks', decision=selected)
        job = tasks.jobs[prepared['id']]
        worker = Mock()
        worker.respond.return_value = {'ok': True, 'needsClarification': True, 'text': 'Which one?'}
        with patch.object(self.controller, 'make_task_agent', return_value=worker):
            result = tasks.run_plan(job)
        self.assertFalse(result['ok'])
        self.assertTrue(result['needsClarification'])
        self.assertEqual(worker.respond.call_count, 1)

    def test_earlier_child_remains_resolvable_after_a_later_step(self):
        tasks = self.controller.tasks
        prepared = tasks.prepare('Several actions', 'tasks', decision=mixed())
        job = tasks.jobs[prepared['id']]
        tasks.update(job, childId='child-second', childIds=['child-first', 'child-second'])
        self.assertEqual(self.controller.activity_status('child-first')['id'], prepared['id'])


if __name__ == '__main__':
    unittest.main()
