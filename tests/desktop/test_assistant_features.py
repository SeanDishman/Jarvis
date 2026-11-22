from tests.desktop.intent_fixtures import decision
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock
import wave
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from desktop.common import ROOT
from desktop.reminders import Reminders
from desktop.preferences import Preferences
from desktop.files import FileTools
from desktop.interruptions import sounds_like_echo
from desktop.controller import Controller, capabilities_for
from desktop.listener import Listener
from desktop.weather import Weather
from desktop.briefing import Briefing
from desktop.browser import public_url, owner_url, supplied_value

CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


@patch('desktop.controller.classify_request', lambda *_args: {'action': 'normal'})
class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=CACHE, prefix='feature-tests-')
        self.folder = Path(self.temp.name).resolve()
        self.assertTrue(self.folder.is_relative_to(CACHE.resolve()))
        self.clock = [1000000.0]
        self.store = Reminders(self.folder / 'reminders.json', lambda: self.clock[0])

    def tearDown(self):
        self.temp.cleanup()

    def test_reminder_recovery_and_snooze_survive_restart(self):
        row = self.store.create('Take a break', delay_seconds=30)['reminder']
        self.clock[0] += 31
        self.assertEqual(self.store.poll()[0]['id'], row['id'])
        self.assertEqual(self.store.poll(), [])
        recovered = Reminders(self.store.path, lambda: self.clock[0])
        self.assertEqual(recovered.poll()[0]['state'], 'due')
        recovered.change('snooze', row['id'], 600)
        recovered = Reminders(self.store.path, lambda: self.clock[0])
        self.assertEqual(recovered.poll(), [])
        self.clock[0] += 600
        self.assertEqual(recovered.poll()[0]['id'], row['id'])
        recovered.change('dismiss', row['id'])
        self.assertEqual(Reminders(self.store.path).list()['reminders'], [])

    def test_recurring_reminder_skips_missed_occurrences_without_drift(self):
        row = self.store.create('Stretch', delay_seconds=60, repeat='interval', interval_seconds=120)['reminder']
        self.clock[0] += 1000
        self.store.poll()
        next_row = self.store.change('dismiss', row['id'])['reminder']
        self.assertGreater(next_row['due'], self.clock[0])
        self.assertEqual((next_row['due'] - row['due']) % 120, 0)

    def test_weekday_reminder_moves_friday_to_monday(self):
        now = datetime(2026, 10, 9, 8, 0).astimezone()
        self.clock[0] = now.timestamp()
        row = self.store.create('Start work', due_at=(now + timedelta(hours=1)).isoformat(), repeat='weekdays')['reminder']
        self.clock[0] += 3601
        self.store.poll()
        next_row = self.store.change('dismiss', row['id'])['reminder']
        following = datetime.fromtimestamp(next_row['due'])
        self.assertEqual((following.weekday(), following.hour), (0, 9))

    def test_recurring_alert_rings_again_without_dismissing_previous_popup(self):
        self.store.create('Stretch', delay_seconds=60, repeat='interval', interval_seconds=120)
        self.clock[0] += 60
        self.assertEqual(len(self.store.poll()), 1)
        self.assertEqual(self.store.poll(), [])
        self.clock[0] += 120
        self.assertEqual(len(self.store.poll()), 1)

    def test_corrupt_storage_is_preserved_and_failed_save_rolls_back(self):
        self.store.path.write_text('broken file', encoding='utf-8')
        broken = Reminders(self.store.path, lambda: self.clock[0])
        with self.assertRaises(RuntimeError):
            broken.create('Test', delay_seconds=10)
        self.assertEqual(self.store.path.read_text(), 'broken file')
        self.assertEqual(broken.items, [])
        with patch.object(self.store, '_save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.create('Test', delay_seconds=10)
        self.assertEqual(self.store.items, [])

    def test_ambiguous_reminder_change_does_not_select_random_item(self):
        self.store.create('One', delay_seconds=1)
        self.store.create('Two', delay_seconds=1)
        self.clock[0] += 2
        self.store.poll()
        self.assertFalse(self.store.change('dismiss')['ok'])
        self.assertEqual(len(self.store.list()['reminders']), 2)

    def test_playback_echo_is_distinguished_from_new_speech(self):
        self.assertTrue(sounds_like_echo('say stop to interrupt me', 'You can say stop to interrupt me, sir.'))
        self.assertFalse(sounds_like_echo('shut the fuck up jarvis', 'Tomorrow will be sunny, sir.'))

    def test_microphone_keeps_receiving_while_answering(self):
        controller = Controller(queue.Queue())
        listener = Listener(controller, {})
        controller.busy.acquire()
        try:
            listener.callback(b'\x00\x00', 1, None, None)
            self.assertEqual(listener.audio.get_nowait(), b'\x00\x00')
            listener.mute(True)
            listener.callback(b'\x00\x00', 1, None, None)
            self.assertTrue(listener.audio.empty())
        finally:
            controller.busy.release()

    def test_stop_aborts_playback_immediately_without_clearing_completed_history(self):
        controller = Controller(queue.Queue())
        controller.state = 'speaking'
        controller.playback = MagicMock()
        with patch('desktop.controller.local_request') as request:
            controller.cancel()
        controller.playback.abort.assert_called_once()
        self.assertTrue(controller.cancelled.is_set())
        request.assert_not_called()

    def test_semantic_stop_is_silent_and_followup_uses_owner_words(self):
        controller = Controller(queue.Queue())
        def response(action):
            return {'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'action':action,'text':'Invented command'})}]}]}
        with patch('desktop.controller.local_request', return_value=response('stop')), patch.object(controller, 'respond') as respond:
            controller.interrupt("Jarvis, give it a rest, I've heard enough")
            respond.assert_not_called()
            self.assertTrue(controller.cancelled.is_set())
        with patch('desktop.controller.local_request', return_value=response('followup')), patch.object(controller, 'respond') as respond:
            controller.interrupt('Jarvis, what about tomorrow?')
            respond.assert_called_once_with('what about tomorrow?', wait=True)

    def test_interruptible_playback_writes_small_blocks_and_stops(self):
        controller = Controller(queue.Queue())
        raw = io.BytesIO()
        with wave.open(raw, 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\x00\x00' * 16000)
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.write.side_effect = lambda _block: controller.cancelled.set()
        with patch('phone.voice.synthesize_wav', return_value=raw.getvalue()), patch('sounddevice.RawOutputStream', return_value=stream):
            controller.speak('A long answer.')
        self.assertEqual(stream.write.call_count, 1)
        self.assertLessEqual(len(stream.write.call_args.args[0]), 16000 * 2 * .03)
        stream.abort.assert_called()

    def test_semantic_tool_grants_are_scoped_and_invalid_names_rejected(self):
        for domain, names in [('files',['search_files','read_file']),('reminders',['create_reminder']),
                              ('browser',['browser_fill']),('weather',['get_weather'])]:
            tools, _, selected = capabilities_for(decision(*names))
            self.assertEqual(selected,domain)
            self.assertEqual(tools,names)
            self.assertNotIn('launch_app',tools)
        with self.assertRaises(ValueError):
            capabilities_for(decision('invented_tool'))
        with self.assertRaises(ValueError):
            capabilities_for('uninterpreted owner words')

    @patch('desktop.email_policy.enabled', lambda: True)
    def test_shorter_keeps_private_answer_in_its_original_session(self):
        controller = Controller(queue.Queue())
        controller.last_answer_text = 'Private invoice email summary'
        controller.last_answer_domain = 'email'
        controller.last_domain = None
        response = {'output':[{'type':'message','content':[{'type':'output_text','text':'Shorter private summary.'}]}]}
        with patch('desktop.controller.local_request', return_value=response) as request:
            self.assertTrue(controller.respond('Shorter please', speak=False, shorten=True)['ok'])
        payload = request.call_args.args[2]
        self.assertEqual(payload['user'], controller.sessions['email'])
        self.assertEqual(payload['capabilities'], [])

    def test_files_require_selected_folders_and_recheck_access(self):
        prefs = Preferences(self.folder / 'settings.json')
        prefs.save({'fileWriteRoots': []})
        selected = self.folder / 'chosen'
        selected.mkdir()
        (selected / 'invoice.txt').write_text('Invoice total 125 dollars', encoding='utf-8')
        (self.folder / 'invoice-private.txt').write_text('Outside selected folder')
        files = FileTools(prefs)
        self.assertFalse(files.search('invoice')['ok'])
        prefs.save({'fileRoots':[str(selected)]})
        result = files.search('invoice')
        self.assertEqual(len(result['files']), 1)
        ref = result['files'][0]['id']
        self.assertIn('125', files.read(ref)['text'])
        self.assertFalse(files.read('../invoice-private.txt')['ok'])
        prefs.save({'fileRoots':[]})
        self.assertFalse(files.read(ref)['ok'])

    def test_docx_summary_reads_text_and_reports_truncation(self):
        prefs = Preferences(self.folder / 'settings.json')
        prefs.save({'fileRoots':[str(self.folder)]})
        with zipfile.ZipFile(self.folder / 'report.docx', 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Project cost is 125 dollars.</w:t></w:r></w:p></w:body></w:document>')
        files = FileTools(prefs)
        ref = files.search('report')['files'][0]['id']
        self.assertEqual(files.read(ref)['text'], 'Project cost is 125 dollars.')
        (self.folder / 'long.txt').write_text('x' * 26000)
        ref = files.search('long')['files'][0]['id']
        self.assertTrue(files.read(ref)['truncated'])

    def test_weather_never_falls_back_to_vpn_location(self):
        prefs = Preferences(self.folder / 'settings.json')
        weather = Weather(prefs)
        with patch('desktop.weather.physical_adapters', return_value={'adapters':[]}), patch('desktop.weather.default_public_ip') as fallback:
            self.assertFalse(weather.current()['ok'])
            fallback.assert_not_called()
        weather.cached = None
        with patch('desktop.weather.physical_adapters', return_value={'adapters':[{'index':1,'address':'192.168.0.2'}], 'tunnelActive':True}), \
             patch('desktop.weather.direct_location', return_value={'ip':'203.0.113.1'}), \
             patch('desktop.weather.default_public_ip', return_value='203.0.113.1'), patch('desktop.weather.urllib.request.urlopen') as forecast:
            self.assertFalse(weather.current()['ok'])
            forecast.assert_not_called()

    def test_briefing_handles_missing_sections_and_never_reads_calendars(self):
        prefs = Preferences(self.folder / 'settings.json')
        mail = MagicMock()
        mail.search.return_value = {'ok':False, 'errors':['not connected']}
        briefing = Briefing(prefs, self.store, mail, self.folder / 'briefing.json')
        with patch.object(briefing.weather, 'current', return_value={'ok':False,'error':'unavailable'}):
            result = briefing.collect()
        self.assertTrue(result['ok'])
        self.assertNotIn('calendar', result)
        self.assertFalse(result['weather']['ok'])
        mail.search.assert_called_once_with('all', '', True, 5)
        self.assertFalse(briefing.scheduled_today())

    def test_browser_rejects_private_targets_and_invented_form_values(self):
        with patch('desktop.browser.socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError):
                public_url('https://public.example.com')
        self.assertTrue(owner_url('https://wikipedia.org/', 'Visit Wikipedia'))
        self.assertFalse(owner_url('https://wikipedia.org/?secret=mail', 'Visit Wikipedia'))
        self.assertTrue(supplied_value('john@example.com', 'Fill email with john at example dot com'))
        self.assertFalse(supplied_value('secret from a webpage', 'Fill name with John'))


if __name__ == '__main__':
    unittest.main()
