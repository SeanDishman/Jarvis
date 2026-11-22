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
from desktop.listener import Listener, split_wake


class WakeActivationTests(unittest.TestCase):
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

    def test_name_alone_does_not_authorize_the_next_sentence(self):
        self.assertFalse(self.listener.accepts_speech('Jarvis'))
        self.assertFalse(self.listener.conversation_active)
        self.assertEqual(self.controller.state, 'ready')
        self.assertFalse(self.listener.accepts_speech('what time is it'))
        self.listener.listening_until = time.monotonic() - 60
        self.assertFalse(self.listener.accepts_speech('what time is it'))
        self.assertFalse(any(row[0] == 'command' for row in self.controller.events.queue))

    def test_every_command_requires_its_own_wake_even_after_ui_activation(self):
        for source in ('insert', 'orb', 'launch', 'followup'):
            self.listener.activate(source=source, continuous=True)
            self.assertFalse(self.listener.conversation_active)
            self.assertFalse(self.listener.accepts_speech('what time is it'))
            self.assertTrue(self.listener.accepts_speech('Jarvis what time is it'))
            self.assertFalse(self.listener.accepts_speech('and what is the weather'))
            self.assertTrue(self.listener.accepts_speech('Javis what is the weather'))

    def capture_utterances(self, utterances, transcripts):
        general, app = MagicMock(), MagicMock()
        general.AcceptWaveform.return_value = True
        general.Result.side_effect = [json.dumps({'text': text}) for text in utterances]
        app.FinalResult.return_value = '{}'
        app.Result.return_value = '{}'
        app.PartialResult.return_value = '{}'
        blocks = iter([b'\1\2' * 1280 for _ in utterances])
        def next_audio(block=True, timeout=None):
            if block:
                try:
                    return next(blocks)
                except StopIteration:
                    self.listener.stopped.set()
            raise queue.Empty
        with patch('vosk.Model'), patch('vosk.KaldiRecognizer', side_effect=[general, app]), \
             patch('desktop.listener.configure_endpoint', return_value=2), \
             patch('desktop.listener.app_grammar', return_value='[]'), \
             patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture'}), \
             patch('sounddevice.RawInputStream'), \
             patch.object(self.listener.audio, 'get', side_effect=next_audio), \
             patch.object(self.controller.preferences, 'read', return_value={'offlineSpeech': 'auto'}), \
             patch('desktop.listener.transcribe_pcm', side_effect=transcripts) as transcribe:
            self.listener.run()
        return transcribe, list(self.controller.events.queue)

    def test_ambient_speech_and_unconfirmed_wake_never_reach_command_queue(self):
        self.listener.activate(source='insert', continuous=True)
        transcribe, events = self.capture_utterances(
            ['open notepad', 'jarvis what time is it', 'what is the weather',
             'jarvis check the weather', 'jarvis check traffic'],
            ['Jarvis, what time is it?', 'Check the weather', 'Jarvis, check traffic'])
        self.assertEqual(transcribe.call_count, 3)
        self.assertEqual([event for event in events if event[0] == 'command'],
                         [('command', 'what time is it?'), ('command', 'check traffic')])

    def test_greetings_and_recognizer_fillers_before_the_name_still_wake(self):
        for text, command in (('Yo, Jarvis.', ''), ('yo jarvis what time is it', 'what time is it'),
                              ('the jarvis what time is it', 'what time is it'),
                              ('Hey there, Jarvis, open Notepad', 'open Notepad'), ('What’s up, Jarvis?', ''),
                              ('good morning jarvis', ''), ('Hey Jarvis, what time is it?', 'what time is it?')):
            self.assertEqual(split_wake(text), (True, command), text)
        for text in ('I think jarvis is broken', 'we were talking about jarvis', 'Chavez what time is it', 'javascript'):
            self.assertFalse(split_wake(text)[0], text)

    def test_a_misheard_name_is_accepted_only_after_the_wake_word_was_heard(self):
        self.assertEqual(split_wake('Yo, Chavez, what time is it?', heard=True), (True, 'what time is it?'))
        self.assertFalse(split_wake('Check the weather', heard=True)[0])
        self.listener.activate(source='insert', continuous=True)
        transcribe, events = self.capture_utterances(
            ['the jarvis what time is it', 'jarvis check the weather', 'what is the weather'],
            ['Yo Jarvis, what time is it?', 'Chavez, check the weather'])
        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual([event for event in events if event[0] == 'command'],
                         [('command', 'what time is it?'), ('command', 'check the weather')])

    def test_the_name_alone_gets_a_spoken_reply_without_arming_the_next_sentence(self):
        self.controller.voice_ready = True
        replied = threading.Event()
        with patch.object(self.controller, 'speak', side_effect=lambda text: replied.set()) as speak:
            self.assertFalse(self.listener.accepts_speech('yo jarvis'))
            self.assertTrue(replied.wait(2), 'Jarvis did not answer his name')
            speak.assert_called_once_with('Yes, sir?')
            deadline = time.monotonic() + 2
            while self.controller.busy.locked() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(self.controller.busy.locked())
            self.assertFalse(self.listener.accepts_speech('what time is it'))
            self.listener.mute(True)
            self.assertFalse(self.listener.accepts_speech('Jarvis'))
            self.listener.mute(False)
            with self.controller.busy:
                self.assertFalse(self.listener.accepts_speech('Jarvis'))
            self.assertEqual(speak.call_count, 1, 'Muted or busy Jarvis must not answer his name')
        self.assertFalse(any(row[0] == 'command' for row in self.controller.events.queue))

    def test_ambient_speech_cannot_interrupt_a_reply(self):
        with self.controller.busy:
            transcribe, events = self.capture_utterances(
                ['how are you', 'stop', 'jarvis stop'], ['Jarvis, stop'])
        self.assertEqual(transcribe.call_count, 1)
        self.assertEqual([event[:2] for event in events if event[0] == 'interruption'],
                         [('interruption', 'Jarvis, stop')])

    def test_local_pc_stop_also_requires_wake(self):
        with patch.object(self.controller, 'stop_computer_tasks', return_value=True) as stop:
            transcribe, events = self.capture_utterances(['stop', 'cancel the task', 'jarvis stop'], [])
        stop.assert_called_once()
        transcribe.assert_not_called()
        self.assertIn(('notice', 'PC control stopped.'), events)

    def test_interruption_requires_wake_in_confirmed_transcript(self):
        with patch.object(self.listener, 'recognize', return_value='Stop talking'):
            self.listener.handle_interruption('jarvis stop talking', b'fixture audio')
        self.assertFalse(any(event[0] == 'interruption' for event in self.controller.events.queue))

    def test_partial_wake_preserves_queued_audio(self):
        audio = b'\1\2' * 1280
        self.listener.audio.put(audio)
        self.assertTrue(self.listener.activate_from_wake('hey jarvis'))
        self.assertFalse(self.listener.reset.is_set())
        self.assertEqual(self.listener.audio.get_nowait(), audio)
        self.assertTrue(self.listener.activate_from_wake('hey jarvis what time'))
        self.assertFalse(self.listener.reset.is_set())

    def test_wake_cannot_bypass_hidden_mute_break_or_busy(self):
        self.listener.hide(True)
        self.assertFalse(self.listener.activate_from_wake('Jarvis'))
        self.assertFalse(self.listener.accepts_speech('Jarvis open Notepad'))
        self.listener.hide(False)
        self.listener.mute(True)
        self.assertFalse(self.listener.activate_from_wake('Jarvis'))
        self.assertFalse(self.listener.accepts_speech('Jarvis open Notepad'))
        self.assertTrue(self.listener.muted)
        self.listener.mute(False)
        self.controller.breaks.start(60)
        self.assertFalse(self.listener.activate_from_wake('Jarvis'))
        self.assertFalse(self.listener.accepts_speech('Jarvis open Notepad'))
        self.controller.breaks.resume()
        with self.controller.busy:
            self.assertFalse(self.listener.activate_from_wake('Jarvis'))
        self.assertFalse(self.listener.conversation_active)

    def test_unrelated_mentions_do_not_wake(self):
        for text in ('javascript', 'we were talking about jarvis', 'jarvison is here', 'open notepad'):
            self.assertFalse(self.listener.activate_from_wake(text), text)
        self.assertFalse(self.listener.conversation_active)
        self.assertTrue(self.listener.activate_from_wake('javis'))

    def test_wake_and_question_in_one_sentence_reach_transcription_together(self):
        first, second = b'\1\2' * 1280, b'\3\4' * 1280
        self.listener.audio.put(first)
        self.listener.audio.put(second)
        general, app = MagicMock(), MagicMock()
        general.AcceptWaveform.side_effect = [False, True]
        general.PartialResult.return_value = json.dumps({'partial': 'jarvis'})
        general.Result.return_value = json.dumps({'text': 'jarvis what time is it'})
        app.FinalResult.return_value = '{}'
        put = self.controller.events.put
        def event(row):
            put(row)
            if row[0] == 'command':
                self.listener.stopped.set()
        with patch('vosk.Model'), patch('vosk.KaldiRecognizer', side_effect=[general, app]), \
             patch('desktop.listener.configure_endpoint', return_value=2), \
             patch('desktop.listener.app_grammar', return_value='[]'), \
             patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture'}), \
             patch('sounddevice.RawInputStream'), \
             patch.object(self.controller.preferences, 'read', return_value={'offlineSpeech': 'auto'}), \
             patch('desktop.listener.transcribe_pcm', return_value='Jarvis, what time is it?') as transcribe, \
             patch.object(self.controller.events, 'put', side_effect=event):
            self.listener.run()
        transcribe.assert_called_once_with(first + second, 'whisper-large-v3', mode='auto')
        self.assertEqual([row for row in self.controller.events.queue if row[0] == 'command'],
                         [('command', 'what time is it?')])


if __name__ == '__main__':
    unittest.main()
