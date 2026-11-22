import json
import queue
import time
import unittest
from unittest.mock import MagicMock, patch

from desktop.listener import speech_onset_seconds, split_wake, wake_candidate_source
from tests.desktop import test_wake_grammar as grammar


class WakeRuleTests(unittest.TestCase):
    def test_the_name_counts_wherever_someone_would_say_it_to_jarvis(self):
        for text, command in (('Jarvis, what time is it?', 'what time is it?'),
                              ('Yo, um, Jarvis, what is the weather?', 'what is the weather?'),
                              ('okay so jarvis open spotify', 'open spotify'),
                              ("What's the weather like, Jarvis?", "What's the weather like?"),
                              ('What time is it Jarvis?', 'What time is it?'),
                              ('Can you, Jarvis, open Spotify?', 'Can you open Spotify?'),
                              ('I finished the report. Jarvis, send it to Sam.', 'send it to Sam.'),
                              ('Jarvis.', '')):
            with self.subTest(text=text):
                self.assertEqual(split_wake(text), (True, command))

    def test_talking_about_jarvis_is_not_talking_to_him(self):
        for text in ('We were talking about Jarvis at lunch.', 'I really like Jarvis.', 'we were talking about jarvis',
                     'Travis is driving the car.', 'Java is a programming language.'):
            with self.subTest(text=text):
                self.assertEqual(split_wake(text), (False, ''))

    def test_a_name_late_in_unpunctuated_speech_goes_to_whisper_to_confirm(self):
        self.assertEqual(split_wake('whats the weather like jarvis'), (False, ''))
        self.assertEqual(wake_candidate_source('whats the weather like jarvis', '[unk]'), 'name_heard')
        self.assertEqual(wake_candidate_source('what is the weather jervis', '[unk]'), 'name_heard')
        self.assertEqual(wake_candidate_source('we talked about it', '[unk] [unk] jarvis'), 'wake_grammar')
        self.assertIsNone(wake_candidate_source('what is the weather', '[unk]'))

    def test_speech_onset_skips_the_silence_before_the_name(self):
        self.assertEqual(speech_onset_seconds(b'\0\0' * 16000), 1.0)


class WakeAnywhereTests(unittest.TestCase):
    setUp = grammar.WakeGrammarTests.setUp
    events = grammar.WakeGrammarTests.events
    run_utterances = grammar.WakeGrammarTests.run_utterances

    def test_a_question_that_ends_with_the_name_is_answered(self):
        commands, transcribe, acknowledge, _states = self.run_utterances(
            [('whats the weather like jarvis', '[unk]')], ["What's the weather like, Jarvis?"])
        self.assertEqual(commands, [('command', "What's the weather like?")])
        self.assertEqual([call.kwargs['mode'] for call in transcribe.call_args_list], ['offline'],
                         'An uncertain name is only ever checked on this PC')
        acknowledge.assert_not_called()

    def test_filler_words_before_the_name_still_wake(self):
        commands, _transcribe, _acknowledge, states = self.run_utterances(
            [('yo um jarvis what time is it', '[unk] jarvis [unk]')], ['Yo, um, Jarvis, what time is it?'])
        self.assertEqual(commands, [('command', 'what time is it?')])
        self.assertIn('listening', [state for state, _ in states])

    def test_a_mention_whisper_hears_is_ignored_and_logged_as_such(self):
        commands, _transcribe, acknowledge, _states = self.run_utterances(
            [('we were talking about jarvis', '[unk] jarvis')], ['We were talking about Jarvis.'])
        self.assertEqual(commands, [])
        acknowledge.assert_not_called()
        self.assertIn('name_not_addressed', [fields['reason'] for fields in self.events('speech_ignored')])

    def test_a_lone_name_right_after_a_question_answers_that_question(self):
        # Heard clearly, the name needs no check of its own; only the question before it is transcribed.
        self.listener.previous_phrase = (b'\0\0' * 16000, time.monotonic())
        commands, transcribe, acknowledge, _states = self.run_utterances([('jarvis', 'jarvis')], ["What's the weather like?"])
        self.assertEqual(commands, [('command', "What's the weather like?")])
        acknowledge.assert_not_called()
        self.assertEqual(transcribe.call_count, 1)
        self.assertIn('trailing_name', [fields['source'] for fields in self.events('speech_accepted')])
        # A name only the grammar heard is confirmed by Whisper first, then the question before it is used.
        self.listener.stopped.clear()
        self.listener.previous_phrase = (b'\0\0' * 16000, time.monotonic())
        commands, transcribe, acknowledge, _states = self.run_utterances([('jar', 'jarvis')], ['Jarvis?', 'What time is it?'])
        self.assertEqual(commands, [('command', "What's the weather like?"), ('command', 'What time is it?')])
        acknowledge.assert_not_called()

    def test_a_lone_name_long_after_other_speech_just_gets_its_reply(self):
        self.listener.previous_phrase = (b'\0\0' * 16000, time.monotonic() - 30)
        commands, transcribe, acknowledge, _states = self.run_utterances([('jarvis', 'jarvis')], [])
        self.assertEqual(commands, [])
        acknowledge.assert_called_once()
        transcribe.assert_not_called()

    def test_he_turns_green_as_soon_as_either_decoder_hears_the_name(self):
        general, app = MagicMock(), MagicMock()
        general.AcceptWaveform.return_value = False
        general.PartialResult.return_value = json.dumps({'partial': 'whats the weather like job as'})
        app.AcceptWaveform.return_value = False
        app.PartialResult.return_value = json.dumps({'partial': '[unk] jarvis'})
        blocks = iter([b'\1\2' * 1280])

        def next_audio(block=True, timeout=None):
            try:
                return next(blocks)
            except StopIteration:
                self.listener.stopped.set()
                raise queue.Empty

        states = []
        with patch('vosk.Model'), patch('vosk.KaldiRecognizer', side_effect=[general, app]), \
             patch('desktop.listener.configure_endpoint', return_value=2), \
             patch('desktop.listener.app_grammar', return_value='[]'), \
             patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'fixture'}), \
             patch('sounddevice.RawInputStream'), \
             patch.object(self.listener.audio, 'get', side_effect=next_audio), \
             patch.object(self.controller, 'update', side_effect=lambda state, note='': states.append(state)):
            self.listener.run()
        self.assertIn('listening', states)


if __name__ == '__main__':
    unittest.main()
