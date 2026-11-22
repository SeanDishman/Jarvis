import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path
import threading
import time
import uuid

from desktop.storage import local_path

TEMPORARY = {'.part','.partial','.crdownload','.download','.tmp','.temp'}


def battery_status():
    class Power(ctypes.Structure):
        _fields_ = [('ac',wintypes.BYTE),('flags',wintypes.BYTE),('percent',wintypes.BYTE),('reserved',wintypes.BYTE),
                    ('life',wintypes.DWORD),('full',wintypes.DWORD)]
    status = Power()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)) or status.percent == 255 or status.flags & 128:
        return None
    return {'percent':int(status.percent),'charging':status.ac == 1}


class Monitor:
    def __init__(self, preferences, events, clock=time.monotonic, battery=battery_status):
        self.preferences, self.events, self.clock, self.battery = preferences, events, clock, battery
        self.lock = threading.RLock()
        self.baselines = {}
        self.pending = {}
        self.alerts = []
        self.last_battery_alert = None
        self.next_check = 0
        self.last_error = ''

    def _alert(self, kind, text, path=None):
        row = {'id':uuid.uuid4().hex,'kind':kind,'text':text,'path':path,
               'time':datetime.now().astimezone().isoformat(timespec='seconds'),'dismissed':False}
        self.alerts.append(row)
        self.alerts = self.alerts[-100:]
        self.events.put(('monitor_alert',row.copy()))

    def poll(self):
        with self.lock:
            if self.clock() < self.next_check:
                return
            self.next_check = self.clock() + 15
            settings = self.preferences.read()
            if not settings.get('monitorEnabled',True):
                self.baselines.clear()
                self.pending.clear()
                return
            self.last_error = ''
            folders = settings.get('monitorFolders',[])
            for old in list(self.baselines):
                if old not in folders:
                    self.baselines.pop(old,None)
                    self.pending.pop(old,None)
            for folder in folders:
                try:
                    root = local_path(folder,[folder],existing=True)
                    current = {}
                    with os.scandir(root) as entries:
                        for index, entry in enumerate(entries):
                            if index >= 5000:
                                self.last_error = 'A watched folder exceeds the 5000-entry monitoring limit.'
                                break
                            path = Path(entry.path)
                            if entry.is_file(follow_symlinks=False) and not entry.name.startswith('.') and path.suffix.lower() not in TEMPORARY:
                                local_path(path,[root],existing=True)
                                info = entry.stat(follow_symlinks=False)
                                current[entry.name] = (info.st_size,info.st_mtime_ns)
                    if folder not in self.baselines:
                        self.baselines[folder] = current
                        self.pending[folder] = {}
                        continue
                    previous = self.baselines[folder]
                    pending = self.pending[folder]
                    for name, stamp in current.items():
                        if name in pending and pending[name]['stamp'] == stamp:
                            kind = pending.pop(name)['kind']
                            self._alert(kind,f'{"New file ready" if kind == "new_file" else "File changed"}: {name}',str(root/name))
                        elif previous.get(name) != stamp:
                            pending[name] = {'stamp':stamp,'kind':pending.get(name,{}).get('kind','new_file' if name not in previous else 'file_changed')}
                    for gone in set(pending)-set(current):
                        pending.pop(gone,None)
                    self.baselines[folder] = current
                except (OSError,ValueError):
                    self.last_error = 'A watched folder is unavailable. Check monitoring settings.'
            if settings.get('monitorBattery',True):
                battery = self.battery()
                if battery:
                    level = 10 if battery['percent'] <= 10 else 20 if battery['percent'] <= 20 else None
                    if battery['charging'] or level is None:
                        self.last_battery_alert = None
                    elif level != self.last_battery_alert:
                        self._alert('battery',f'Battery is at {battery["percent"]} percent. Connect your charger.')
                        self.last_battery_alert = level

    def status(self):
        with self.lock:
            settings = self.preferences.read()
            return {'ok':True,'enabled':settings.get('monitorEnabled',True),'folders':settings.get('monitorFolders',[]),
                    'battery':settings.get('monitorBattery',True),'alerts':[row.copy() for row in self.alerts if not row['dismissed']],
                    'warning':self.last_error,'scope':'Top-level files in selected folders and battery only. No screen, inbox or notification-content monitoring.'}

    def dismiss(self, identifier=''):
        with self.lock:
            for row in self.alerts:
                if not identifier or row['id'] == identifier:
                    row['dismissed'] = True
            return {'ok':True,'message':'Dismissed the selected alerts.'}
