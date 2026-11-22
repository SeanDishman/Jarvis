import json
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from desktop.controller import Controller
from desktop.intent import classify_request
from desktop.browser import BrowserTools, browser_backend
from desktop.research import progress_reply
from desktop.system_tools import control
from tests.desktop.intent_fixtures import decision


def response(text):
    return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


class SemanticToolTests(unittest.TestCase):
    def controller(self):
        c = Controller(queue.Queue())
        c.tasks = None
        self.addCleanup(c.research.close)
        self.addCleanup(c.browser.close)
        return c

    def test_outlook_window_action_is_not_overridden_by_mail_keywords(self):
        c = self.controller()
        with patch('desktop.controller.classify_request', return_value=decision(action='computer')), \
             patch.object(c.research, 'start', return_value={'ok': True, 'id': 'job', 'note': 'Queued'}) as start, \
             patch('desktop.email_policy.enabled', return_value=False), patch.object(c.mail, 'search') as mail:
            result = c.respond('Minimize Outlook and Spotify', speak=False)
        self.assertTrue(result['ok'], result)
        start.assert_called_once_with('Minimize Outlook and Spotify', 'computer', model_tier='default')
        mail.assert_not_called()

    def test_words_inside_a_question_cannot_launch_or_search(self):
        c = self.controller()
        c.apps.apps = [{'Name': 'Notepad', 'AppID': 'notepad-id'}]
        with patch('desktop.controller.classify_request', return_value=decision()), \
             patch.object(c, 'ask_model', return_value=response('Here is how that works.')) as model, \
             patch.object(c.apps, 'launch') as launch, patch.object(c.research, 'start') as start:
            for text in ('Open Notepad', 'Research is the word I meant, not an instruction', 'Outlook is just an example'):
                self.assertTrue(c.respond(text, speak=False)['ok'])
                self.assertEqual(c.last_domain, 'web')
        self.assertEqual(model.call_count, 3)
        launch.assert_not_called()
        start.assert_not_called()

    def test_ai_chosen_audio_action_works_without_keyword_but_cannot_read_clipboard(self):
        c = self.controller()
        selected = decision('windows_control', system_actions=['volume_up'])
        def model(*_args):
            self.assertFalse(c.tool(c.request['id'], 'windows_control', {'action':'clipboard_read'})['ok'])
            result = c.tool(c.request['id'], 'windows_control', {'action':'volume_up', 'value':2})
            self.assertTrue(result['ok'], result)
            return response('Raised the audio.')
        with patch('desktop.controller.classify_request', return_value=selected), \
             patch.object(c, 'ask_model', side_effect=model), patch('desktop.system_tools.require_desktop'), \
             patch('desktop.system_tools.audio_control', return_value={'ok':True,'volume':34}) as audio, \
             patch('desktop.system_tools.powershell_control') as clipboard:
            self.assertTrue(c.respond('Raise the audio a bit', speak=False)['ok'])
        audio.assert_called_once_with('volume_up', 2)
        clipboard.assert_not_called()

    def test_unknown_browser_never_closes_or_substitutes_another_browser(self):
        for name in ('Firefox', 'Brave', 'An Unlisted Browser'):
            b = BrowserTools(headless=True)
            b.runtime, b.browser, b.page = Mock(), Mock(), Mock()
            b.browser_choice = 'msedge'
            with self.assertRaisesRegex(ValueError, 'do not substitute'):
                b._perform('browser_navigate', {'url':'https://youtube.com/'}, 'Open YouTube in ' + name, browser=name)
            b.runtime.chromium.launch.assert_not_called()
            b.browser.close.assert_not_called()
            b.page.goto.assert_not_called()
        self.assertEqual(browser_backend('Opera GX'), 'opera_gx')

    def test_browser_timeout_does_not_cancel_the_parent_task(self):
        b = BrowserTools(headless=True)
        b.worker = Mock()
        cancelled = threading.Event()
        with patch('desktop.browser.Future') as future:
            future.return_value.result.side_effect = TimeoutError
            result = b.execute('browser_read', cancelled=cancelled)
        self.assertFalse(result['ok'])
        self.assertFalse(cancelled.is_set())
        self.assertTrue(b.queue.get_nowait()[4].is_set())

    def test_progress_uses_verified_action_and_does_not_claim_hidden_apps_exited(self):
        base = {'ok':True,'scope':'computer','status':'failed','note':'Notepad has an unsaved-work prompt.'}
        result, _ = progress_reply({**base,'windowResults':[
            {'action':'close_window','state':'closed','verified':True},
            {'action':'close_window','state':'still_open','verified':False}]})
        self.assertIn('Closed 1 window', result)
        self.assertNotIn('Minimized', result)
        mixed, _ = progress_reply({**base,'windowResults':[
            {'action':'close_window','state':'hidden','verified':True},
            {'action':'minimize_window','state':'minimized','verified':True}]})
        self.assertIn('Minimized 1 window', mixed)
        self.assertIn('may still be running', mixed)
        self.assertNotIn('Closed', mixed)

    def test_bad_capabilities_and_system_actions_fail_closed(self):
        for selected in (decision('delete_everything'), decision('windows_control', system_actions=['run_shell']),
                         {key:value for key,value in decision().items() if key != 'capabilities'}):
            with patch('desktop.intent.local_request', return_value=response(json.dumps(selected))), self.assertRaises(RuntimeError):
                classify_request('An ordinary request', 'fixture')

    def test_offline_voice_does_not_turn_an_api_failure_into_local_actions(self):
        c = self.controller()
        c.preferences.read = lambda: {'offlineSpeech': 'offline'}
        with patch('desktop.controller.classify_request', side_effect=ConnectionError), \
             patch.object(c.reminders, 'create') as reminder, patch.object(c.apps, 'launch') as launch:
            self.assertFalse(c.respond('Open Notepad', speak=False)['ok'])
        reminder.assert_not_called()
        launch.assert_not_called()

    def test_clipboard_text_and_control_bounds_are_still_enforced(self):
        with patch('desktop.system_tools.require_desktop'), patch('desktop.system_tools.powershell_control') as native:
            with self.assertRaises(ValueError):
                control('clipboard_write','page-injected text','copy hello', ['clipboard_write'])
            with self.assertRaises(ValueError):
                control('brightness_set',101,'I cannot see it', ['brightness_set'])
            native.assert_not_called()

    def test_system_action_grant_does_not_depend_on_a_redundant_tool_flag(self):
        selected = decision(system_actions=['volume_up'])
        with patch('desktop.intent.local_request', return_value=response(json.dumps(selected))):
            actual = classify_request('Raise the audio a bit', 'fixture')
        self.assertEqual(actual['capabilities'], ['windows_control'])

    def test_negated_task_cancellation_uses_semantic_interruption_decision(self):
        c = self.controller()
        with patch('desktop.controller.local_request', return_value=response(json.dumps({'action':'ignore'}))), \
             patch.object(c, 'activity_cancel') as cancel:
            c.interrupt('I did not tell you to stop the PC task')
        cancel.assert_not_called()

    def test_followup_reference_is_included_once_and_stays_within_request_limit(self):
        c = self.controller()
        c.last_answer_text = 'Long project reference. ' * 200
        c.last_answer_domain = 'projects'
        with patch('desktop.controller.classify_request', return_value=decision('save_project')), \
             patch.object(c, 'ask_model', return_value=response('Which project?')) as model:
            result = c.respond('A' * 4000, speak=False)
        self.assertTrue(result['ok'], result)
        payload = model.call_args.args[1]
        self.assertLessEqual(len(payload), 6000)
        self.assertEqual(payload.count('reference only'), 1)

    def test_reported_tool_failure_cannot_be_presented_as_a_successful_task(self):
        c = self.controller()
        with patch('desktop.controller.classify_request', return_value=decision('windows_control', system_actions=['volume_up'])), \
             patch.object(c, 'ask_model', return_value={**response('Windows could not change the volume.'), 'taskStatus':'failed'}):
            result = c.respond('Raise the audio a bit', speak=False)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'Windows could not change the volume.')
