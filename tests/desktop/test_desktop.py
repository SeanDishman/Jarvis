from tests.desktop.intent_fixtures import decision
import base64
import json
import pathlib
import queue
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
from desktop.controller import Controller, capabilities_for, LAUNCH
from desktop.listener import split_wake, prefer_app_transcript
from desktop.mail import MailTools, plain_body
from desktop.web_tools import validate_url
from desktop.windows import AppCatalog, preferred_monitor


@patch('desktop.controller.classify_request', lambda *_args: {'action': 'normal'})
class DesktopTests(unittest.TestCase):
    def catalog(self):
        catalog = AppCatalog()
        catalog.apps = [{'Name':'Notepad','AppID':'notepad-id'}, {'Name':'Notepad++','AppID':'notepad-plus-id'},
                        {'Name':'Google Chrome','AppID':'Chrome'}, {'Name':'Outlook (classic)','AppID':'classic-id'}]
        return catalog

    def test_similar_app_names_do_not_collide(self):
        apps = self.catalog()
        self.assertEqual(apps.match('Notepad')[0]['AppID'],'notepad-id')
        self.assertEqual(len(apps.match('Notepad')),1)
        self.assertEqual(apps.match('Notepad++')[0]['AppID'],'notepad-plus-id')
        self.assertEqual(apps.match('Chrome')[0]['AppID'],'Chrome')

    def test_launch_uses_catalog_and_never_shell_arguments(self):
        apps = self.catalog()
        with patch('desktop.windows.os.startfile') as launch:
            self.assertTrue(apps.launch('Chrome')['ok'])
            launch.assert_called_once_with('shell:AppsFolder\\Chrome')
            self.assertFalse(apps.launch('Chrome; powershell malicious')['ok'])
            self.assertEqual(launch.call_count,1)

    def test_polite_launch_requests(self):
        self.assertEqual(LAUNCH.fullmatch('Could you open Notepad for me').group(1),'Notepad')
        self.assertEqual(LAUNCH.fullmatch('Please launch Outlook classic').group(1),'Outlook classic')

    def test_wake_is_only_at_start(self):
        self.assertEqual(split_wake('hey jarvis open notepad'),(True,'open notepad'))
        self.assertEqual(split_wake('jarvis'),(True,''))
        self.assertEqual(split_wake('javis'),(True,''))
        self.assertEqual(split_wake('we were talking about jarvis'),(False,''))
        self.assertEqual(split_wake('jarvison is here'),(False,''))

    def test_email_context_cannot_invoke_web_or_launch(self):
        capabilities,_,domain = capabilities_for(decision('search_email','read_email'))
        self.assertEqual(domain,'email')
        self.assertNotIn('start_web_search',capabilities)
        self.assertNotIn('web_search_status',capabilities)
        self.assertNotIn('launch_app',capabilities)
        self.assertIn('read_email',capabilities_for(decision('read_email'))[0])
        self.assertIn('start_web_search',capabilities_for(decision('start_web_search'))[0])

    def test_constrained_speech_does_not_turn_conversation_into_actions(self):
        self.assertEqual(prefer_app_transcript('jarvis open know but','jarvis open notepad'),'jarvis open notepad')
        self.assertEqual(prefer_app_transcript('tell me about notepads','open notepad'), 'tell me about notepads')
        self.assertEqual(prefer_app_transcript('jarvis what is the weather','jarvis open chrome'),'jarvis what is the weather')

    def test_email_reply_and_send_are_unavailable(self):
        controller = Controller(queue.Queue())
        controller.request = {'id':'mail','capabilities':['email_accounts','search_email','read_email'],'appQuery':None,'executed':[]}
        for operation in ('reply_email','send_email','delete_email','mark_read','forward_email'):
            self.assertFalse(controller.tool('mail',operation,{'id':'message'})['ok'])

    @patch('desktop.email_policy.enabled', lambda: True)
    def test_mail_history_is_not_reused_by_web_requests(self):
        controller = Controller(queue.Queue())
        reply = {'output':[{'type':'message','content':[{'type':'output_text','text':'Ready, sir.'}]}]}
        controller.tasks = None
        with patch('desktop.controller.classify_request', side_effect=[decision('read_email'),decision('get_weather'),decision('read_email')]), patch('desktop.controller.local_request',return_value=reply) as request:
            self.assertTrue(controller.respond('Read my Gmail inbox',speak=False)['ok'])
            self.assertTrue(controller.respond('What is the weather online?',speak=False)['ok'])
            self.assertTrue(controller.respond('Read the first Gmail email',speak=False)['ok'])
        requests = [call.args[2] for call in request.call_args_list]
        self.assertNotEqual(requests[0]['user'],requests[1]['user'])
        self.assertEqual(requests[0]['user'],requests[2]['user'])
        self.assertNotIn('start_web_search',requests[0]['capabilities'])
        self.assertNotIn('read_email',requests[1]['capabilities'])

    def test_background_email_results_cannot_enter_web_context(self):
        controller = Controller(queue.Queue())
        controller.request = {'id':'web','capabilities':['start_web_search','web_search_status'],'appQuery':None,'executed':[]}
        self.assertFalse(controller.tool('web','email_search_status',{'id':''})['ok'])
        self.assertFalse(controller.tool('web','start_email_search',{'question':'Read emails'})['ok'])

    @patch('desktop.email_policy.enabled', lambda: True)
    def test_classic_disconnected_mailbox_is_not_reported_as_empty(self):
        mail = MailTools()
        missing = {'ok':False,'messages':[],'error':'Not connected in Outlook classic: gmail'}
        with patch('desktop.mail.secret_path') as path, patch('desktop.mail.classic_available',return_value=True), patch('desktop.mail.classic',return_value=missing):
            path.return_value.exists.return_value = False
            result = mail.search('gmail')
        self.assertFalse(result['ok'])
        self.assertIn('Not connected',result['errors'][0])

    def test_tool_host_rejects_unrequested_action(self):
        controller = Controller(queue.Queue())
        controller.apps = self.catalog()
        controller.request = {'id':'current','capabilities':['search_email'],'appQuery':None,'executed':[]}
        self.assertFalse(controller.tool('current','launch_app',{'name':'Notepad'})['ok'])
        self.assertFalse(controller.tool('wrong','search_email',{})['ok'])
        controller.request['capabilities'] = ['launch_app']
        controller.request['appQuery'] = 'Notepad'
        self.assertFalse(controller.tool('current','launch_app',{'name':'unknown.exe --bad-argument'})['ok'])
        with patch.object(controller.apps,'launch',return_value={'ok':True}) as launch:
            self.assertTrue(controller.tool('current','launch_app',{'name':'Notepad'})['ok'])
            launch.assert_called_once_with('Notepad')

    def test_private_web_targets_rejected(self):
        for address in ['http://127.0.0.1/a','http://192.168.1.2','http://localhost','file:///C:/secret','https://user:secret@example.com','http://thing.local']:
            with self.assertRaises(ValueError,msg=address): validate_url(address)
        self.assertEqual(validate_url('https://www.example.com/path'),'https://www.example.com/path')

    def test_mail_body_prefers_text_and_ignores_attachments(self):
        encode=lambda text:base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')
        payload={'parts':[{'mimeType':'text/html','body':{'data':encode('<b>HTML copy</b>')}},
                          {'mimeType':'text/plain','body':{'data':encode('Plain body')}},
                          {'filename':'secret.txt','mimeType':'text/plain','body':{'data':encode('attachment')}}]}
        self.assertEqual(plain_body(payload),'Plain body')

    @patch('desktop.email_policy.enabled', lambda: True)
    def test_mail_read_requires_prior_search_reference(self):
        mail = MailTools()
        self.assertFalse(mail.read('invented-id')['ok'])
        reference = mail.reference('gmail','real-message-id')
        payload={'payload':{'mimeType':'text/plain','body':{'data':'SGVsbG8'},'headers':[{'name':'Subject','value':'Test'}]}}
        with patch('desktop.mail.api',return_value=payload) as api:
            self.assertEqual(mail.read(reference)['body'],'Hello')
            api.assert_called_once_with('gmail','/messages/real-message-id',{'format':'full'})

    def test_laptop_monitor_preferred_over_external_primary(self):
        external={'device':'external','primary':True,'internal':False}
        laptop={'device':'internal','primary':False,'internal':True}
        self.assertEqual(preferred_monitor([external,laptop]),laptop)
        self.assertEqual(preferred_monitor([external,laptop],'external'),external)


if __name__=='__main__':
    unittest.main()
