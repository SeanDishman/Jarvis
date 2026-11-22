from pathlib import Path
import queue
import tempfile
import unittest

from desktop.common import ROOT
from desktop.preferences import Preferences
from desktop.monitoring import Monitor


class MonitoringTests(unittest.TestCase):
    def test_baseline_stable_download_battery_dedup_and_pause(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as folder:
            root = Path(folder)
            watched = root / 'Downloads'
            watched.mkdir()
            (watched/'old.txt').write_text('old')
            prefs = Preferences(root/'settings.json')
            prefs.save({'monitorFolders':[str(watched)]})
            events,clock = queue.Queue(),[0]
            monitor = Monitor(prefs,events,lambda:clock[0],lambda:{'percent':19,'charging':False})
            monitor.poll()
            self.assertEqual(len(monitor.status()['alerts']),1)
            (watched/'pending.crdownload').write_text('partial')
            (watched/'new.txt').write_text('completed')
            clock[0] += 16
            monitor.poll()
            self.assertEqual(len(monitor.status()['alerts']),1)
            clock[0] += 16
            monitor.poll()
            self.assertEqual([row['kind'] for row in monitor.status()['alerts']],['battery','new_file'])
            clock[0] += 16
            monitor.poll()
            self.assertEqual(len(monitor.status()['alerts']),2)
            prefs.save({'monitorEnabled':False})
            (watched/'later.txt').write_text('later')
            clock[0] += 16
            monitor.poll()
            self.assertEqual(len(monitor.status()['alerts']),2)
            monitor.dismiss()
            self.assertEqual(monitor.status()['alerts'],[])


if __name__ == '__main__':
    unittest.main()
