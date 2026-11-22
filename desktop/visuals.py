"""Pages Jarvis draws for the owner: a diagram, chart or small demo written as one self-contained HTML document.

The document is the model's own writing, possibly shaped by what it read on the web, so it never runs as an ordinary
page. It is saved inside a wrapper that shows it in a sandboxed frame under a content policy: scripts may run for
animation and interaction, but nothing is loaded from or sent to the network, and the frame cannot navigate the
browser, open windows or submit forms.
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import time
from pathlib import Path

from desktop.common import ROOT

FOLDER = 'Visuals'
LIMIT = 400_000
POLICY = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; "
          "font-src data:; media-src data: blob:")


def wrapped(title, document):
    """The page that is saved: the drawing inside a sandboxed frame that fills the window."""
    name = html.escape(title, quote=True)
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
            f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{name}</title>\n'
            '<style>html,body{margin:0;height:100%;background:#fff}'
            'iframe{position:fixed;inset:0;width:100%;height:100%;border:0}</style>\n'
            f'</head><body><iframe sandbox="allow-scripts" title="{name}" srcdoc="{html.escape(document, quote=True)}"></iframe>'
            '</body></html>\n')


def default_browser():
    from desktop.app_identity import default_program
    for scheme in ('https', 'http'):
        program = default_program(scheme)
        if program and Path(program).is_file():
            return program
    return None


def open_page(path):
    """Open a saved page in the owner's default browser, as a page rather than in whatever edits HTML files."""
    program = default_browser()
    if program:
        subprocess.Popen([program, path.as_uri()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return Path(program).stem
    os.startfile(str(path))
    return 'the default app for web pages'


class Visuals:
    def __init__(self, preferences, opener=open_page, clock=time.localtime):
        self.preferences, self.opener, self.clock = preferences, opener, clock

    def folder(self):
        try:
            roots = self.preferences.read().get('fileWriteRoots') or []
        except (OSError, ValueError):
            roots = []
        return Path(roots[0] if roots else ROOT / 'Jarvis Files') / FOLDER

    def show(self, title, document):
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise ValueError('Give the visual a short title.')
        if not isinstance(document, str) or '<' not in document or '\x00' in document:
            raise ValueError('The visual must be an HTML document.')
        if len(document) > LIMIT:
            raise ValueError('The visual is too large. Draw it more simply.')
        title = ' '.join(title.split())
        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        stem = time.strftime('%Y-%m-%d %H%M%S ', self.clock()) + (re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-')[:60] or 'visual')
        path = folder / (stem + '.html')
        for count in range(2, 50):
            if not path.exists():
                break
            path = folder / f'{stem} {count}.html'
        path.write_text(wrapped(title, document), encoding='utf-8')
        try:
            opened = self.opener(path)
        except Exception as exc:
            return {'ok': False, 'saved': str(path), 'opened': False,
                    'error': f'The page was saved but could not be opened ({type(exc).__name__}). Tell the owner where it is.'}
        return {'ok': True, 'title': title, 'saved': str(path), 'opened': True, 'openedIn': opened,
                'note': 'The page is open in the browser. Say in one sentence what you drew; do not read the markup aloud.'}
