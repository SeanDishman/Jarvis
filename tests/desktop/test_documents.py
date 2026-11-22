from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop.common import ROOT
from desktop.documents import Documents
from desktop.document_search import DocumentSearch
from desktop.files import FileTools
from desktop.preferences import Preferences


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.cache')
        self.root = Path(self.temp.name)
        self.prefs = Preferences(self.root / 'settings.json')
        self.prefs.save({'fileWriteRoots':[str(self.root / 'Documents')]})
        self.files = FileTools(self.prefs)
        self.docs = Documents(self.prefs, self.files)
        self.docs.backup_root = self.root / 'backups'
        self.location = self.docs.locations()['locations'][0]['id']

    def tearDown(self):
        self.temp.cleanup()

    def test_create_edit_conflict_backup_move_and_revocation(self):
        row = self.docs.create(self.location, 'sample.txt', 'first version')
        with self.assertRaises(FileExistsError):
            self.docs.create(self.location, 'sample.txt', 'clobber')
        read = self.files.read(row['id'])
        edit = self.docs.edit(row['id'], read['revision'], 'replace', 'first', 'second')
        self.assertEqual(Path(edit['backup']).read_text(), 'first version')
        self.assertEqual(self.files.read(row['id'])['text'], 'second version')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.docs.edit(row['id'], read['revision'], 'append', text='stale')
        moved = self.docs.move(row['id'], edit['revision'], self.location, 'Sorted/new.txt')
        self.assertEqual(Path(moved['path']).read_text(), 'second version')
        with self.assertRaises(ValueError):
            self.docs.destination(self.location, '../escape.txt')
        self.prefs.save({'fileWriteRoots':[]})
        self.assertFalse(self.files.read(moved['id'])['ok'])

    def test_word_and_workbook_roundtrip(self):
        word = self.docs.create(self.location, 'report.docx', 'The total is fifty.')
        read = self.files.read(word['id'])
        self.assertIn('fifty', read['text'])
        self.docs.edit(word['id'], read['revision'], 'replace', 'fifty', 'sixty')
        self.assertIn('sixty', self.files.read(word['id'])['text'])
        book = self.docs.create(self.location, 'budget.xlsx', rows=[['Item','Cost'],['Desk',50]])
        read = self.files.read(book['id'])
        self.docs.edit(book['id'], read['revision'], 'cells', cells=[{'cell':'B2','value':60}])
        self.assertIn('B2: 60', self.files.read(book['id'])['text'])

    def test_content_search_finds_body_and_revokes_cached_results(self):
        self.docs.create(self.location, 'unrelated-name.txt', 'The lunar rover needs replacement wheels.')
        search = DocumentSearch(self.files, self.root / 'index.sqlite3')
        result = search.search('lunar rover', semantic=False)
        self.assertEqual(result['files'][0]['name'], 'unrelated-name.txt')
        self.prefs.save({'fileWriteRoots':[]})
        self.assertFalse(search.search('lunar rover', semantic=False)['ok'])


if __name__ == '__main__':
    unittest.main()
