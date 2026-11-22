import html
from pathlib import Path
import queue
import re
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from desktop import visuals
from desktop.controller import Controller
from desktop.visuals import Visuals, wrapped

DRAWING = '<!doctype html><title>t</title><h1>Amplitude "peak" & trough</h1><svg><path d="M0 0"/></svg><script>let a = 1 < 2;</script>'


class VisualPageTests(unittest.TestCase):
    """A drawing is saved as a real page and opened in the browser, never typed into one."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.opened = []
        preferences = SimpleNamespace(read=lambda: {'fileWriteRoots': [str(self.root)]})
        self.visuals = Visuals(preferences, opener=lambda path: self.opened.append(path) or 'opera',
                               clock=lambda: time.struct_time((2026, 11, 20, 13, 5, 9, 4, 324, 0)))

    def test_the_drawing_is_saved_in_jarvis_files_and_opened(self):
        result = self.visuals.show('  What amplitude  is ', DRAWING)
        path = self.root / 'Visuals' / '2026-11-20 130509 What-amplitude-is.html'
        self.assertEqual((result['ok'], result['saved'], result['opened'], result['openedIn'], result['title']),
                         (True, str(path), True, 'opera', 'What amplitude is'))
        self.assertEqual(self.opened, [path])
        again = self.visuals.show('What amplitude is', DRAWING)
        self.assertTrue(again['saved'].endswith('What-amplitude-is 2.html'), 'An earlier drawing is never overwritten')

    def test_the_page_shows_the_drawing_in_a_frame_that_cannot_reach_out(self):
        page = wrapped('Wave "A" <1>', DRAWING)
        self.assertIn("default-src 'none'", page)
        self.assertNotIn('http', page.replace('http-equiv', ''), 'Nothing in the wrapper points at the network')
        frame = re.search(r'<iframe sandbox="([^"]*)" title="([^"]*)" srcdoc="([^"]*)">', page)
        self.assertEqual(frame.group(1), 'allow-scripts', 'Scripts may run; navigation, pop-ups, forms and same-origin access may not')
        self.assertEqual(html.unescape(frame.group(3)), DRAWING, 'The drawing arrives intact, quotes and all')
        self.assertIn('<title>Wave &quot;A&quot; &lt;1&gt;</title>', page)

    def test_what_is_not_a_drawing_is_refused_and_nothing_is_opened(self):
        for title, document, message in (('', DRAWING, 'short title'), ('x' * 121, DRAWING, 'short title'),
                                         ('Wave', 'just words', 'HTML document'), ('Wave', None, 'HTML document'),
                                         ('Wave', '<p>' + 'a' * visuals.LIMIT, 'too large')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.visuals.show(title, document)
        self.assertEqual((self.opened, list(self.root.iterdir())), ([], []))

    def test_a_page_that_cannot_be_opened_is_reported_with_where_it_was_saved(self):
        def broken(_path):
            raise OSError('no browser')
        result = Visuals(SimpleNamespace(read=lambda: {'fileWriteRoots': [str(self.root)]}), opener=broken).show('Wave', DRAWING)
        self.assertEqual((result['ok'], result['opened']), (False, False))
        self.assertTrue(Path(result['saved']).is_file())

    def test_the_default_browser_opens_the_page_by_its_address(self):
        page = self.root / 'a page.html'
        page.write_text('x', encoding='utf-8')
        with patch.object(visuals, 'default_browser', return_value=r'C:\Programs\Opera GX\opera.exe'), \
             patch.object(visuals.subprocess, 'Popen') as launch:
            self.assertEqual(visuals.open_page(page), 'opera')
        self.assertEqual(launch.call_args.args[0], [r'C:\Programs\Opera GX\opera.exe', page.as_uri()])
        with patch.object(visuals, 'default_browser', return_value=None), patch.object(visuals.os, 'startfile', create=True) as start:
            visuals.open_page(page)
        start.assert_called_once_with(str(page))

    def test_jarvis_draws_through_the_tool_in_the_same_conversation(self):
        controller = Controller(queue.Queue())
        self.assertEqual(controller.sessions['visuals'], controller.sessions['web'], 'Draw that follows what was just discussed')
        with patch.object(controller.visuals, 'show', return_value={'ok': True}) as show:
            controller.execute_tool({'capabilities': ['show_visual']}, 'show_visual', {'title': 'Wave', 'html': DRAWING})
        show.assert_called_once_with('Wave', DRAWING)


if __name__ == '__main__':
    unittest.main()
