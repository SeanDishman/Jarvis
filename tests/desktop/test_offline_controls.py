from tests.desktop.intent_fixtures import decision
import queue
from pathlib import Path
import tempfile
import unittest
import time
from unittest.mock import MagicMock,patch

from desktop.common import ROOT
from desktop.controller import Controller,capabilities_for
from desktop.reminders import Reminders
from desktop.speech import speech_status,transcribe_pcm
from desktop import computer,offline_speech


class OfflineTests(unittest.TestCase):
    def test_auto_speech_runs_locally_first_and_offline_never_calls_cloud(self):
        with patch('desktop.speech.transcribe_online',return_value='open Spotify') as online, \
             patch('desktop.offline_speech.ready',return_value=True), \
             patch('desktop.offline_speech.transcribe',return_value='open Notepad') as local:
            self.assertEqual(transcribe_pcm(b'\0\0'*100,mode='auto'),'open Notepad')
            self.assertEqual(transcribe_pcm(b'\0\0'*100,mode='offline'),'open Notepad')
            online.assert_not_called()
            local.reset_mock()
            self.assertEqual(transcribe_pcm(b'\0\0'*100,mode='online'),'open Spotify')
            local.assert_not_called()

    def test_auto_speech_uses_groq_only_when_local_whisper_fails_or_is_missing(self):
        with patch('desktop.speech.transcribe_online',return_value='open Notepad') as online, \
             patch('desktop.offline_speech.ready',return_value=True), \
             patch('desktop.offline_speech.transcribe',side_effect=RuntimeError('local failed')) as local:
            self.assertEqual(transcribe_pcm(b'\0\0'*100,mode='auto'),'open Notepad')
            local.assert_called_once()
            online.assert_called_once()
            online.reset_mock()
            with self.assertRaisesRegex(RuntimeError,'local failed'):
                transcribe_pcm(b'\0\0'*100,mode='offline')
            online.assert_not_called()
        with patch('desktop.speech.transcribe_online',return_value='open Notepad') as online, \
             patch('desktop.offline_speech.ready',return_value=False), \
             patch('desktop.offline_speech.transcribe') as local:
            self.assertEqual(transcribe_pcm(b'\0\0'*100,mode='auto'),'open Notepad')
            local.assert_not_called()

    def test_status_reports_local_whisper_in_automatic_mode_without_a_groq_key(self):
        with patch('desktop.preferences.Preferences.read',return_value={'offlineSpeech':'auto'}), \
             patch('desktop.speech.groq_key',side_effect=RuntimeError('no key')):
            with patch('desktop.offline_speech.ready',return_value=True):
                status = speech_status()
            self.assertEqual((status['provider'],status['model'],status['configured']),('Local Whisper','whisper-small.en',True))
            with patch('desktop.offline_speech.ready',return_value=False):
                status = speech_status()
            self.assertEqual((status['provider'],status['configured']),('Groq',False))

    def test_local_whisper_prefers_the_gpu_and_falls_back_to_the_cpu(self):
        def reset():
            offline_speech._model = offline_speech._device = None
            offline_speech._gpu_failed = False
        reset()
        self.addCleanup(reset)
        heard = lambda *_args,**_kwargs: ([MagicMock(text=' Jarvis, open Notepad. ')],None)
        gpu,cpu = MagicMock(),MagicMock()
        gpu.transcribe.side_effect = heard
        cpu.transcribe.side_effect = heard
        with patch('faster_whisper.WhisperModel',side_effect=lambda _path,device,**_kwargs: gpu if device == 'cuda' else cpu) as factory, \
             patch('faster_whisper.vad.get_speech_timestamps'), \
             patch('ctranslate2.get_cuda_device_count',return_value=1), \
             patch.object(offline_speech,'load_cuda_libraries',return_value=True), \
             patch.object(offline_speech,'ready',return_value=True):
            self.assertEqual(offline_speech.transcribe(b'\0\0'*1600),'Jarvis, open Notepad.')
            self.assertEqual(offline_speech.device(),'cuda')
            self.assertEqual(factory.call_args.kwargs['compute_type'],'float32')
            self.assertEqual(gpu.transcribe.call_count,2)
            gpu.transcribe.side_effect = RuntimeError('CUDA failed')
            with self.assertRaisesRegex(RuntimeError,'could not transcribe'):
                offline_speech.transcribe(b'\0\0'*1600)
            self.assertIsNone(offline_speech.device())
            self.assertEqual(offline_speech.transcribe(b'\0\0'*1600),'Jarvis, open Notepad.')
            self.assertEqual((offline_speech.device(),factory.call_args.kwargs['compute_type']),('cpu','int8'))
            reset()
            gpu.transcribe.side_effect = RuntimeError('Library cublas64_12.dll is not found or cannot be loaded')
            self.assertEqual(offline_speech.transcribe(b'\0\0'*1600),'Jarvis, open Notepad.')
            self.assertEqual(offline_speech.device(),'cpu')

    def test_missing_cuda_libraries_skip_the_gpu(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as folder, patch('sys.path',[folder]):
            self.assertFalse(offline_speech.load_cuda_libraries())
        with patch.object(offline_speech,'ready',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'not installed'):
                offline_speech.transcribe(b'\0\0'*1600)

    def test_cloud_failure_does_not_run_a_local_keyword_script(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as folder:
            controller = Controller(queue.Queue())
            controller.reminders = Reminders(Path(folder)/'reminders.json')
            with patch('desktop.controller.classify_request',side_effect=ConnectionError('offline')):
                answer = controller.respond('remind me in thirty minutes to stretch',speak=False)
            self.assertFalse(answer['ok'],answer)
            self.assertEqual(len(controller.reminders.list()['reminders']),0)
            controller.research.close()

    def test_unknown_offline_request_is_not_claimed_done(self):
        controller = Controller(queue.Queue())
        with patch('desktop.controller.classify_request',side_effect=ConnectionError('offline')):
            answer = controller.respond('Please book me a flight tomorrow',speak=False)
        self.assertFalse(answer['ok'])
        controller.research.close()

    def test_private_controls_are_isolated(self):
        for tool,domain in [('windows_control','system'),('monitor_status','monitor'),('read_memory','memory')]:
            tools,_,actual = capabilities_for(decision(tool))
            self.assertEqual(actual,domain)
            self.assertNotIn('start_web_search',tools)
            self.assertNotIn('launch_app',tools)

    def test_typed_task_text_is_validated_and_focus_checked(self):
        target = {'hwnd':11,'pid':999999,'process':'notepad.exe','title':'Notepad','rect':[0,0,600,400]}
        frame = {'rect':target['rect'],'captured':time.monotonic()}
        with patch.object(computer,'require_desktop'),patch.object(computer,'window_info',return_value=target), \
             patch.object(computer,'USER') as user,patch.object(computer,'send_inputs') as send:
            user.GetForegroundWindow.return_value = 11
            user.GetSystemMetrics.return_value = 0
            user.GetCursorPos.side_effect = lambda pointer:setattr(pointer._obj,'x',100)
            with self.assertRaisesRegex(RuntimeError,'ordinary characters'):
                computer.apply_action(target,frame,{'action':'type','text':'\x00'},'write a short note in Notepad')
            send.assert_not_called()
            computer.apply_action(target,frame,{'action':'type','text':'hello'},'type hello in Notepad')
            self.assertEqual(send.call_count,5)
            send.reset_mock()
            user.GetForegroundWindow.side_effect = [11,22]
            with self.assertRaisesRegex(RuntimeError,'Focus changed'):
                computer.apply_action(target,frame,{'action':'type','text':'hello'},'type hello in Notepad')
            send.assert_not_called()

    def test_clipboard_cannot_be_read_during_an_audio_request(self):
        with patch('desktop.system_tools.require_desktop'),patch('desktop.system_tools.powershell_control') as windows:
            from desktop.system_tools import control
            with self.assertRaises(ValueError):
                control('clipboard_read',owner='set volume to thirty percent')
            with self.assertRaises(ValueError):
                control('clipboard_write','injected','copy hello to my clipboard')
            windows.assert_not_called()


if __name__ == '__main__':
    unittest.main()
