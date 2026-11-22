from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.listener import Listener


class StartupListeningTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch('desktop.controller.BreakState', return_value=BreakState(Path(folder.name) / 'break.json')):
            self.controller = Controller(queue.Queue())
        for service in (self.controller.research, self.controller.tasks, self.controller.browser):
            self.addCleanup(service.close)
        for name in ('controller', 'listener'):
            logger = patch('desktop.' + name + '.diagnostic')
            logger.start()
            self.addCleanup(logger.stop)
        self.controller.windows_desktop = 'Default'
        self.listener = Listener(self.controller, {})
        self.controller.listener = self.listener

    def test_voice_warmup_does_not_announce_ready_before_microphone(self):
        with patch('phone.voice.speech_voice'), \
                patch('phone.voice.synthesize_wav', return_value=b'voice'), \
                patch.object(self.controller.apps, 'refresh'):
            self.controller.warmup()
        self.assertTrue(self.controller.voice_ready)
        self.assertFalse(self.controller.microphone_ready)
        self.assertEqual(self.controller.state, 'starting')
        self.assertFalse(any(event[:2] == ('state', 'ready') for event in self.controller.events.queue))

    def test_launch_activation_does_not_announce_ready_before_microphone(self):
        self.listener.activate(source='launch')
        self.assertEqual(self.controller.state, 'starting')
        self.assertFalse(self.controller.microphone_ready)

    def test_first_captured_wake_survives_a_pending_launch_reset(self):
        stale, first_wake = b'\x01\x02' * 1280, b'\x03\x04' * 1280
        self.listener.audio.put(stale)
        self.listener.reset.set()
        general, apps, stream = MagicMock(), MagicMock(), MagicMock()
        general.AcceptWaveform.return_value = True
        general.Result.return_value = '{"text":"jarvis"}'
        apps.FinalResult.return_value = '{}'
        acknowledged = threading.Event()
        stream.__enter__.side_effect = lambda: self.listener.callback(first_wake, 1280, None, None)
        def acknowledge():
            acknowledged.set()
            self.listener.stopped.set()
        with patch('vosk.Model'), patch('vosk.KaldiRecognizer', side_effect=[general, apps]), \
                patch('desktop.listener.configure_endpoint', return_value=2), \
                patch('desktop.listener.app_grammar', return_value='[]'), \
                patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture'}), \
                patch('sounddevice.RawInputStream', return_value=stream), \
                patch.object(self.controller, 'acknowledge_wake', side_effect=acknowledge):
            worker = threading.Thread(target=self.listener.run, daemon=True)
            worker.start()
            try:
                self.assertTrue(acknowledged.wait(1), 'The first captured wake word was discarded')
                general.AcceptWaveform.assert_called_once_with(first_wake)
            finally:
                self.listener.stopped.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
