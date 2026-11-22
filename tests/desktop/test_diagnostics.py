import io
import json
from pathlib import Path
import queue
import tempfile
import time
import unittest
import wave
from unittest.mock import MagicMock, patch

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.diagnostics import EventLog
from desktop.listener import Listener
from desktop.speech import transcribe_pcm


class LogTests(unittest.TestCase):
    def test_rotates_appends_and_excludes_content_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            logger = EventLog(path, max_bytes=450, backups=2)
            try:
                for _ in range(15):
                    logger.write('speech_accepted', heard_chars=42, source='conversation',
                                 transcript='private words', token='secret', audio=b'raw',
                                 headers={'Authorization': 'Bearer secret'})
                logger.close()
                logger.write('reopened', state='ready')
            finally:
                logger.close()
            paths = sorted(Path(directory).glob('events.jsonl*'))
            self.assertEqual(len(paths), 3)
            for file in paths:
                self.assertLessEqual(file.stat().st_size, 450)
                text = file.read_text()
                for private in ('private words', 'secret', 'Authorization', 'transcript', 'audio'):
                    self.assertNotIn(private, text)
                for line in text.splitlines():
                    self.assertIn('time', json.loads(line))
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])['event'], 'reopened')

    def test_unwritable_log_does_not_break_assistant(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = EventLog(Path(directory))
            try:
                logger.write('still_running', state='ready')
            finally:
                logger.close()


class ConversationDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        path = Path(self.temporary.name)
        self.logger = EventLog(path / 'events.jsonl')
        self.addCleanup(self.logger.close)
        for module in ('controller', 'listener', 'speech'):
            patcher = patch('desktop.' + module + '.diagnostic', self.logger.write)
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch('desktop.controller.BreakState', return_value=BreakState(path / 'break.json')):
            self.controller = Controller(queue.Queue())
        self.addCleanup(self.controller.research.close)
        self.controller.windows_desktop = 'default'
        self.listener = Listener(self.controller, {})
        self.controller.listener = self.listener

    def rows(self):
        return [json.loads(line) for line in self.logger.path.read_text().splitlines()]

    def test_plain_greeting_always_requires_wake(self):
        self.assertFalse(self.listener.accepts_speech("what's up dude"))
        self.listener.activate(source='insert', continuous=True)
        self.listener.listening_until = time.monotonic() - 60
        self.assertFalse(self.listener.accepts_speech("what's up dude"))
        self.assertTrue(self.listener.accepts_speech("Jarvis what's up dude"))
        self.assertFalse(self.listener.accepts_speech('what time is it'))
        self.assertTrue(self.listener.accepts_speech('Jarvis what time is it'))
        self.controller.microphone_ready = True
        self.controller.idle()
        self.assertEqual(self.controller.state, 'ready')
        self.listener.mute(True)
        self.assertFalse(self.listener.conversation_active)
        self.assertFalse(self.listener.is_listening())
        self.assertFalse(self.listener.accepts_speech('hello again'))
        rows = self.rows()
        self.assertTrue(any(r.get('reason') == 'wake_word_required' for r in rows))
        self.assertEqual(len([r for r in rows if r['event'] == 'speech_accepted']), 2)
        self.assertNotIn("what's up dude", self.logger.path.read_text())

    def test_manual_activation_does_not_bypass_wake(self):
        self.listener.activate(source='orb')
        self.assertFalse(self.listener.accepts_speech('hello there'))
        self.listener.listening_until = time.monotonic() - 1
        self.assertFalse(self.listener.accepts_speech('hello again'))
        self.assertTrue(self.listener.accepts_speech('hey jarvis what time is it'))

    def test_break_prevents_continuous_activation(self):
        self.controller.breaks.start(60)
        self.listener.activate(source='insert', continuous=True)
        self.assertFalse(self.listener.conversation_active)
        self.assertFalse(self.listener.is_listening())
        self.assertEqual(self.rows()[-1]['reason'], 'break')

    def run_capture(self, transcript=None, failure=None):
        general, app = MagicMock(), MagicMock()
        general.AcceptWaveform.return_value = True
        general.Result.return_value = json.dumps({'text': "jarvis what's up dude"})
        app.FinalResult.return_value = '{}'
        self.listener.activate(source='insert', continuous=True)
        self.listener.reset.clear()
        self.listener.audio.put(b'\x00\x01' * 1280)
        put = self.controller.events.put

        def collect(event):
            put(event)
            if event[0] in ('command', 'speech_error'):
                self.listener.stopped.set()

        with patch('vosk.Model'), patch('vosk.KaldiRecognizer', side_effect=[general, app]), \
             patch('desktop.listener.configure_endpoint', return_value=2), \
             patch('desktop.listener.app_grammar', return_value='[]'), \
             patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture mic'}), \
             patch('sounddevice.RawInputStream'), \
             patch('desktop.listener.transcribe_pcm', return_value=transcript, side_effect=failure), \
             patch.object(self.controller.events, 'put', side_effect=collect):
            self.listener.run()
        return list(self.controller.events.queue)

    def test_greeting_reaches_command_queue_through_capture_and_whisper(self):
        events = self.run_capture(transcript="Jarvis, What's up dude?")
        self.assertIn(('command', "What's up dude?"), events)
        names = [r['event'] for r in self.rows()]
        self.assertIn('microphone_opened', names)
        self.assertIn('speech_accepted', names)
        self.assertIn('transcription_finished', names)
        self.assertIn('voice_command_queued', names)

    def test_failed_whisper_logs_failure_without_dispatching_local_guess(self):
        events = self.run_capture(failure=RuntimeError('connection unavailable'))
        self.assertFalse(any(e[0] == 'command' for e in events))
        self.assertTrue(any(e[0] == 'speech_error' for e in events))
        self.assertIn('transcription_failed', [r['event'] for r in self.rows()])

    def test_groq_error_code_logged_without_provider_payload_or_key(self):
        response = MagicMock(status_code=403, text='private-provider-payload')
        with patch('desktop.speech.groq_key', return_value='private-key'), \
             patch('desktop.speech.requests.post', return_value=response):
            with self.assertRaises(RuntimeError):
                transcribe_pcm(b'\0\0' * 800)
        rows = self.rows()
        self.assertTrue(any(r.get('error_code') == 403 for r in rows))
        self.assertNotIn('private', self.logger.path.read_text())

    def test_response_logs_model_synthesis_and_playback_without_reply_text(self):
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b'\0\0' * 800)
        answer = 'At your service, sir.'
        response = {'model': 'fixture-model', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': answer}]}]}
        with patch('desktop.controller.classify_request', return_value={'action': 'normal'}), \
             patch('desktop.controller.local_request', return_value=response), \
             patch('phone.voice.synthesize_wav', return_value=audio.getvalue()), \
             patch('sounddevice.RawOutputStream') as output:
            result = self.controller.respond("What's up dude?")
        self.assertTrue(result['ok'])
        self.assertTrue(result['spoken'])
        self.assertTrue(output.return_value.__enter__.return_value.write.called)
        rows = self.rows()
        names = [r['event'] for r in rows]
        for name in ('command_started', 'intent_finished', 'model_finished', 'reply_ready', 'synthesis_finished', 'playback_finished', 'command_finished'):
            self.assertIn(name, names)
        done = next(r for r in rows if r['event'] == 'command_finished')
        self.assertTrue(done['spoken'])
        self.assertTrue(done['completed'])
        self.assertNotIn(answer, self.logger.path.read_text())

    def test_audio_failure_does_not_report_successful_playback(self):
        response = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Hello, sir.'}]}]}
        with patch('desktop.controller.classify_request', return_value={'action': 'normal'}), \
             patch('desktop.controller.local_request', return_value=response), \
             patch('phone.voice.synthesize_wav', side_effect=OSError('fixture failure')):
            result = self.controller.respond('hello')
        self.assertFalse(result['spoken'])
        self.assertIn(('finished', False), list(self.controller.events.queue))
        self.assertTrue(any(r['event'] == 'speech_failed' and r['error_type'] == 'OSError' for r in self.rows()))


if __name__ == '__main__':
    unittest.main()
