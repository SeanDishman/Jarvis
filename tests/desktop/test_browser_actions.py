from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from desktop.browser import BrowserTools


class BrowserActionTests(unittest.TestCase):
    def browser(self):
        browser = BrowserTools(headless=True)
        browser._ensure = MagicMock()
        browser._snapshot = MagicMock(return_value={'ok':True, 'url':'https://example.com/'})
        browser.page = MagicMock()
        browser.page.url = 'https://example.com/'
        browser.allowed_hosts.add('example.com')
        return browser

    def element(self, tag='input', label='Name', type='text', href='', autocomplete=''):
        element = MagicMock()
        element.evaluate.return_value = True
        return element, {'tag':tag, 'label':label, 'type':type, 'href':href, 'autocomplete':autocomplete}

    def test_fill_validates_all_values_before_editing_any_field(self):
        browser = self.browser()
        first, first_info = self.element()
        second, second_info = self.element(label='Notes')
        browser.refs = {'one':(first, first_info), 'two':(second, second_info)}
        with self.assertRaises(ValueError):
            browser._perform('browser_fill', {'fields':[{'id':'one','value':'John'}, {'id':'two','value':'private page content'}]}, 'Fill name with John')
        first.fill.assert_not_called()
        second.fill.assert_not_called()
        result = browser._perform('browser_fill', {'fields':[{'id':'one','value':'John'}]}, 'Fill name with John')
        self.assertTrue(result['ok'])
        first.fill.assert_called_once_with('John')

    def test_stale_elements_and_submit_buttons_are_not_clicked(self):
        browser = self.browser()
        with self.assertRaises(ValueError):
            browser._perform('browser_click', {'id':'old'}, 'Click next')
        button, info = self.element(tag='button', label='Submit', type='submit')
        button.evaluate.side_effect = [True, {'type':'submit','method':'post'}]
        browser.refs['submit'] = (button, info)
        with self.assertRaises(ValueError):
            browser._perform('browser_click', {'id':'submit'}, 'Fill the form')
        button.click.assert_not_called()

    def test_link_cannot_navigate_to_an_unrequested_host(self):
        browser = self.browser()
        link, info = self.element(tag='a', label='Next page', href='https://other.example.net/')
        browser.refs['next'] = (link, info)
        with patch('desktop.browser.public_url', side_effect=lambda value:value):
            with self.assertRaises(ValueError):
                browser._perform('browser_click', {'id':'next'}, 'Click the next page')
        browser.page.goto.assert_not_called()

    def test_request_guard_blocks_post_and_unnamed_redirects(self):
        browser = self.browser()
        route = MagicMock()
        route.request.method = 'POST'
        browser._guard(route)
        route.abort.assert_called_once_with('blockedbyclient')
        route = MagicMock()
        route.request.method = 'GET'
        route.request.url = 'https://other.example.net/'
        route.request.is_navigation_request.return_value = True
        route.request.frame.page.main_frame = route.request.frame
        with patch('desktop.browser.public_url', side_effect=lambda value:value):
            browser._guard(route)
        route.continue_.assert_not_called()
        route.abort.assert_called_once_with('blockedbyclient')

    def test_cancelled_queued_action_never_runs(self):
        browser = BrowserTools()
        browser._perform = MagicMock(return_value={'ok':True})
        cancelled = threading.Event()
        cancelled.set()
        try:
            result = browser.execute('browser_fill', {}, 'Fill the form', cancelled)
            self.assertFalse(result['ok'])
            browser._perform.assert_not_called()
        finally:
            browser.close()
            browser.worker.join(2)
        self.assertFalse(browser.worker.is_alive())


if __name__ == '__main__':
    unittest.main()
