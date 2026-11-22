import hashlib
import io
from pathlib import Path
import re
import threading

from desktop.common import ROOT
from desktop.storage import local_path, revision, write_file

EDITABLE = {'.txt', '.md', '.csv', '.tsv', '.docx', '.xlsx'}


class Documents:
    def __init__(self, preferences, files):
        self.preferences, self.files = preferences, files
        self.lock = threading.RLock()
        self.backup_root = ROOT / '.openclaw/file-backups'

    def roots(self):
        return [Path(value) for value in self.preferences.read()['fileWriteRoots']]

    def locations(self):
        return {'ok': True, 'locations': [{'id': hashlib.sha256(str(root).casefold().encode()).hexdigest()[:16],
                'name': root.name, 'path': str(root)} for root in self.roots()]}

    def destination(self, location, name):
        matches = [row for row in self.locations()['locations'] if row['id'] == location]
        if len(matches) != 1:
            raise ValueError('Choose a location returned by list_file_locations')
        relative = Path(str(name))
        if relative.is_absolute() or not relative.parts or any(part in ('.', '..') or ':' in part for part in relative.parts):
            raise ValueError('Use a relative filename inside the selected folder')
        path = local_path(Path(matches[0]['path']) / relative, self.roots())
        if path.suffix.lower() not in EDITABLE:
            raise ValueError('Supported writable formats are TXT, Markdown, CSV, TSV, DOCX and XLSX')
        return path

    def _source(self, identifier, expected):
        candidate = self.files.refs.get(identifier)
        if candidate is None:
            raise ValueError('Search and read the file before editing it')
        path = local_path(candidate, self.roots(), existing=True)
        if path.suffix.lower() not in EDITABLE or not path.is_file() or path.stat().st_size > 20_000_000:
            raise ValueError('Choose a supported document smaller than 20 MB')
        if not expected or revision(path.read_bytes()) != expected:
            raise ValueError('The file changed or has not been read. Read it again before editing.')
        return path

    def _saved(self, path, data, expected=None):
        result = write_file(path, data, expected=expected, backup_root=self.backup_root)
        return {'ok': True, 'id': self.files.reference(path), 'name': path.name, **result}

    def create(self, location, name, text='', rows=None):
        path = self.destination(location, name)
        if not isinstance(text, str) or len(text) > 100000:
            raise ValueError('Use at most 100000 characters')
        with self.lock:
            if path.suffix.lower() == '.docx':
                from docx import Document
                document = Document()
                for paragraph in text.split('\n'):
                    document.add_paragraph(paragraph)
                output = io.BytesIO()
                document.save(output)
                data = output.getvalue()
            elif path.suffix.lower() == '.xlsx':
                from openpyxl import Workbook
                if not isinstance(rows, list) or len(rows) > 2000:
                    raise ValueError('Supply up to 2000 spreadsheet rows')
                book = Workbook()
                for row in rows:
                    if not isinstance(row, list) or len(row) > 50 or any(not isinstance(value, (str, int, float, bool, type(None))) for value in row):
                        raise ValueError('Each spreadsheet row can contain up to 50 simple values')
                    book.active.append(row)
                    for cell in book.active[book.active.max_row]:
                        if isinstance(cell.value, str):
                            cell.data_type = 's'
                output = io.BytesIO()
                book.save(output)
                data = output.getvalue()
            else:
                data = text.encode('utf-8')
            return self._saved(path, data)

    def edit(self, identifier, expected, operation, find='', text='', cells=None):
        if not isinstance(text, str) or len(text) > 100000 or not isinstance(find, str) or len(find) > 100000:
            raise ValueError('Keep this edit below 100000 characters')
        with self.lock:
            path = self._source(identifier, expected)
            if path.suffix.lower() == '.xlsx':
                if operation != 'cells' or not isinstance(cells, list) or not 1 <= len(cells) <= 200:
                    raise ValueError('Use a cells edit with up to 200 spreadsheet cells')
                from openpyxl import load_workbook
                book = load_workbook(path, keep_links=True)
                for change in cells:
                    address = change.get('cell', '')
                    if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,5}', address):
                        raise ValueError('Use a cell address such as B2')
                    sheet = change.get('sheet') or book.active.title
                    if sheet not in book.sheetnames:
                        raise ValueError('That worksheet does not exist')
                    value = change.get('value', '')
                    if not isinstance(value, (str, int, float, bool, type(None))):
                        raise ValueError('Use a simple cell value')
                    book[sheet][address] = value
                    if isinstance(value, str):
                        book[sheet][address].data_type = 's'
                output = io.BytesIO()
                book.save(output)
                data = output.getvalue()
            elif operation not in ('replace', 'append'):
                raise ValueError('Use replace or append for text and Word documents')
            elif path.suffix.lower() == '.docx':
                from docx import Document
                document = Document(path)
                if operation == 'append':
                    for paragraph in text.split('\n'):
                        document.add_paragraph(paragraph)
                else:
                    paragraphs = list(document.paragraphs)
                    for table in document.tables:
                        for row in table.rows:
                            for cell in row.cells:
                                paragraphs.extend(cell.paragraphs)
                    matches = [p for p in paragraphs if find and find in p.text]
                    if sum(p.text.count(find) for p in matches) != 1:
                        raise ValueError('Replacement text must identify exactly one occurrence in a paragraph')
                    paragraph = matches[0]
                    start, finish = paragraph.text.index(find), paragraph.text.index(find) + len(find)
                    offset, inserted = 0, False
                    for run in paragraph.runs:
                        old = run.text
                        left, right = max(0, start-offset), min(len(old), finish-offset)
                        if right > left:
                            run.text = old[:left] + (text if not inserted else '') + old[right:]
                            inserted = True
                        offset += len(old)
                output = io.BytesIO()
                document.save(output)
                data = output.getvalue()
            else:
                original = path.read_bytes()
                encoding = 'utf-16' if original.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig' if original.startswith(b'\xef\xbb\xbf') else 'utf-8'
                current = original.decode(encoding)
                if operation == 'append':
                    current += text
                else:
                    if not find or current.count(find) != 1:
                        raise ValueError('Replacement text must match exactly one occurrence')
                    current = current.replace(find, text, 1)
                data = current.encode(encoding)
            return self._saved(path, data, expected)

    def move(self, identifier, expected, location, name):
        with self.lock:
            source = self._source(identifier, expected)
            target = self.destination(location, name)
            if source.suffix.lower() != target.suffix.lower():
                raise ValueError('Renaming cannot change a document format')
            if target.exists():
                raise ValueError('A file already exists at the destination')
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            return {'ok': True, 'id': self.files.reference(target), 'path': str(target), 'previous_path': str(source), 'revision': expected}
