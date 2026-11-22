from tests.desktop.intent_fixtures import decision
import json
import pathlib
import queue
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from desktop.controller import Controller
from desktop.research import ResearchJobs, validate_plan, validate_summary


def response(value):
    return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}


def wait_finished(jobs, job_id):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        result = jobs.status(job_id)
        if result['status'] in ('completed', 'failed', 'cancelled'):
            return result
        time.sleep(.01)
    raise AssertionError('Search did not complete')


@patch('desktop.controller.classify_request', lambda *_args: {'action': 'normal'})
class ResearchTests(unittest.TestCase):
    def test_search_plan_bounds_and_provider_neutral_keywords(self):
        plan = validate_plan({'queries': ['  NASA  Artemis dates ', 'NASA Artemis dates', 'Artemis NASA schedule'],
                              'keywords': ['NASA', 'Artemis'], 'unreadOnly': False}, 'web')
        self.assertEqual(plan['queries'], ['NASA Artemis dates', 'Artemis NASA schedule'])
        with self.assertRaises(ValueError):
            validate_plan({'queries': [''], 'keywords': []}, 'web')
        with self.assertRaises(ValueError):
            validate_plan({'queries': ['a' * 401], 'keywords': []}, 'web')
        self.assertEqual(validate_plan({'queries': [''], 'keywords': []}, 'email')['queries'], [''])

    def test_summary_must_reference_actual_evidence(self):
        evidence = [{'sourceId': 1, 'title': 'NASA', 'url': 'https://www.nasa.gov/', 'text': 'Evidence'}]
        with self.assertRaises(ValueError):
            validate_summary({'summary': 'Made up', 'sourceIds': [99]}, evidence)
        summary, sources = validate_summary({'summary': 'A grounded answer, sir.', 'sourceIds': [1, 1]}, evidence)
        self.assertEqual(summary, 'A grounded answer, sir.')
        self.assertEqual(len(sources), 1)
        self.assertNotIn('text', sources[0])

    def test_search_runs_while_foreground_conversation_and_progress_work(self):
        controller = Controller(queue.Queue())
        controller.tasks = None
        planning, release = threading.Event(), threading.Event()
        def research_http(_port, route, payload=None, **_kwargs):
            if route == '/models':
                return {'resolvedSearchModel': 'test-search-model'}
            if route == '/session/end':
                return {}
            self.assertEqual(payload['selectedModel'], 'test-search-model')
            self.assertNotIn('capabilities', payload)
            if payload['task'] == 'plan':
                planning.set()
                release.wait(3)
                return response({'keywords': ['NASA', 'Artemis'], 'queries': ['NASA Artemis schedule']})
            self.assertIn('NASA Artemis schedule', payload['input'])
            return response({'summary': 'Here are the findings, sir.', 'sourceIds': [1]})
        chat = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Four, sir.'}]}]}
        def foreground_model(_port, _path, payload, **_kwargs):
            if payload['input'] == 'Search the web for the Artemis schedule':
                result = controller.tool(payload['desktopRequestId'], 'start_web_search', {'question':'The Artemis schedule'})
                self.assertTrue(result['ok'], result)
            return chat
        with patch('desktop.controller.classify_request', side_effect=[decision('start_web_search'),decision(action='task_status'),decision()]), patch('desktop.research.local_request', side_effect=research_http), \
             patch('desktop.research.search_web', return_value={'ok': True, 'results': [{'title': 'NASA', 'url': 'https://www.nasa.gov/', 'snippet': 'Schedule'}]}) as search, \
             patch('desktop.research.read_webpage', return_value={'ok': True, 'title': 'NASA', 'text': 'Official schedule'}), \
             patch('desktop.controller.local_request', side_effect=foreground_model) as foreground:
            try:
                began = time.monotonic()
                result = controller.respond('Search the web for the Artemis schedule', speak=False)
                self.assertLess(time.monotonic() - began, .5)
                self.assertTrue(result['ok'])
                self.assertTrue(planning.wait(1))
                self.assertFalse(controller.busy.locked())
                self.assertIn('Choosing focused search terms', controller.respond('How is the search going?', speak=False)['text'])
                self.assertEqual(controller.respond('What is two plus two?', speak=False)['text'], 'Four, sir.')
                self.assertEqual(foreground.call_count, 2)
                release.set()
                finished = wait_finished(controller.research, controller.research.list()[0]['id'])
                self.assertEqual(finished['status'], 'completed', finished)
                self.assertEqual(finished['queries'], ['NASA Artemis schedule'])
                self.assertEqual(finished['pagesRead'], 1)
                search.assert_called_once_with('NASA Artemis schedule')
            finally:
                release.set()
                controller.research.close()

    @patch('desktop.email_policy.enabled', lambda: True)
    def test_email_worker_never_uses_public_search_and_keeps_results_scoped(self):
        mail = Mock(spec=['search', 'read'])
        mail.search.return_value = {'ok': True, 'messages': [{'id': 'msg1', 'provider': 'gmail', 'subject': 'Receipt'}], 'errors': []}
        mail.read.return_value = {'ok': True, 'body': 'Private receipt body'}
        jobs = ResearchJobs(queue.Queue(), mail)
        def request(_port, route, payload=None, **_kwargs):
            if route == '/models':
                return {'resolvedSearchModel': 'test-model'}
            if payload['task'] == 'plan':
                return response({'keywords': ['receipt'], 'queries': ['receipt'], 'unreadOnly': True})
            self.assertIn('Private receipt body', payload['input'])
            return response({'summary': 'One receipt, sir.', 'sourceIds': [1]})
        with patch('desktop.research.local_request', side_effect=request), patch('desktop.research.search_web') as web:
            try:
                started = jobs.start('Find unread receipt emails in Gmail', 'email', 'gmail')
                finished = wait_finished(jobs, started['id'])
                self.assertEqual(finished['status'], 'completed', finished)
                self.assertFalse(jobs.status(started['id'], 'web')['ok'])
                self.assertTrue(jobs.status(started['id'], 'email')['ok'])
                web.assert_not_called()
                mail.search.assert_called_once_with('gmail', 'receipt', True, 5)
                mail.read.assert_called_once_with('msg1')
            finally:
                jobs.close()

    def test_cancelled_planning_cannot_start_a_search(self):
        jobs = ResearchJobs(queue.Queue(), Mock())
        planning, release, returned = threading.Event(), threading.Event(), threading.Event()
        def request(_port, route, payload=None, **_kwargs):
            if route == '/models':
                return {'resolvedSearchModel': 'test-model'}
            if route == '/session/end':
                return {}
            planning.set()
            release.wait(3)
            returned.set()
            return response({'keywords': ['space'], 'queries': ['space']})
        with patch('desktop.research.local_request', side_effect=request), patch('desktop.research.search_web') as web:
            try:
                job = jobs.start('Find space news')
                self.assertTrue(planning.wait(1))
                self.assertEqual(jobs.cancel(job['id'])['status'], 'cancelled')
                release.set()
                self.assertTrue(returned.wait(1))
                jobs.pending.join()
                web.assert_not_called()
                self.assertEqual(jobs.status(job['id'])['status'], 'cancelled')
            finally:
                release.set()
                jobs.close()

    def test_search_failure_is_reported_instead_of_a_fake_answer(self):
        jobs = ResearchJobs(queue.Queue(), Mock())
        def request(_port, route, payload=None, **_kwargs):
            if route == '/models':
                return {'resolvedSearchModel': 'test-model'}
            return response({'keywords': ['test'], 'queries': ['test']})
        with patch('desktop.research.local_request', side_effect=request), \
             patch('desktop.research.search_web', return_value={'ok': False, 'error': 'Provider blocked the query'}):
            try:
                job = jobs.start('Find test information')
                finished = wait_finished(jobs, job['id'])
                self.assertEqual(finished['status'], 'failed')
                self.assertEqual(finished['summary'], '')
                self.assertIn('blocked', finished['error'])
            finally:
                jobs.close()


if __name__ == '__main__':
    unittest.main()
