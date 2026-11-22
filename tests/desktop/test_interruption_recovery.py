import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.listener import Listener


def decision(action):
    return {'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps({'action': action})}]}]}


class InterruptionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        with patch('desktop.controller.BreakState', return_value=BreakState(Path(temporary.name) / 'break.json')):
            self.controller = Controller(queue.Queue())
        for service in (self.controller.research, self.controller.tasks, self.controller.browser):
            self.addCleanup(service.close)
        self.controller.windows_desktop = 'default'
        self.controller.microphone_ready = True
        self.listener = Listener(self.controller, {})
        self.controller.listener = self.listener

    def run_audio(self, general, keyword, blocks, recognize):
        audio = iter(blocks)
        model = MagicMock()
        model.vosk_model_find_word.return_value = 0
        failures = []

        def diagnostic(name, **details):
            if name == 'microphone_failed':
                failures.append(details)
                self.listener.stopped.set()

        def next_audio(block=True, timeout=None):
            if block:
                try:
                    return next(audio)
                except StopIteration:
                    self.listener.stopped.set()
            raise queue.Empty

        with patch('vosk.Model', return_value=model), patch('vosk.KaldiRecognizer', side_effect=[general, keyword]), \
                patch('desktop.listener.diagnostic', side_effect=diagnostic), \
                patch('desktop.listener.configure_endpoint', return_value=2), \
                patch('desktop.listener.app_grammar', return_value='[]'), \
                patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture'}), \
                patch('sounddevice.RawInputStream'), \
                patch.object(self.listener.audio, 'get', side_effect=next_audio), \
                patch.object(self.listener, 'recognize', side_effect=recognize) as transcription:
            self.listener.run()
        self.assertEqual(failures, [])
        return transcription

    def test_cleanup_cannot_erase_the_next_requests_thinking_animation(self):
        with self.controller.busy:
            self.controller.update('thinking', 'Answering the follow-up')
            self.controller.idle()
            self.assertEqual(self.controller.state, 'thinking')
            self.assertEqual(self.controller.note, 'Answering the follow-up')
        self.controller.idle()
        self.assertEqual(self.controller.state, 'ready')

    def test_interruption_animation_tracks_listening_transcription_and_resume(self):
        self.controller.spoken_chunk = 'The forecast calls for rain.'
        with self.controller.busy:
            self.controller.update('speaking', 'Speaking')
            context = self.controller.interruption_context()
            self.assertTrue(self.controller.pause_interruption(context))
            self.assertEqual(self.controller.state, 'listening')

            def recognize(*_args, **_kwargs):
                self.assertEqual(self.controller.state, 'thinking')
                return 'Jarvis, do not stop. Keep explaining.'

            with patch.object(self.listener, 'recognize', side_effect=recognize):
                self.listener.handle_interruption('jarvis do not stop', b'audio', context)
            with patch('desktop.controller.local_request', return_value=decision('ignore')):
                self.controller.interrupt('Jarvis, do not stop. Keep explaining.', context)
            self.assertEqual(self.controller.state, 'speaking')
            self.assertFalse(self.controller.speech_paused.is_set())
            self.assertFalse(self.controller.cancelled.is_set())

    def test_stop_keeps_microphone_armed_for_the_next_wake(self):
        self.controller.busy.acquire()
        self.controller.update('speaking', 'Speaking')
        context = self.controller.interruption_context()
        self.controller.pause_interruption(context)
        with patch('desktop.controller.local_request', return_value=decision('stop')):
            self.controller.interrupt('Jarvis, shut up.', context)
        self.controller.release_response()
        self.assertEqual(self.controller.state, 'ready')
        self.assertFalse(self.listener.muted)
        self.assertFalse(self.listener.hidden)
        self.assertFalse(self.controller.speech_paused.is_set())
        self.assertTrue(self.listener.accepts_speech('Jarvis, what time is it?'))
        self.assertFalse(self.listener.accepts_speech('unrelated conversation'))

    def test_playback_before_the_wake_is_not_sent_as_the_owners_request(self):
        general, keyword = MagicMock(), MagicMock()
        frame = {'count': 0}
        echo = 'The forecast calls for rain.'
        request = 'Jarvis, shut up.'
        self.controller.spoken_chunk = echo

        def waveform(_pcm):
            frame['count'] += 1
            return frame['count'] == 102

        general.AcceptWaveform.side_effect = waveform
        general.PartialResult.side_effect = lambda: json.dumps({'partial': echo if frame['count'] < 100 else request})
        general.Result.return_value = json.dumps({'text': echo + ' ' + request})
        keyword.AcceptWaveform.return_value = False
        keyword.PartialResult.side_effect = lambda: json.dumps({'partial': echo if frame['count'] < 100 else 'jarvis'})
        keyword.FinalResult.return_value = '{}'
        with self.controller.busy:
            transcription = self.run_audio(general, keyword, [b'\1\0' * 1280] * 102,
                                           lambda *_args, **_kwargs: echo + ' ' + request)
        self.assertEqual(transcription.call_count, 1)
        self.assertLessEqual(len(transcription.call_args.args[0]), int(2.3 * 32000))
        interruptions = [row for row in self.controller.events.queue if row[0] == 'interruption']
        self.assertEqual([row[1] for row in interruptions], [request])

    def check_capture_boundary(self, replacement):
        general, keyword = MagicMock(), MagicMock()
        capture = {'count': 0, 'echo': False}
        first, second = b'\1\0' * 1280, b'\2\0' * 1280
        self.controller.busy.acquire()

        def waveform(_pcm):
            capture['count'] += 1
            if capture['count'] == 1:
                capture['echo'] = True
                self.controller.cancelled.set()
                if replacement:
                    self.controller.cancelled = threading.Event()
                else:
                    self.controller.busy.release()
                return False
            return True

        general.Reset.side_effect = lambda: capture.update(echo=False)
        general.AcceptWaveform.side_effect = waveform
        general.PartialResult.return_value = '{"partial":"the old answer"}'
        general.Result.side_effect = lambda: json.dumps({'text': ('the old answer ' if capture['echo'] else '') + 'jarvis what time is it'})
        keyword.AcceptWaveform.return_value = False
        keyword.PartialResult.return_value = '{}'
        keyword.FinalResult.return_value = '{}'
        try:
            transcription = self.run_audio(general, keyword, [first, second],
                                           lambda *_args, **_kwargs: 'Jarvis, what time is it?')
        finally:
            if self.controller.busy.locked():
                self.controller.busy.release()
        self.assertEqual(transcription.call_count, 1)
        self.assertEqual(transcription.call_args.args[0], second)
        expected = ('interruption', 'Jarvis, what time is it?') if replacement else ('command', 'what time is it?')
        self.assertIn(expected, [row[:2] for row in self.controller.events.queue])

    def test_next_wake_after_stop_does_not_inherit_the_old_playback(self):
        self.check_capture_boundary(False)

    def test_new_answer_resets_capture_even_without_an_observed_idle_gap(self):
        self.check_capture_boundary(True)


if __name__ == '__main__':
    unittest.main()
