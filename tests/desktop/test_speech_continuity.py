import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import wave

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.listener import Listener


def wav_bytes(value=1):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(bytes([value, 0]) * 800)
    return buffer.getvalue()


class SpeechContinuityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch('desktop.controller.BreakState', return_value=BreakState(Path(folder.name) / 'break.json')):
            self.controller = Controller(queue.Queue())
        for service in (self.controller.research, self.controller.tasks, self.controller.browser):
            self.addCleanup(service.close)
        for module in ('controller', 'listener'):
            logger = patch('desktop.' + module + '.diagnostic')
            logger.start()
            self.addCleanup(logger.stop)

    def test_next_audio_is_prepared_during_playback_in_one_stream(self):
        playing, prepared = threading.Event(), threading.Event()
        synthesized, writes = [], []
        chunks = ['First sentence. ' * 13, 'Second sentence. ' * 13, 'Final sentence.']
        text = ''.join(chunks)

        def synthesize(chunk):
            synthesized.append(chunk)
            index = len(synthesized)
            if index == 2:
                self.assertTrue(playing.wait(1), 'Synthesis delayed the start of playback')
                prepared.set()
            return wav_bytes(index)

        def write(block):
            writes.append(block)
            if len(writes) == 1:
                playing.set()
                self.assertTrue(prepared.wait(1), 'The next audio was not prepared during playback')

        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.write.side_effect = write
        with patch('phone.voice.synthesize_wav', side_effect=synthesize), \
                patch('sounddevice.RawOutputStream', return_value=stream) as output:
            self.controller.speak(text)
        self.assertEqual(''.join(synthesized), text)
        self.assertGreaterEqual(len(synthesized), 3)
        expected = b''.join(bytes([index, 0]) * 800 for index in range(1, len(synthesized) + 1))
        self.assertEqual(b''.join(writes), expected)
        output.assert_called_once()

    def test_cancel_during_synthesis_releases_speech_without_playing_stale_audio(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        errors = []
        original = self.controller.cancelled

        def synthesize(_chunk):
            entered.set()
            release.wait(3)
            finished.set()
            return wav_bytes()

        def speak():
            try:
                self.controller.speak('Please stop this answer before its audio is ready.')
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=speak, daemon=True)
        with patch('phone.voice.synthesize_wav', side_effect=synthesize), \
                patch('sounddevice.RawOutputStream') as output:
            try:
                worker.start()
                self.assertTrue(entered.wait(1))
                self.controller.cancel()
                self.controller.cancelled = threading.Event()
                worker.join(.5)
                self.assertFalse(worker.is_alive(), 'Cancellation waited for synthesis to finish')
                self.assertTrue(original.is_set())
                self.assertFalse(self.controller.cancelled.is_set())
            finally:
                release.set()
                worker.join(2)
                finished.wait(2)
            output.assert_not_called()
        self.assertEqual(errors, [])

    def test_prefetch_failure_is_reported_after_current_audio_finishes(self):
        stream = MagicMock()
        stream.__enter__.return_value = stream
        with patch('phone.voice.synthesize_wav', side_effect=[wav_bytes(), OSError('synthesis failed')]), \
                patch('sounddevice.RawOutputStream', return_value=stream):
            with self.assertRaisesRegex(OSError, 'synthesis failed'):
                self.controller.speak('A complete sentence with several words. ' * 12)
        self.assertEqual(b''.join(call.args[0] for call in stream.write.call_args_list), b'\1\0' * 800)
        self.assertIsNone(self.controller.playback)
        self.assertEqual(self.controller.spoken_chunk, '')
        self.assertEqual(self.controller.spoken_text, '')

    def test_cancel_during_playback_discards_prefetched_audio(self):
        preparing, release, finished = threading.Event(), threading.Event(), threading.Event()
        chunks, errors = [], []
        stream = MagicMock()
        stream.__enter__.return_value = stream

        def synthesize(chunk):
            chunks.append(chunk)
            if len(chunks) == 2:
                preparing.set()
                release.wait(3)
                finished.set()
            return wav_bytes()

        def write(_block):
            self.assertTrue(preparing.wait(1))
            self.controller.cancel()

        def speak():
            try:
                self.controller.speak('This answer would otherwise continue for several more sentences. ' * 12)
            except Exception as error:
                errors.append(error)

        stream.write.side_effect = write
        worker = threading.Thread(target=speak, daemon=True)
        with patch('phone.voice.synthesize_wav', side_effect=synthesize), \
                patch('sounddevice.RawOutputStream', return_value=stream):
            try:
                worker.start()
                worker.join(1.5)
                self.assertFalse(worker.is_alive(), 'Playing speech waited on unused prefetched audio after stop')
            finally:
                release.set()
                worker.join(2)
                finished.wait(2)
        self.assertEqual(errors, [])
        self.assertEqual(len(chunks), 2, 'Synthesis continued after cancellation')
        stream.write.assert_called_once()
        stream.abort.assert_called()

    def check_wake(self, general_text, expected_pause):
        self.controller.windows_desktop = 'Default'
        self.controller.spoken_chunk = 'The forecast calls for rain throughout the afternoon.'
        listener = Listener(self.controller, {})
        model = MagicMock()
        model.vosk_model_find_word.return_value = 0
        general, keyword = MagicMock(), MagicMock()
        general.AcceptWaveform.side_effect = [False, True]
        general.PartialResult.return_value = json.dumps({'partial': general_text})
        general.Result.return_value = json.dumps({'text': general_text})
        keyword.AcceptWaveform.return_value = False
        keyword.PartialResult.return_value = '{"partial":"jarvis"}'
        keyword.FinalResult.return_value = '{}'
        for _ in range(2):
            listener.audio.put(b'\0\0' * 1280)

        def finish(*_args, **_kwargs):
            listener.stopped.set()

        self.controller.busy.acquire()
        try:
            with patch('vosk.Model', return_value=model), patch('vosk.KaldiRecognizer', side_effect=[general, keyword]), \
                    patch('desktop.listener.configure_endpoint', return_value=2), \
                    patch('desktop.listener.app_grammar', return_value='[]'), \
                    patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture'}), \
                    patch('sounddevice.RawInputStream'), \
                    patch.object(listener, 'handle_interruption', side_effect=finish), \
                    patch.object(self.controller, 'pause_interruption', wraps=self.controller.pause_interruption) as pause:
                listener.run()
            self.assertEqual(pause.called, expected_pause)
        finally:
            self.controller.busy.release()

    def test_keyword_false_positive_cannot_pause_recognized_playback_echo(self):
        self.check_wake('the forecast calls for rain', False)

    def test_keyword_still_pauses_a_request_misheard_by_general_recognizer(self):
        self.check_wake('this stop talking', True)


if __name__ == '__main__':
    unittest.main()
