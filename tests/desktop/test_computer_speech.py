from tests.desktop.intent_fixtures import decision
import base64
import ctypes
import io
import queue
import struct
import threading
import time
import unittest
import wave
from unittest.mock import Mock, patch

from desktop import computer, speech
from desktop.common import protect
from desktop.controller import Controller, capabilities_for
from desktop.model_commands import switch_model
from desktop.listener import Listener
from desktop.research import ResearchJobs


@patch('desktop.controller.classify_request', lambda *_args: {'action': 'normal'})
class ComputerTests(unittest.TestCase):
    def setUp(self):
        self.target = {'hwnd': 11, 'pid': 999999, 'title': 'Proton VPN', 'process': 'protonvpn.exe',
                       'rect': [-1000, 100, 0, 700]}
        self.frame = {'width': 500, 'height': 300, 'rect': self.target['rect'][:], 'captured': time.monotonic()}

    def test_click_scaling_and_png_dimensions(self):
        self.assertEqual(computer.image_point(self.frame, 250, 150), (-500, 400))
        for point in ((500, 0), (-1, 4), (True, 3), (2.5, 4)):
            with self.assertRaises(RuntimeError):
                computer.image_point(self.frame, *point)
        png = computer.encode_png(2, 1, bytes([0, 0, 255, 255, 255, 0, 0, 255]))
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('!II', png[16:24]), (2, 1))
        self.assertEqual(ctypes.sizeof(computer.Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_mail_and_shell_input_unavailable_browsers_controllable(self):
        for process in ('outlook.exe', 'olk.exe', 'powershell.exe'):
            self.assertFalse(computer.controllable({**self.target, 'process': process}))
        for process in ('chrome.exe', 'msedge.exe', 'opera.exe', 'dropbox.exe', 'unlisted-editor.exe'):
            self.assertTrue(computer.controllable({**self.target, 'process': process}))
        self.assertFalse(computer.controllable({**self.target, 'title': 'Gmail Inbox'}))
        self.assertTrue(computer.controllable(self.target))
        self.assertNotIn('start_computer_task', capabilities_for(decision('search_email','read_email'))[0])
        self.assertEqual(capabilities_for(decision('browser_click'))[2], 'browser')

    def test_changed_target_focus_or_old_frame_never_clicks(self):
        action = {'action': 'click', 'x': 250, 'y': 150}
        cases = [({**self.target, 'process': 'outlook.exe'}, 11, self.frame),
                 ({**self.target, 'pid': 42}, 11, self.frame),
                 ({**self.target, 'rect': [0, 0, 1000, 600]}, 11, self.frame),
                 (self.target, 22, self.frame),
                 (self.target, 11, {**self.frame, 'captured': time.monotonic() - 90})]
        for target, foreground, frame in cases:
            with patch.object(computer, 'require_desktop'), patch.object(computer, 'window_info', return_value=target), \
                 patch.object(computer, 'USER') as user, patch.object(computer, 'send_inputs') as send:
                user.GetForegroundWindow.return_value = foreground
                with self.assertRaises(RuntimeError):
                    computer.apply_action(self.target, frame, action)
                send.assert_not_called()

    def test_local_stop_only_cancels_pc_tasks(self):
        controller = Controller(queue.Queue())
        for text in ('Jarvis stop', 'Stop the PC task', 'Stop clicking', 'Please cancel that computer task'):
            self.assertTrue(computer.stop_requested(text), text)
        for text in ('Stop the timer', 'Stop talking', 'Cancel my email search'):
            self.assertFalse(computer.stop_requested(text), text)
        jobs = [{'id':'pc1','scope':'computer','status':'running'}, {'id':'pc2','scope':'computer','status':'completed'}]
        with patch.object(controller.research, 'list', return_value=jobs) as listing, patch.object(controller.research, 'cancel') as cancel:
            self.assertTrue(controller.stop_computer_tasks())
            listing.assert_called_once_with('computer')
            cancel.assert_called_once_with('pc1', 'computer')

    def test_valid_click_uses_fresh_target_and_virtual_desktop_coordinates(self):
        with patch.object(computer, 'require_desktop'), patch.object(computer, 'window_info', return_value=self.target), \
             patch.object(computer, 'USER') as user, patch.object(computer, 'send_inputs') as send:
            user.GetForegroundWindow.return_value = 11
            user.GetAncestor.return_value = 11
            user.GetSystemMetrics.side_effect = lambda key: {76:-1000, 77:0, 78:2920, 79:1080}[key]
            user.GetCursorPos.side_effect = lambda pointer: setattr(pointer._obj, 'x', 100)
            computer.apply_action(self.target, self.frame, {'action': 'click', 'x': 250, 'y': 150})
            self.assertEqual(send.call_count, 2)
            movement = send.call_args_list[0].args[0].data.mi
            self.assertEqual(movement.dx, round(500 * 65535 / 2919))
            self.assertEqual([item.data.mi.dwFlags for item in send.call_args_list[1].args], [2, 4])

    def test_computer_job_runs_in_background_and_keeps_screenshot_private(self):
        ready, release = threading.Event(), threading.Event()
        def run(job, update, check, ask):
            update(job, stage='looking', _frame={'image': 'private-pixels', 'captured': 123})
            ready.set()
            release.wait(2)
            check(job)
            update(job, status='completed', summary='Verified, sir.', finished=time.time())
        pc = Mock()
        pc.run.side_effect = run
        jobs = ResearchJobs(queue.Queue(), Mock(), pc)
        with patch('desktop.research.local_request', return_value={'conversationModel':'gpt-6-sol', 'resolvedSearchModel':'gpt-6-luna'}):
            try:
                started = jobs.start('Turn off Proton VPN', 'computer')
                self.assertTrue(ready.wait(2))
                self.assertEqual(jobs.status(started['id'])['model'], 'gpt-6-sol')
                self.assertNotIn('_frame', jobs.status(started['id']))
                self.assertNotIn('_foreground', jobs.status(started['id']))
                self.assertEqual(jobs.frame(started['id'])['image'], 'private-pixels')
                self.assertFalse(jobs.status(started['id'], 'web')['ok'])
                release.set()
                jobs.pending.join()
                self.assertEqual(jobs.status(started['id'])['status'], 'completed')
            finally:
                release.set()
                jobs.close()


@patch('desktop.controller.classify_request', lambda *_args: {'action': 'normal'})
class SpeechAndModelTests(unittest.TestCase):
    def test_whisper_request_uses_vibecode_key_and_memory_wav(self):
        response = Mock(status_code=200)
        response.json.return_value = {'text':' Jarvis, switch to Astra. '}
        with patch.object(speech, 'groq_key', return_value='test-secret'), patch.object(speech.requests, 'post', return_value=response) as post:
            self.assertEqual(speech.transcribe_pcm(b'\0\0' * 1600), 'Jarvis, switch to Astra.')
        self.assertEqual(post.call_args.args, (speech.ENDPOINT,))
        request = post.call_args.kwargs
        self.assertFalse(request['allow_redirects'])
        self.assertEqual(request['data']['model'], 'whisper-large-v3')
        self.assertEqual(request['headers']['Authorization'], 'Bearer test-secret')
        with wave.open(io.BytesIO(request['files']['file'][1]), 'rb') as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
            self.assertEqual(wav.getnframes(), 1600)

    def test_groq_errors_are_explicit_and_never_replay_provider_secrets(self):
        for status, expected in ((401,'rejected'), (403,'network'), (429,'rate limit')):
            response = Mock(status_code=status, text='test-secret')
            with patch.object(speech, 'groq_key', return_value='test-secret'), patch.object(speech.requests, 'post', return_value=response):
                with self.assertRaises(RuntimeError) as error:
                    speech.transcribe_pcm(b'\0\0' * 160)
                self.assertIn(expected, str(error.exception))
                self.assertNotIn('test-secret', str(error.exception))

    def test_failed_whisper_exposes_error_without_local_action_fallback(self):
        controller = Controller(queue.Queue())
        listener = Listener(controller, {'whisperModel':'whisper-large-v3-turbo'})
        with patch('desktop.listener.transcribe_pcm', side_effect=RuntimeError('Groq network denied')) as transcribe:
            with self.assertRaisesRegex(RuntimeError, 'Groq network denied'):
                listener.recognize(b'\0\0' * 100)
            self.assertEqual(controller.speech_error, 'Groq network denied')
            self.assertEqual(transcribe.call_args.args[1], 'whisper-large-v3-turbo')
            self.assertFalse(listener.transcribing)
            self.assertTrue(listener.reset.is_set())
        with patch('desktop.listener.transcribe_pcm', return_value='Switch to Astra'):
            self.assertEqual(listener.recognize(b'\0\0' * 100), 'Switch to Astra')
            self.assertIsNone(controller.speech_error)

    def test_dpapi_roundtrip_with_vibecode_entropy(self):
        sealed = protect(b'non-secret-fixture', entropy=speech.ENTROPY)
        self.assertEqual(protect(sealed, decrypt=True, entropy=speech.ENTROPY), b'non-secret-fixture')
        with self.assertRaises(OSError):
            protect(sealed, decrypt=True)

    def test_explicit_model_commands_preserve_other_choice(self):
        catalog = {'models':[{'id':'gpt-6.1-sol','name':'GPT 6.1 Sol'}, {'id':'gpt-6-astra','name':'GPT 6 Astra'},
                             {'id':'gpt-6-luna','name':'GPT 6 Luna'}], 'conversationModel':'gpt-6-luna', 'searchModel':'same'}
        with patch('desktop.model_commands.local_request', return_value=catalog) as request:
            self.assertTrue(switch_model('search', 'Astra')['ok'])
            self.assertEqual(request.call_args.args[2], {'conversationModel':'gpt-6-luna','searchModel':'gpt-6-astra'})
            request.reset_mock()
            self.assertFalse(switch_model('conversation', 'imaginary model')['ok'])
            self.assertEqual(request.call_count, 1)

    def test_model_switch_is_an_ai_selected_tool_with_current_permission(self):
        controller = Controller(queue.Queue())
        controller.tasks = None
        def model(*_args):
            result = controller.tool(controller.request['id'], 'select_model', {'scope':'conversation','model':'Astra'})
            return {'output':[{'type':'message','content':[{'type':'output_text','text':result['text']}]}]}
        with patch('desktop.controller.classify_request', return_value=decision('select_model')),              patch('desktop.controller.switch_model', return_value={'ok':True,'text':'Switched to Astra, sir.'}) as switch,              patch.object(controller, 'ask_model', side_effect=model):
            answer = controller.respond('Switch to Astra', speak=False)
            self.assertTrue(answer['ok'],answer)
            switch.assert_called_once_with('conversation', 'Astra')


if __name__ == '__main__':
    unittest.main()
