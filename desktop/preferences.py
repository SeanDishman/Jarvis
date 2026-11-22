import copy
import json
from pathlib import Path
import re
import threading
import uuid

from desktop.common import ROOT

DEFAULTS = {'fileRoots': [], 'briefingTime': '', 'weatherUnits': 'fahrenheit',
            'memoryVault': str(ROOT / 'Jarvis Brain'),
            'fileWriteRoots': [str(ROOT / 'Jarvis Files')],
            'monitorFolders': [str(Path.home() / 'Downloads'), str(ROOT / 'Jarvis Files')],
            'lockInMode': True,
            'monitorEnabled': True, 'monitorBattery': True, 'offlineSpeech': 'auto'}


class Preferences:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / '.openclaw/assistant-settings.json')
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            value = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            return {**copy.deepcopy(DEFAULTS), **value}

    def save(self, updates):
        with self.lock:
            data = self.read()
            data.update({k: v for k, v in updates.items() if k in DEFAULTS})
            if not isinstance(data['fileRoots'], list) or len(data['fileRoots']) > 12:
                raise ValueError('Choose up to twelve search folders')
            roots = []
            for raw in data['fileRoots']:
                path = Path(raw).resolve(strict=True)
                if not path.is_dir() or path == Path(path.anchor) or str(path).startswith('\\\\'):
                    raise ValueError('Choose a local folder, not an entire drive or network share')
                if path not in roots:
                    roots.append(path)
            data['fileRoots'] = [str(r) for r in roots]
            from desktop.storage import local_path
            for key in ('fileWriteRoots', 'monitorFolders'):
                if not isinstance(data[key], list) or len(data[key]) > 12:
                    raise ValueError('Choose up to twelve folders')
                checked = []
                for raw in data[key]:
                    path = Path(raw).absolute()
                    if path == Path(path.anchor) or (path.exists() and not path.is_dir()):
                        raise ValueError('Choose a local folder, not an entire drive')
                    checked.append(str(local_path(path, [path])))
                data[key] = list(dict.fromkeys(checked))
            if not isinstance(data['memoryVault'],str) or not data['memoryVault'].strip():
                raise ValueError('Choose an Obsidian vault folder')
            vault = Path(data['memoryVault']).absolute()
            if vault == Path(vault.anchor) or (vault.exists() and not vault.is_dir()):
                raise ValueError('Choose an Obsidian vault folder')
            data['memoryVault'] = str(local_path(vault, [vault]))
            if data['offlineSpeech'] not in ('auto', 'offline', 'online'):
                raise ValueError('Choose automatic, offline or online speech')
            for key in ('monitorEnabled', 'monitorBattery'):
                if type(data[key]) is not bool:
                    raise ValueError('Monitoring settings must be enabled or disabled')
            if type(data['lockInMode']) is not bool:
                raise ValueError('Lock in mode must be on or off')
            if data['briefingTime'] and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', data['briefingTime']):
                raise ValueError('Use a briefing time such as 08:00, or leave it blank')
            if data['weatherUnits'] not in ('fahrenheit', 'celsius'):
                raise ValueError('Choose Fahrenheit or Celsius')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return data
