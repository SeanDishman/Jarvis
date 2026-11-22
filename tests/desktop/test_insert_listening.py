import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.diagnostics import EventLog
from desktop.listener import Listener


class InsertOffStopsListeningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)
        self.logger = EventLog(path / 'events.jsonl')
        self.addCleanup(self.logger.close)
        for module in ('controller', 'listener'):
            patcher = patch('desktop.' + module + '.diagnostic', self.logger.write)
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch('desktop.controller.BreakState', return_value=BreakState(path / 'break.json')):
            self.controller = Controller(queue.Queue())
        self.addCleanup(self.controller.research.close)
        self.controller.windows_desktop = 'default'
        self.controller.microphone_ready = True
        self.listener = Listener(self.controller, {})
        self.controller.listener = self.listener

    def rows(self):
        return [json.loads(line) for line in self.logger.path.read_text().splitlines()]

    def test_hidden_jarvis_hears_nothing_until_shown(self):
        self.listener.activate(source='insert', continuous=True)
        self.assertFalse(self.listener.accepts_speech("what's up dude"))
        self.assertTrue(self.listener.accepts_speech("Jarvis what's up dude"))
        self.listener.hide(True)
        self.assertFalse(self.listener.conversation_active)
        self.assertFalse(self.listener.is_listening())
        self.assertEqual(self.controller.state, 'hidden')
        for heard in ("what's up dude", 'jarvis what time is it', 'jarvis'):
            self.assertFalse(self.listener.accepts_speech(heard), heard)
        self.listener.callback(b'\0\0' * 160, 160, None, None)
        self.assertTrue(self.listener.audio.empty())
        self.listener.activate(8, source='followup')
        self.listener.activate(source='orb', continuous=True)
        self.assertFalse(self.listener.is_listening())
        self.assertEqual([r['reason'] for r in self.rows() if r['event'] == 'listening_not_opened'], ['hidden', 'hidden'])
        self.assertFalse(self.listener.muted, 'Hiding must not change the saved mute setting')

    def test_in_flight_transcript_is_dropped_when_hidden(self):
        def transcribe(*_args, **_kwargs):
            self.listener.hide(True)
            return 'open notepad'
        with patch('desktop.listener.transcribe_pcm', side_effect=transcribe):
            self.assertEqual(self.listener.recognize(b'\0\0' * 100), '')

    def test_showing_again_still_requires_wake_for_each_command(self):
        self.listener.hide(True)
        self.listener.hide(False)
        self.listener.activate(source='insert', continuous=True)
        self.assertFalse(self.listener.conversation_active)
        self.assertFalse(self.listener.accepts_speech('what time is it'))
        self.assertTrue(self.listener.accepts_speech('Jarvis what time is it'))
        self.assertFalse(self.listener.accepts_speech('what about the weather'))

    def test_microphone_stream_is_released_while_hidden(self):
        streams = MagicMock()
        opened = []
        streams.side_effect = lambda **_kwargs: opened.append(time.monotonic()) or MagicMock()
        with patch('vosk.Model'), patch('vosk.KaldiRecognizer'), patch('desktop.listener.app_grammar', return_value='[]'), \
             patch('desktop.listener.configure_endpoint', return_value=2), \
             patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture mic'}), \
             patch('sounddevice.RawInputStream', streams):
            worker = threading.Thread(target=self.listener.run, daemon=True)
            worker.start()
            try:
                self.assertTrue(self.wait(lambda: len(opened) == 1 and self.controller.microphone_ready))
                self.listener.hide(True)
                self.assertTrue(self.wait(lambda: not self.controller.microphone_ready))
                time.sleep(.5)
                self.assertEqual(len(opened), 1, 'The microphone reopened while Jarvis was hidden')
                self.listener.hide(False)
                self.assertTrue(self.wait(lambda: len(opened) == 2 and self.controller.microphone_ready))
            finally:
                self.listener.stopped.set()
                worker.join(3)
        self.assertIn({'event': 'microphone_closed', 'reason': 'hidden'},
                      [{key: r.get(key) for key in ('event', 'reason')} for r in self.rows()])

    @staticmethod
    def wait(condition, seconds=3):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(.02)
        return False


if __name__ == '__main__':
    unittest.main()
