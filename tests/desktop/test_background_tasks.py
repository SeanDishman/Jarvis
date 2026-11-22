import json
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from desktop.controller import Controller, capabilities_for
from tests.desktop.intent_fixtures import decision
from desktop.browser import browser_backend, opera_executable
from desktop.acknowledgements import acknowledge, FALLBACK_ACKNOWLEDGEMENT


def response(text):
    return {'model':'openai/gpt-6-luna','reasoning':'low', 'output':[{'type':'message','content':[{'type':'output_text','text':text}]}]}


def normal(*_args):
    return {'action':'normal', 'model_tier':'default', 'needs_clarification':False}


class BackgroundTaskTests(unittest.TestCase):
    def controller(self):
        c = Controller(queue.Queue())
        c.breaks = SimpleNamespace(active=False)
        c.preferences.read = lambda: {'offlineSpeech':'automatic'}
        self.addCleanup(c.browser.close)
        self.addCleanup(c.research.close)
        self.addCleanup(c.tasks.close)
        return c

    def finish(self, c, identifier):
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            result = c.activity_status(identifier)
            if result['status'] in ('completed','failed','cancelled'):
                return result
            time.sleep(.01)
        self.fail('Task did not complete')

    def test_semantic_selection_precedes_ack_and_browser_does_not_block_chat(self):
        c = self.controller()
        order = []
        browser_started, release_browser = threading.Event(), threading.Event()
        requests = []
        acknowledgement = "I'll open the site, sir."
        def classify(text, *_args):
            order.append('classify')
            return decision('browser_navigate') if text == 'Open example.com' else decision()
        def model(_port, _path, payload=None, **_kwargs):
            if payload is None: return {}
            if payload.get('model') == 'openclaw/desktop' and 'Open example.com' in payload.get('input',''):
                requests.append(payload['desktopRequestId'])
                result = c.tool(payload['desktopRequestId'], 'browser_navigate', {'url':'https://example.com/'})
                self.assertTrue(result['ok'], result)
                return response('The site is open, sir.')
            return response('Four, sir.')
        def browser(*_args, **_kwargs):
            browser_started.set()
            self.assertTrue(release_browser.wait(3))
            return {'ok':True,'url':'https://example.com/'}
        def ack(*_args):
            order.append('ack')
            return acknowledgement
        with patch.object(c, 'speak', side_effect=lambda _text:order.append('speak')),              patch('desktop.controller.acknowledge', side_effect=ack),              patch('desktop.controller.classify_request', side_effect=classify) as classify_mock,              patch('desktop.controller.local_request', side_effect=model),              patch.object(c.browser, '_perform', side_effect=browser):
            try:
                started = c.submit('Open example.com', speak=True)
                self.assertEqual(started['text'], acknowledgement)
                self.assertEqual(order[:3], ['classify','ack','speak'])
                self.assertTrue(browser_started.wait(2))
                self.assertEqual(classify_mock.call_count, 1)
                self.assertFalse(c.busy.locked())
                self.assertEqual(c.submit('What is two plus two?', speak=False)['text'], 'Four, sir.')
                c.cancel()
                self.assertEqual(c.activity_status(started['taskId'])['status'], 'running')
                release_browser.set()
                done = self.finish(c, started['taskId'])
                self.assertEqual(done['status'], 'completed', done)
                self.assertFalse(c.tool(requests[0], 'browser_navigate', {'url':'https://example.com/'})['ok'])
            finally:
                release_browser.set()
                c.tasks.pending.join()

    def test_cancelling_during_classification_prevents_dispatch(self):
        c = self.controller()
        classifying, release = threading.Event(), threading.Event()
        result = []
        def classify(*_args):
            classifying.set()
            release.wait(3)
            return decision('list_apps','launch_app')
        with patch('desktop.controller.acknowledge') as ack,              patch('desktop.controller.classify_request', side_effect=classify),              patch('desktop.controller.local_request') as model:
            worker = threading.Thread(target=lambda: result.append(c.submit('Open Opera GX', speak=False)))
            try:
                worker.start()
                self.assertTrue(classifying.wait(2))
                c.cancel()
                release.set()
                worker.join(3)
                self.assertEqual(result[0]['error'], 'Cancelled')
                ack.assert_not_called()
                model.assert_not_called()
                self.assertEqual(c.tasks.list(), [])
            finally:
                release.set()
                worker.join(3)

    def test_malformed_generated_acknowledgement_uses_short_fallback(self):
        for text in ('', 'First line.\nSecond line.', 'too long ' * 25):
            with self.subTest(text=text), patch('desktop.acknowledgements.local_request', return_value=response(text)):
                self.assertEqual(acknowledge('Can you see the drivers on my PC?', 'fixture-ack'), FALLBACK_ACKNOWLEDGEMENT)

    def test_browser_selection_is_explicit_and_never_silently_falls_back(self):
        self.assertEqual(browser_backend('Opera GX'), 'opera_gx')
        for name in ('Firefox','Brave','Unlisted browser'):
            with self.assertRaisesRegex(ValueError, 'do not substitute'):
                browser_backend(name)
        with patch('desktop.browser.Path.is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'will not substitute'):
                opera_executable('opera_gx')
