import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from desktop.common import ROOT
from desktop.projects import Projects


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=ROOT / '.cache', prefix='projects-test-')
        self.addCleanup(self.folder.cleanup)
        self.projects = Projects(Path(self.folder.name) / 'projects')

    def test_research_survives_restart_and_resume_uses_saved_context(self):
        job = {'status': 'completed', 'scope': 'web', 'question': 'Compare laptops',
               'summary': 'Model A has the longest battery life.', 'sources': [{'title': 'Manufacturer', 'url': 'https://example.com/'}]}
        saved = self.projects.save('Laptop research', decisions='Prefer quiet fans', next_steps='Compare weight', job=job)
        reopened = Projects(self.projects.directory)
        self.assertEqual(reopened.read(saved['id'])['sources'], job['sources'])
        self.assertEqual(reopened.list('laptop')['projects'][0]['id'], saved['id'])
        research = Mock()
        research.start.return_value = {'ok': True, 'id': 'new-job'}
        resumed = reopened.resume(saved['id'], research, 'Continue yesterday’s laptop research')
        self.assertEqual(resumed['id'], 'new-job')
        query = research.start.call_args.args[0]
        for text in ('quiet fans', 'Compare weight', 'longest battery life', 'https://example.com/'):
            self.assertIn(text, query)
        self.assertEqual(reopened.read(saved['id'])['revision'], saved['revision'])

    def test_private_projects_do_not_start_public_research_and_updates_conflict(self):
        saved = self.projects.save('Private notes', 'Internal notes')
        research = Mock()
        self.projects.resume(saved['id'], research, 'Resume my notes')
        research.start.assert_not_called()
        self.projects.save(saved['title'], saved['findings'], 'New decision', identifier=saved['id'], expected=saved['revision'])
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.projects.save(saved['title'], 'Stale content', identifier=saved['id'], expected=saved['revision'])
        with self.assertRaises(ValueError):
            self.projects.read('../settings')

    def test_invalid_and_unfinished_projects_are_not_saved(self):
        with self.assertRaises(ValueError):
            self.projects.save('')
        with self.assertRaises(ValueError):
            self.projects.save('Pending', job={'status':'running'})
        self.assertEqual(self.projects.list()['projects'], [])
