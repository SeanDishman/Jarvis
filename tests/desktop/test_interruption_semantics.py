import json
import io
import queue
import threading
import tempfile
from pathlib import Path
import unittest
import wave
import inspect
from unittest.mock import MagicMock, patch

from desktop.controller import Controller
from desktop.listener import Listener
from desktop.breaks import BreakState


def decision(action):
    return {'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps({'action': action})}]}]}


class InterruptionTests(unittest.TestCase):
    def test_every_utterance_uses_the_model_and_supplies_context(self):
        for text in ('stop', 'shut the fuck up, Jarvis', 'shorter',
                     'That will do for now', 'do not stop', 'stop the timer'):
            with self.subTest(text=text):
                controller = Controller(queue.Queue())
                controller.request = {'ownerText': 'Explain the weather forecast'}
                controller.spoken_chunk = 'Rain is expected tomorrow.'
                with patch('desktop.controller.local_request', return_value=decision('ignore')) as request, \
                     patch.object(controller, 'cancel') as cancel, patch.object(controller, 'respond') as respond:
                    controller.interrupt(text)
                request.assert_called_once()
                payload = request.call_args.args[2]
                self.assertEqual(payload['model'], 'openclaw/interrupt')
                self.assertEqual(json.loads(payload['input']), {
                    'utterance': text, 'ownerRequest': 'Explain the weather forecast',
                    'assistantSpeech': 'Rain is expected tomorrow.', 'openQuestion': None})
                cancel.assert_not_called()
                respond.assert_not_called()
                self.assertFalse(controller.speech_paused.is_set())

    def test_playback_continues_until_a_stop_decision_is_confirmed(self):
        controller = Controller(queue.Queue())
        entered, release = threading.Event(), threading.Event()
        controller.playback = MagicMock()

        def interpret(*_args, **_kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return decision('stop')

        with patch('desktop.controller.local_request', side_effect=interpret), \
             patch.object(controller, 'respond') as respond:
            worker = threading.Thread(target=controller.interrupt, args=('Enough of the lecture',))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertFalse(controller.speech_paused.is_set(), 'Unclassified speech paused the reply')
                self.assertFalse(controller.cancelled.is_set())
                controller.playback.abort.assert_not_called()
            finally:
                release.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(controller.cancelled.is_set())
            controller.playback.abort.assert_called_once()
            respond.assert_not_called()

    def test_shorter_is_driven_by_the_ai_decision(self):
        controller = Controller(queue.Queue())
        with patch('desktop.controller.local_request', return_value=decision('shorter')), \
             patch.object(controller, 'respond') as respond:
            controller.interrupt('I only have time for the gist')
        respond.assert_called_once()
        forwarded = inspect.signature(Controller.respond).bind(controller, *respond.call_args.args, **respond.call_args.kwargs).arguments
        self.assertEqual(forwarded['text'], 'Give me a shorter version of your last answer, in one sentence.')
        self.assertTrue(forwarded['shorten'])
        self.assertTrue(forwarded['wait'])

    def test_failed_or_invalid_classification_resumes_without_guessing(self):
        for failure in (TimeoutError('offline'), ValueError('invalid JSON'), None):
            with self.subTest(failure=failure):
                controller = Controller(queue.Queue())
                with patch('desktop.controller.local_request', side_effect=failure,
                           return_value=decision('invalid')), patch.object(controller, 'respond') as respond:
                    controller.interrupt('Jarvis, shut the fuck up')
                self.assertFalse(controller.cancelled.is_set())
                self.assertFalse(controller.speech_paused.is_set())
                respond.assert_not_called()
                self.assertEqual(controller.events.get_nowait()[0], 'notice')

    def test_old_decision_cannot_cancel_a_new_reply(self):
        controller = Controller(queue.Queue())

        def replaced_reply(*_args, **_kwargs):
            controller.cancelled = threading.Event()
            return decision('stop')

        with patch('desktop.controller.local_request', side_effect=replaced_reply):
            controller.interrupt('Stop speaking')
        self.assertFalse(controller.cancelled.is_set())
        self.assertFalse(controller.speech_paused.is_set())

    def test_listener_pauses_and_sends_full_utterance_without_cancelling(self):
        controller = Controller(queue.Queue())
        listener = Listener(controller, {})
        full = 'Jarvis, stop, actually keep going, I want to hear the rest.'
        with patch.object(listener, 'recognize', return_value=full) as recognize:
            listener.handle_interruption('jarvis stop', b'full utterance audio')
        recognize.assert_called_once_with(b'full utterance audio', update_state=False)
        event = controller.events.get_nowait()
        self.assertEqual(event[:2], ('interruption', full))
        self.assertIs(event[2]['answer'], controller.cancelled)
        self.assertFalse(controller.cancelled.is_set())
        self.assertTrue(controller.speech_paused.is_set())
        with patch('desktop.controller.local_request', return_value=decision('ignore')):
            controller.interrupt(event[1], context=event[2])
        self.assertFalse(controller.speech_paused.is_set())

    def test_failed_transcription_resumes_without_using_local_guess(self):
        controller = Controller(queue.Queue())
        listener = Listener(controller, {})
        with patch.object(listener, 'recognize', side_effect=RuntimeError('speech service unavailable')):
            listener.handle_interruption('jarvis shut up', b'audio')
        self.assertFalse(controller.cancelled.is_set())
        self.assertFalse(controller.speech_paused.is_set())
        self.assertEqual(controller.events.get_nowait(), ('speech_error', 'speech service unavailable'))
        self.assertTrue(controller.events.empty())

    def test_partial_stop_never_cancels_before_complete_sentence(self):
        controller = Controller(queue.Queue())
        controller.windows_desktop = 'default'
        listener = Listener(controller, {})
        general, app = MagicMock(), MagicMock()
        general.AcceptWaveform.side_effect = [False, True]
        general.PartialResult.return_value = json.dumps({'partial': 'jarvis stop'})
        general.Result.return_value = json.dumps({'text': 'jarvis stop the timer'})
        app.FinalResult.return_value = '{}'
        app.Result.return_value = '{}'
        app.PartialResult.return_value = '{}'
        listener.audio.put(b'\x00\x00' * 1280)
        listener.audio.put(b'\x00\x00' * 1280)

        def transcribe(*_args, **_kwargs):
            listener.stopped.set()
            self.assertTrue(controller.speech_paused.is_set(), 'Jarvis kept speaking over a directed interruption')
            self.assertFalse(controller.cancelled.is_set())
            return 'Jarvis, stop the timer'

        controller.busy.acquire()
        try:
            with patch('vosk.Model'), patch('vosk.KaldiRecognizer', side_effect=[general, app]), \
                 patch('desktop.listener.configure_endpoint', return_value=2), \
                 patch('desktop.listener.app_grammar', return_value='[]'), \
                 patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'test'}), \
                 patch('sounddevice.RawInputStream'), patch.object(listener, 'recognize', side_effect=transcribe), \
                 patch.object(controller, 'stop_computer_tasks') as pc_stop:
                listener.run()
            pc_stop.assert_not_called()
        finally:
            controller.busy.release()
        events = []
        while not controller.events.empty():
            events.append(controller.events.get_nowait())
        self.assertIn(('interruption', 'Jarvis, stop the timer'), [event[:2] for event in events])
        self.assertFalse(controller.cancelled.is_set())

    def test_keyword_detection_survives_wrong_general_transcript(self):
        controller = Controller(queue.Queue())
        listener = Listener(controller, {})
        context = controller.interruption_context()
        context['wakeDetected'] = True
        with patch.object(listener, 'recognize', return_value='Jarvis, shut the fuck up.') as recognize:
            listener.handle_interruption('this shut the up', b'audio', context=context)
        recognize.assert_called_once()
        event = controller.events.get_nowait()
        with patch('desktop.controller.local_request', return_value=decision('stop')):
            controller.interrupt(event[1], event[2])
        self.assertTrue(controller.cancelled.is_set())
        self.assertFalse(controller.speech_paused.is_set())

    def test_speech_before_the_wake_word_does_not_discard_interruption(self):
        controller = Controller(queue.Queue())
        listener = Listener(controller, {})
        controller.spoken_chunk = 'Rain is expected tomorrow.'
        for heard, request in (('Rain is expected tomorrow. Jarvis, be quiet please.', 'Jarvis, be quiet please.'),
                               ('RainIsExpectedTomorrowJarvis. Be quiet please.', 'Jarvis. Be quiet please.')):
            with patch.object(listener, 'recognize', return_value=heard):
                listener.handle_interruption(heard, b'audio')
            self.assertEqual(controller.events.get_nowait()[:2], ('interruption', request))

    def test_false_wake_and_transcription_failure_release_playback(self):
        for heard in ('Stop talking.', 'Jarvis.', 'Jarvis can help with the weather.'):
            with self.subTest(heard=heard):
                controller = Controller(queue.Queue())
                listener = Listener(controller, {})
                controller.spoken_chunk = 'Jarvis can help with the weather.'
                context = controller.interruption_context()
                context['wakeDetected'] = True
                controller.pause_interruption(context)
                with patch.object(listener, 'recognize', return_value=heard):
                    listener.handle_interruption('jarvis', b'audio', context)
                self.assertFalse(controller.speech_paused.is_set())
                self.assertFalse(controller.cancelled.is_set())
                self.assertTrue(controller.events.empty())

    def test_model_failure_releases_an_interruption_pause(self):
        controller = Controller(queue.Queue())
        context = controller.interruption_context()
        controller.pause_interruption(context)
        with patch('desktop.controller.local_request', side_effect=TimeoutError('offline')):
            controller.interrupt('Jarvis, could you repeat that?', context)
        self.assertFalse(controller.speech_paused.is_set())
        self.assertFalse(controller.cancelled.is_set())

    def test_old_context_cannot_resume_a_newer_interruption(self):
        controller = Controller(queue.Queue())
        old = controller.interruption_context()
        controller.pause_interruption(old)
        controller.cancelled = threading.Event()
        current = controller.interruption_context()
        controller.pause_interruption(current)
        controller.resume_interruption(old)
        self.assertTrue(controller.speech_paused.is_set())
        controller.resume_interruption(current)
        self.assertFalse(controller.speech_paused.is_set())

    def test_background_speech_does_not_stall_audio_while_ai_decides(self):
        controller = Controller(queue.Queue())
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b'\0\0' * 80000)
        audio_progress, release_audio = threading.Event(), threading.Event()
        classifying, release_decision = threading.Event(), threading.Event()
        stream = MagicMock()

        def write(_block):
            audio_progress.set()
            release_audio.wait(.01)

        def classify(*_args, **_kwargs):
            classifying.set()
            self.assertTrue(release_decision.wait(2))
            return decision('ignore')

        stream.__enter__.return_value = stream
        stream.write.side_effect = write
        speech = threading.Thread(target=controller.speak, args=('Let me explain the answer, sir.',))
        background = threading.Thread(target=controller.interrupt, args=('Yeah okay',))
        with patch('phone.voice.synthesize_wav', return_value=buffer.getvalue()), \
             patch('sounddevice.RawOutputStream', return_value=stream), \
             patch('desktop.controller.local_request', side_effect=classify), \
             patch.object(controller, 'respond') as respond:
            try:
                speech.start()
                self.assertTrue(audio_progress.wait(2))
                background.start()
                self.assertTrue(classifying.wait(2))
                audio_progress.clear()
                self.assertTrue(audio_progress.wait(.5), 'Background speech stalled actual output writes')
                self.assertFalse(controller.speech_paused.is_set())
                release_decision.set()
                background.join(2)
                self.assertFalse(controller.cancelled.is_set())
                stream.abort.assert_not_called()
                respond.assert_not_called()
            finally:
                release_decision.set()
                release_audio.set()
                speech.join(2)
                if background.ident:
                    background.join(2)
                controller.research.close()
        self.assertFalse(speech.is_alive() or background.is_alive())

    def test_speech_context_survives_playback_advancing_during_transcription(self):
        controller = Controller(queue.Queue())
        listener = Listener(controller, {})
        controller.request = {'ownerText': 'Explain the weather'}
        controller.spoken_chunk = 'Rain is expected tomorrow.'
        context = controller.interruption_context()

        def transcribe(*_args, **_kwargs):
            controller.spoken_chunk = 'The wind will become stronger.'
            return 'Rain is expected tomorrow.'

        with patch.object(listener, 'recognize', side_effect=transcribe) as recognize:
            listener.handle_interruption('jarvis different rough local decoding', b'audio', context=context)
        recognize.assert_called_once()
        self.assertTrue(controller.events.empty(), 'Playback echo was classified against a later passage')
        self.assertFalse(controller.speech_paused.is_set())

    def test_captured_interruption_cannot_cancel_a_newer_answer(self):
        controller = Controller(queue.Queue())
        context = controller.interruption_context()
        controller.cancelled = threading.Event()
        with patch('desktop.controller.local_request') as model, patch.object(controller, 'cancel') as cancel:
            controller.interrupt('Stop', context=context)
        model.assert_not_called()
        cancel.assert_not_called()

    def test_new_question_interrupts_thinking_and_can_itself_be_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(queue.Queue())
            controller.breaks = BreakState(Path(directory) / 'break.json')
            controller.microphone_ready = True
            listener = Listener(controller, {})
            controller.listener = listener
            listener.activate(continuous=True)
            started = [threading.Event(), threading.Event()]
            aborted = [threading.Event(), threading.Event()]
            sessions = []
            results = []

            def transport(_port, route, payload, **_kwargs):
                if route == '/session/end':
                    aborted[sessions.index(payload['user'])].set()
                    return {'status': 'cleared'}
                if payload['model'] == 'openclaw/interrupt':
                    utterance = json.loads(payload['input'])['utterance']
                    return decision('stop' if utterance == 'Stop there' else 'followup')
                index = len(sessions)
                sessions.append(payload['user'])
                started[index].set()
                self.assertTrue(aborted[index].wait(4), 'The unfinished model call was not cancelled')
                return {'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': 'Stale answer must not be spoken.'}]}]}

            first = threading.Thread(target=lambda: results.append(controller.respond('Explain gravity')))
            followup = threading.Thread(target=controller.interrupt, args=('What time is it instead?',))
            with patch('desktop.controller.classify_request', return_value={'action': 'normal'}), \
                 patch.object(controller.preferences, 'read', return_value={'offlineSpeech': 'online'}), \
                 patch('desktop.controller.local_request', side_effect=transport), \
                 patch.object(controller, 'speak') as speak:
                try:
                    first.start()
                    self.assertTrue(started[0].wait(2))
                    self.assertEqual(controller.state, 'thinking')
                    listener.callback(b'\0\1' * 1280, 1280, None, None)
                    self.assertFalse(listener.audio.empty())
                    followup.start()
                    self.assertTrue(started[1].wait(2), 'The new question waited for the original answer')
                    self.assertTrue(aborted[0].is_set())
                    self.assertNotEqual(sessions[0], sessions[1], 'Cancellation reused the old session')
                    self.assertFalse(controller.interrupt_lock.locked(), 'New answer blocked further speech interruptions')
                    controller.interrupt('Stop there')
                    self.assertTrue(aborted[1].wait(2), 'The second interruption was ignored')
                finally:
                    for pending in aborted:
                        pending.set()
                    first.join(3)
                    if followup.ident:
                        followup.join(3)
                    controller.research.close()
                speak.assert_not_called()
            self.assertFalse(first.is_alive() or followup.is_alive())
            self.assertFalse(controller.busy.locked())
            self.assertEqual(results[0].get('error'), 'Cancelled')
            self.assertFalse(any(event[0] == 'answer' for event in list(controller.events.queue)))


if __name__ == '__main__':
    unittest.main()
