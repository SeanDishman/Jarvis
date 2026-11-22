import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop.common import ROOT
from desktop.memory import Memory
from desktop.preferences import Preferences
from desktop.routing import capabilities_for
from tests.desktop.intent_fixtures import decision
from desktop.storage import local_path


class PersonalToolsTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.cache').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.cache')
        self.root = Path(self.temp.name)
        self.preferences = Preferences(self.root / 'settings.json')
        self.preferences.save({'memoryVault': str(self.root / 'Brain')})
        self.memory = Memory(self.preferences)

    def tearDown(self):
        self.temp.cleanup()

    def test_memory_survives_restart_and_external_edit_then_archive(self):
        saved = self.memory.save('Display preference', 'Use a dark theme.')
        recovered = Memory(self.preferences)
        self.assertEqual(recovered.search('theme')['memories'][0]['id'], saved['id'])
        row = recovered.read(saved['id'])
        Path(row['path']).write_text('# Display preference\n\nUse a light theme.', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'changed'):
            recovered.save('Display preference', 'Stale overwrite', saved['id'], row['revision'])
        fresh = recovered.read(saved['id'])
        archived = recovered.forget(saved['id'], fresh['revision'])
        self.assertTrue(Path(archived['archived']).exists())
        self.assertEqual(Memory(self.preferences).search('theme')['memories'], [])

    def test_paths_and_memory_ids_cannot_escape_vault(self):
        with self.assertRaises(ValueError):
            self.memory.read('../settings')
        with self.assertRaises(ValueError):
            local_path(self.root / 'Brain' / '..' / 'outside.txt', [self.root / 'Brain'])
        with self.assertRaises(ValueError):
            local_path(self.root / 'Brain' / '.obsidian' / 'app.json', [self.root / 'Brain'])

    def test_recall_has_no_write_or_public_tools(self):
        tools, _, domain = capabilities_for(decision('search_memory','read_memory'))
        self.assertEqual(domain, 'memory')
        self.assertEqual(set(tools), {'search_memory', 'read_memory'})
        self.assertIn('save_memory', capabilities_for(decision('search_memory','save_memory'))[0])


if __name__ == '__main__':
    unittest.main()
