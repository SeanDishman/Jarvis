import unittest
from desktop.task_progress import current_tasks


class TaskProgressTests(unittest.TestCase):
    def test_acknowledgement_finishing_does_not_clear_running_work(self):
        job = {'id': 'one', 'status': 'queued', 'stage': 'acknowledging',
               'question': 'Check the time in Nagasaki',
               'acknowledgement': "I'm checking the current time in Nagasaki, Japan."}
        self.assertEqual(current_tasks([job])[0]['title'], job['acknowledgement'])
        job.update(status='running', stage='working', note='Selected Sol')
        row = current_tasks([job])[0]
        self.assertEqual(row['detail'], 'Working on your request')
        for terminal in ('completed', 'failed', 'cancelled'):
            job['status'] = terminal
            self.assertEqual(current_tasks([job]), [])

    def test_foreground_and_independent_jobs_keep_their_own_lifetimes(self):
        jobs = [{'id': 'task-a', 'status': 'running', 'question': 'Find flights', 'childId': 'web-a'},
                {'id': 'web-a', 'status': 'running', 'question': 'Find flights'},
                {'id': 'task-b', 'status': 'queued', 'question': 'Open Notepad'}]
        request = {'id': 'chat', 'ownerText': 'What time is it?'}
        self.assertEqual([r['id'] for r in current_tasks(jobs, request)], ['chat', 'task-a', 'task-b'])
        self.assertEqual([r['id'] for r in current_tasks(jobs, request, 'speaking')], ['task-a', 'task-b'])
        jobs[0]['status'] = 'completed'
        self.assertEqual([r['id'] for r in current_tasks(jobs)], ['task-b'])

    def test_tool_progress_uses_readable_labels(self):
        rows = current_tasks([{'id': 'one', 'status': 'running', 'question': 'Read this page',
                               'note': 'Running browser read', 'stage': 'working'}])
        self.assertEqual(rows[0]['detail'], 'Reading the page')

    def test_running_work_is_visible_before_newly_queued_tasks(self):
        jobs = [{'id': 'queued', 'status': 'queued', 'question': 'Open Notepad'},
                {'id': 'running', 'status': 'running', 'question': 'Check the time'}]
        self.assertEqual([row['id'] for row in current_tasks(jobs)], ['running', 'queued'])


if __name__ == '__main__':
    unittest.main()
