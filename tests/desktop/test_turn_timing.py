from collections import deque
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.listener import Listener
from desktop.speech import pcm_wav
from desktop.turn_timing import configure_endpoint, contains_speech, pause_seconds


class TurnTimingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        with patch('desktop.controller.BreakState', return_value=BreakState(self.folder / 'break.json')):
            self.controller = Controller(queue.Queue())
        self.addCleanup(self.controller.research.close)
        self.addCleanup(self.controller.tasks.close)
        self.addCleanup(self.controller.browser.close)
        self.controller.windows_desktop = 'default'
        self.listener = Listener(self.controller, {})

    def test_configure_vosk_preserves_decoder_options_and_waits_through_pauses(self):
        config = self.folder / 'conf/model.conf'
        config.parent.mkdir()
        original = '--beam=10.0\n--endpoint.rule2.min-trailing-silence=0.5\n'
        config.write_text(original)
        self.assertEqual(configure_endpoint(self.folder, {}), 2.0)
        updated = config.read_text()
        self.assertIn('--beam=10.0', updated)
        self.assertIn('--endpoint.rule2.min-trailing-silence=2', updated)
        self.assertIn('--endpoint.rule3.min-trailing-silence=2.5', updated)
        self.assertIn('--endpoint.rule4.min-trailing-silence=3', updated)
        self.assertIn('--endpoint.rule5.min-utterance-length=60', updated)
        self.assertEqual(config.with_suffix('.conf.original').read_text(), original)
        modified = config.stat().st_mtime_ns
        configure_endpoint(self.folder, {})
        self.assertEqual(config.stat().st_mtime_ns, modified)
        configure_endpoint(self.folder, {'speechPauseSeconds': 3})
        self.assertIn('--endpoint.rule2.min-trailing-silence=3', config.read_text())
        self.assertEqual(config.with_suffix('.conf.original').read_text(), original)

    def test_invalid_pause_settings_use_a_safe_conversation_default(self):
        for value in (None, 'fast', float('nan'), float('inf'), 0, .1, 20):
            self.assertEqual(pause_seconds({'speechPauseSeconds': value}), 2.0)
        self.assertEqual(pause_seconds({'speechPauseSeconds': 1.5}), 1.5)

    def test_capture_continues_during_transcription_without_a_destructive_reset(self):
        captured = b'\1\2' * 1280
        def transcribe(*_args, **_kwargs):
            self.assertTrue(self.listener.transcribing)
            self.listener.callback(captured, 1280, None, None)
            return 'The first part'
        with patch('desktop.listener.transcribe_pcm', side_effect=transcribe):
            self.assertEqual(self.listener.recognize(b'\0\0' * 1280), 'The first part')
        self.assertEqual(self.listener.audio.get_nowait(), captured)
        self.assertFalse(self.listener.reset.is_set())

    def test_a_continued_sentence_is_combined_before_any_command_is_dispatched(self):
        first, rest = b'\1\2' * 1280, b'\3\4' * 1280
        general, app = MagicMock(), MagicMock()
        general.AcceptWaveform.return_value = True
        general.Result.side_effect = [json.dumps({'text': text}) for text in ('jarvis open', 'notepad', 'notepad')]
        app.FinalResult.return_value = '{}'
        self.listener.audio.put(first)
        recordings = []
        def transcribe(pcm, *_args, **_kwargs):
            recordings.append(pcm)
            if len(recordings) == 1:
                self.listener.callback(rest, 1280, None, None)
                return 'Jarvis Open'
            return 'Jarvis Open Notepad'
        put = self.controller.events.put
        def event(row):
            put(row)
            if row[0] == 'command':
                self.listener.stopped.set()
        with patch('vosk.Model'), patch('vosk.KaldiRecognizer', side_effect=[general, app]), \
             patch('desktop.listener.configure_endpoint', return_value=2), \
             patch('desktop.listener.app_grammar', return_value='[]'), \
             patch('desktop.listener.contains_speech', return_value=True), \
             patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture'}), \
             patch('sounddevice.RawInputStream'), \
             patch('desktop.listener.transcribe_pcm', side_effect=transcribe), \
             patch.object(self.controller.events, 'put', side_effect=event):
            self.listener.run()
        commands = [row for row in self.controller.events.queue if row[0] == 'command']
        self.assertEqual(commands, [('command', 'Open Notepad')])
        self.assertEqual(recordings, [first, first + rest])

    def test_pending_audio_stays_in_order_and_silence_does_not_extend_a_turn(self):
        first, second = b'\1\2' * 1280, b'\3\4' * 1280
        pending = deque([first])
        self.listener.audio.put(second)
        recognizer = MagicMock()
        recognizer.AcceptWaveform.return_value = False
        recognizer.PartialResult.return_value = '{"partial":"notepad"}'
        with patch('desktop.listener.contains_speech', return_value=True) as detector:
            self.assertTrue(self.listener.continuation_waiting(pending, 16000, None, recognizer))
            detector.assert_called_once_with(first + second)
        self.assertEqual(list(pending), [first, second])
        pending = deque([b'\0\0' * 8000])
        self.assertFalse(self.listener.continuation_waiting(pending, 16000, None, recognizer))
        self.assertFalse(pending)
        self.assertFalse(contains_speech(b'\0\0' * 16000))

    def test_noise_during_transcription_does_not_reopen_listening_or_repeat_the_request(self):
        first, noise = b'\1\2' * 1280, b'\3\4' * 20480
        general, app = MagicMock(), MagicMock()
        general.AcceptWaveform.return_value = True
        general.Result.side_effect = ['{"text":"jarvis what time is it"}', '{}']
        general.FinalResult.return_value = '{}'
        app.FinalResult.return_value = '{}'
        self.listener.audio.put(first)
        failures = []

        def diagnostic(name, **details):
            if name == 'microphone_failed':
                failures.append(details)
                self.listener.stopped.set()

        def transcribe(*_args, **_kwargs):
            self.listener.callback(noise, 20480, None, None)
            return 'Jarvis, what time is it?'

        put = self.controller.events.put

        def event(row):
            put(row)
            if row[0] == 'command':
                self.listener.stopped.set()

        with patch('vosk.Model'), patch('vosk.KaldiRecognizer', side_effect=[general, app]), \
                patch('desktop.listener.diagnostic', side_effect=diagnostic), \
                patch('desktop.listener.configure_endpoint', return_value=2), \
                patch('desktop.listener.app_grammar', return_value='[]'), \
                patch('desktop.listener.contains_speech', return_value=True), \
                patch('desktop.listener.CONTINUATION_CONFIRM_SECONDS', 0), \
                patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture'}), \
                patch('sounddevice.RawInputStream'), \
                patch('desktop.listener.transcribe_pcm', side_effect=transcribe) as transcription, \
                patch.object(self.controller.events, 'put', side_effect=event):
            self.listener.run()
        self.assertEqual(failures, [])
        transcription.assert_called_once()
        events = list(self.controller.events.queue)
        self.assertEqual([row for row in events if row[0] == 'command'], [('command', 'what time is it?')])
        states = [row[1] for row in events if row[0] == 'state']
        self.assertEqual(states[states.index('thinking'):], ['thinking'])

    def test_continuation_waits_for_the_rest_of_a_word_without_losing_its_onset(self):
        onset, rest = b'\1\2' * 1280, b'\3\4' * 1280
        pending = deque([onset])
        recognizer = MagicMock()
        recognizer.AcceptWaveform.return_value = False
        calls = []

        def partial():
            calls.append(True)
            if len(calls) == 1:
                self.listener.callback(rest, 1280, None, None)
                return '{}'
            return '{"partial":"notepad"}'

        recognizer.PartialResult.side_effect = partial
        with patch('desktop.listener.contains_speech', return_value=True):
            self.assertTrue(self.listener.continuation_waiting(pending, 16000, None, recognizer))
        self.assertEqual(list(pending), [onset, rest])
        self.assertEqual([call.args[0] for call in recognizer.AcceptWaveform.call_args_list], [onset, rest])

    def test_unknown_noise_is_not_treated_as_spoken_words(self):
        pending = deque([b'\1\2' * 1280])
        recognizer = MagicMock()
        recognizer.AcceptWaveform.return_value = False
        recognizer.PartialResult.return_value = '{"partial":"[unk]"}'
        recognizer.FinalResult.return_value = '{"text":"[unk]"}'
        with patch('desktop.listener.contains_speech', return_value=True), \
                patch('desktop.listener.CONTINUATION_CONFIRM_SECONDS', 0):
            self.assertFalse(self.listener.continuation_waiting(pending, 16000, None, recognizer))
        self.assertFalse(pending)

    def test_muting_during_continuation_confirmation_prevents_dispatch(self):
        pending = deque([b'\1\2' * 1280])
        recognizer = MagicMock()
        recognizer.AcceptWaveform.return_value = False

        def partial():
            self.listener.mute(True)
            return '{}'

        recognizer.PartialResult.side_effect = partial
        with patch('desktop.listener.contains_speech', return_value=True):
            self.assertFalse(self.listener.continuation_waiting(pending, 16000, None, recognizer))
        self.assertTrue(self.listener.muted)
        self.assertEqual(self.controller.state, 'muted')
        self.assertFalse(any(row[0] == 'command' for row in self.controller.events.queue))

    def test_longer_requests_are_accepted_but_audio_is_still_bounded(self):
        self.assertTrue(pcm_wav(b'\0\0' * (16000 * 40)))
        with self.assertRaises(ValueError):
            pcm_wav(b'\0\0' * (16000 * 61))

    def test_one_character_fragment_does_not_trigger_a_reply(self):
        self.listener.activate(continuous=True)
        self.assertFalse(self.listener.accepts_speech('h'))
        self.assertFalse(self.listener.accepts_speech('hi'))
        self.assertTrue(self.listener.accepts_speech('Jarvis hi'))


if __name__ == '__main__':
    unittest.main()
