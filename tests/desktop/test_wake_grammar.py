import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.listener import WAKE_PHRASES, Listener, app_grammar, discard_idle_phrase, wake_in_grammar, without_repeated_names


class WakeGrammarTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.logged = []
        for name in ('desktop.listener.diagnostic', 'desktop.controller.diagnostic'):
            patcher = patch(name, side_effect=lambda event, **fields: self.logged.append((event, fields)))
            patcher.start()
            self.addCleanup(patcher.stop)
        with patch('desktop.controller.BreakState', return_value=BreakState(Path(temporary.name) / 'break.json')):
            self.controller = Controller(queue.Queue())
        for service in (self.controller.research, self.controller.tasks, self.controller.browser):
            self.addCleanup(service.close)
        self.controller.windows_desktop = 'default'
        self.controller.microphone_ready = True
        self.listener = Listener(self.controller, {})
        self.controller.listener = self.listener
        self.mode = 'auto'
        self.local_ready = True
        for target, value in (('desktop.listener.offline_speech.ready', lambda: self.local_ready),):
            patcher = patch(target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(self.controller.preferences, 'read', side_effect=lambda: {'offlineSpeech': self.mode})
        patcher.start()
        self.addCleanup(patcher.stop)

    def events(self, name):
        return [fields for event, fields in self.logged if event == name]

    def test_the_launch_grammar_hears_the_name_where_the_general_model_does_not(self):
        # Wherever the grammar hears the name, the phrase goes to Whisper to confirm it was said to Jarvis.
        for heard in ('jarvis', 'jarvis open chrome', 'hey jarvis open spotify', 'okay jarvis new for voice', '[unk] jarvis',
                      'services notes jarvis'):
            self.assertTrue(wake_in_grammar(heard), heard)
        for heard in ('java weather live', 'open spotify', '', None):
            self.assertFalse(wake_in_grammar(heard), heard)
        model = MagicMock()
        model.vosk_model_find_word.return_value = 1
        phrases = json.loads(app_grammar([{'Name': 'Spotify'}], model))
        for phrase in (*WAKE_PHRASES, '[unk]', 'jarvis open spotify', 'open spotify'):
            self.assertIn(phrase, phrases)

    def test_a_name_only_the_grammar_heard_waits_for_local_confirmation(self):
        state = self.controller.state
        self.assertEqual(self.listener.accepts_speech('hey job as open spot of fi', 'hey jarvis open spotify'), 'confirm')
        self.assertEqual(self.listener.accepts_speech('', 'jarvis'), 'confirm', 'A quiet name the general model missed')
        self.assertEqual(self.controller.state, state, 'Nothing reacts before Whisper confirms the name')
        self.assertEqual(self.events('wake_candidate')[0]['source'], 'wake_grammar')
        self.assertIs(self.listener.accepts_speech('jarvis what time is it', 'jarvis [unk]'), True, 'A clear name works as before')
        self.mode = 'online'
        self.assertFalse(self.listener.accepts_speech('hey job as open spot of fi', 'hey jarvis open spotify'),
                         'Online-only speech never uploads an unconfirmed clip')
        self.mode, self.local_ready = 'auto', False
        self.assertFalse(self.listener.accepts_speech('hey job as open spot of fi', 'hey jarvis open spotify'))
        self.mode = 'offline'
        self.assertEqual(self.listener.accepts_speech('hey job as open spot of fi', 'hey jarvis open spotify'), 'confirm')
        self.assertFalse(self.listener.accepts_speech('java weather live', 'java weather live'))

    def run_utterances(self, utterances, transcripts):
        general, app = MagicMock(), MagicMock()
        general.AcceptWaveform.return_value = True
        general.Result.side_effect = [json.dumps({'text': text}) for text, _ in utterances]
        app.FinalResult.side_effect = [json.dumps({'text': heard}) for _, heard in utterances]
        app.Result.return_value = app.PartialResult.return_value = '{}'
        blocks = iter([b'\1\2' * 1280 for _ in utterances])

        def next_audio(block=True, timeout=None):
            if block:
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
             patch.object(self.controller, 'acknowledge_wake') as acknowledge, \
             patch.object(self.controller, 'update', side_effect=lambda state, note='': states.append((state, note))), \
             patch('desktop.listener.transcribe_pcm', side_effect=transcripts) as transcribe:
            self.listener.run()
        commands = [event for event in self.controller.events.queue if event[0] == 'command']
        return commands, transcribe, acknowledge, states

    def test_a_misheard_name_is_confirmed_by_whisper_and_the_command_runs(self):
        commands, transcribe, acknowledge, states = self.run_utterances(
            [('hey job as open spot of fi', 'hey jarvis open spotify')], ['Hey Jarvis, open Spotify.'])
        self.assertEqual(commands, [('command', 'open Spotify.')])
        self.assertEqual(transcribe.call_count, 1)
        acknowledge.assert_not_called()
        self.assertNotIn('thinking', [state for state, _ in states], 'An unconfirmed clip must not flash Transcribing')
        self.assertEqual([fields['source'] for fields in self.events('speech_accepted')], ['wake_grammar'])

    def test_speech_without_the_name_is_dropped_after_the_local_check(self):
        commands, transcribe, acknowledge, states = self.run_utterances(
            [('java is a programming language', 'jarvis opera chrome english'),
             ('tor vs drive english com', 'jarvis drive english com')],
            ['Java is a programming language.', 'Travis is driving the car.'])
        self.assertEqual(commands, [], 'A grammar-only wake needs Whisper to write the name itself, not Travis or Chavez')
        self.assertEqual(transcribe.call_count, 2)
        acknowledge.assert_not_called()
        self.assertEqual(len(self.events('speech_ignored')), 2)
        self.assertEqual({fields['reason'] for fields in self.events('speech_ignored')}, {'wake_unconfirmed'})
        self.assertNotIn('thinking', [state for state, _ in states])

    def test_the_name_alone_gets_its_reply_once_confirmed(self):
        commands, transcribe, acknowledge, _states = self.run_utterances([('', 'jarvis'), ('jar', 'jarvis')], ['Jarvis.', 'Jarvis.'])
        self.assertEqual(commands, [])
        self.assertEqual(acknowledge.call_count, 2)

    def test_a_repeated_name_is_not_sent_as_the_command(self):
        self.assertEqual(without_repeated_names('Jarvis, open Spotify.'), 'open Spotify.')
        self.assertEqual(without_repeated_names('Jarvis. Hey Jarvis. What time is it?'), 'What time is it?')
        self.assertEqual(without_repeated_names('Jarvis.'), '')
        self.assertEqual(without_repeated_names('open Jarvis settings'), 'open Jarvis settings')
        self.assertEqual(without_repeated_names('Javascript tips'), 'Javascript tips')
        commands, transcribe, acknowledge, _states = self.run_utterances(
            [('jarvis jarvis open spotify', 'jarvis jarvis'), ('jarvis jarvis', 'jarvis jarvis'), ('', 'jarvis jarvis')],
            ['Jarvis. Jarvis, open Spotify.', 'Jarvis. Jarvis.', 'Jarvis, Jarvis.'])
        self.assertEqual(commands, [('command', 'open Spotify.')])
        self.assertEqual(acknowledge.call_count, 2, 'Calling the name twice gets one reply, not a command to the AI')

    def test_repeated_calls_after_background_need_independent_local_confirmation(self):
        background = 'we should finish the report'
        heard = background + ' jarvis jarvis'
        commands, transcribe, acknowledge, _states = self.run_utterances(
            [(heard, '[unk]'), (heard + ' what time is it', '[unk]'), (background, '[unk] jarvis jarvis')],
            ['We should finish the report. Jarvis! Jarvis!',
             'We should finish the report. Jarvis, hey Jarvis, what time is it?',
             'We were talking about Jarvis at lunch.'])
        self.assertEqual(commands, [('command', 'what time is it?')])
        acknowledge.assert_called_once()
        self.assertEqual([call.kwargs['mode'] for call in transcribe.call_args_list], ['offline'] * 3)
        self.mode = 'online'
        self.assertFalse(self.listener.accepts_speech(heard, '[unk] jarvis jarvis'))

    def test_name_resolved_only_at_a_short_pause_keeps_original_command_audio(self):
        general, grammar = MagicMock(), MagicMock()
        grammar.PartialResult.return_value = '{"partial":"[unk]"}'
        general.FinalResult.return_value = '{"text":"jar"}'
        grammar.FinalResult.return_value = '{"text":"jarvis"}'
        pcm = b'\x01\x02' * 1280
        self.assertFalse(discard_idle_phrase(general, grammar, 'the', bytearray(pcm)))
        general.AcceptWaveform.assert_called_once_with(pcm)
        grammar.AcceptWaveform.assert_called_once_with(pcm)

    def test_clear_names_still_take_the_usual_path(self):
        commands, transcribe, acknowledge, states = self.run_utterances(
            [('jarvis what time is it', 'jarvis [unk]'), ('what is the weather', '[unk]')], ['Jarvis, what time is it?'])
        self.assertEqual(commands, [('command', 'what time is it?')])
        self.assertEqual(transcribe.call_count, 1, 'Speech the grammar did not hear as the name is never transcribed')
        self.assertIn('thinking', [state for state, _ in states])
        self.assertEqual(self.events('wake_candidate'), [])


if __name__ == '__main__':
    unittest.main()
