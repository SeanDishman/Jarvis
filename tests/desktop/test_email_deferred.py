from tests.desktop.intent_fixtures import decision
import queue
import unittest
from unittest.mock import patch

from desktop import email_policy, mail
from desktop.controller import Controller
from desktop.research import ResearchJobs


@patch('desktop.email_policy.enabled', lambda: False)
@patch('desktop.controller.classify_request', lambda *_args: decision('search_email','read_email'))
class DeferredEmailTests(unittest.TestCase):
    def test_email_questions_reply_immediately_without_model_or_search(self):
        controller = Controller(queue.Queue())
        with patch('desktop.controller.local_request') as model, patch.object(controller.research, 'start') as search:
            for question in ('Read my emails', 'Check my mail', 'Do you have access to my email?', 'Find receipt emails in Gmail',
                             'Search my Outlook inbox', 'How is the email search going?', 'Read the first one'):
                result = controller.respond(question, speak=False)
                self.assertTrue(result['ok'], question)
                self.assertEqual(result['text'], email_policy.UNAVAILABLE, question)
                self.assertEqual(result['tools'], [])
            model.assert_not_called()
            search.assert_not_called()

    def test_conversation_moves_on_after_unavailable_email(self):
        controller = Controller(queue.Queue())
        response = {'output':[{'type':'message','content':[{'type':'output_text','text':'Four, sir.'}]}]}
        with patch('desktop.controller.classify_request', side_effect=[decision('search_email'),decision()]), patch('desktop.controller.local_request', return_value=response) as model:
            self.assertEqual(controller.respond('Check my mail', speak=False)['text'], email_policy.UNAVAILABLE)
            self.assertEqual(controller.respond('What is two plus two?', speak=False)['text'], 'Four, sir.')
            self.assertEqual(model.call_count, 1)
            self.assertEqual(model.call_args.args[2]['user'], controller.sessions['web'])

    def test_disabled_mail_never_reads_credentials_calls_providers_or_opens_outlook(self):
        adapter = mail.MailTools()
        reference = adapter.reference('gmail', 'existing-message')
        with patch('desktop.mail.read_secret') as secret, patch('desktop.mail.json_request') as network, \
             patch('desktop.mail.subprocess.run') as outlook:
            self.assertFalse(adapter.accounts()['enabled'])
            self.assertTrue(adapter.search()['disabled'])
            self.assertTrue(adapter.read(reference)['disabled'])
            self.assertTrue(mail.classic({'action':'search'})['disabled'])
            with self.assertRaisesRegex(RuntimeError, 'access to your email'):
                mail.connect('gmail')
            with self.assertRaisesRegex(RuntimeError, 'access to your email'):
                mail.api('outlook', '/me/messages')
            secret.assert_not_called()
            network.assert_not_called()
            outlook.assert_not_called()

    def test_disabled_mail_does_not_queue_a_background_job(self):
        jobs = ResearchJobs(queue.Queue(), mail.MailTools())
        with patch('desktop.research.local_request') as model:
            result = jobs.start('Summarize my latest emails', 'email')
            self.assertTrue(result['disabled'])
            self.assertEqual(jobs.list(), [])
            self.assertEqual(jobs.workers, [])
            model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
