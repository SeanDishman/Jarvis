import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import quote

from desktop.common import ROOT


def open_vault(vault):
    vault = Path(vault).resolve()
    executable = ROOT / '.runtime/obsidian/Obsidian.exe'
    if not executable.is_file():
        try:
            os.startfile('obsidian://open?path='+quote(str(vault)))
        except OSError:
            os.startfile(vault)
        return
    profile = ROOT / '.openclaw/obsidian-profile'
    profile.mkdir(parents=True,exist_ok=True)
    config_path = profile / 'obsidian.json'
    configuration = json.loads(config_path.read_text(encoding='utf-8')) if config_path.exists() else {}
    identifier = hashlib.sha256(str(vault).casefold().encode()).hexdigest()[:16]
    vaults = configuration.setdefault('vaults',{})
    for entry in vaults.values():
        entry['open'] = False
    vaults[identifier] = {'path':str(vault),'ts':round(time.time()*1000),'open':True}
    temporary = config_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(configuration),encoding='utf-8')
    temporary.replace(config_path)
    subprocess.Popen([str(executable),'--user-data-dir='+str(profile),'obsidian://open?vault='+identifier],
                     cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
