"""Questions Jarvis has asked the owner and is still waiting on.

A background task whose action needs the owner's answer pauses on that question instead of failing, and keeps every
action after it. A question asked before anything started waits the same way. While a question stays unanswered,
Jarvis asks it again once the room is quiet, a little later each time, then gives up on it. The owner's answer goes
back to what was asked about, so the paused task carries on from where it stopped.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

from desktop.common import local_request
from desktop.diagnostics import event as diagnostic
from desktop.research import response_text
from desktop.timeline import ago

# Seconds after each time Jarvis asked before he asks again: soon after the first time, then less often.
ASK_AGAIN_AFTER = (60, 180, 600)
# After the last of those he stops asking; the question is dropped this long after it, and its task stops waiting.
GIVE_UP_AFTER = 1800
# He asks again only once nobody has spoken and nothing has been said for this long.
QUIET_SECONDS = 20
# The longest request Jarvis accepts; a request with its questions and answers stays within it.
TEXT_LIMIT = 4000
EXCHANGES_KEPT = 2


def with_answers(request, exchanges=()):
    """A request together with each question Jarvis asked about it and the owner's answer, as one request that
    stands on its own. The latest exchanges are kept and the request gives way first if it runs long."""
    parts = [f"You asked the owner: {row['question'][:600]}\nThe owner answered: {row['answer'][:600]}"
             for row in list(exchanges or ())[-EXCHANGES_KEPT:]]
    if not parts:
        return request
    room = TEXT_LIMIT - sum(len(part) + 1 for part in parts)
    return '\n'.join([request[:room], *parts])


def clean_line(text):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        text = text[1:-1].strip()
    return re.sub(r'^(?:jarvis\s*:\s*)', '', text, flags=re.I)


def acceptable(text):
    return (bool(text) and len(text) <= 260 and not any(mark in text for mark in ('`', '*', '#', 'http', 'www.'))
            and any(character.isalpha() for character in text))


def reminder_line(row, asked_ago, times_asked, previous=''):
    """Jarvis's own words for asking a question again: the same choices, what the answer is for, and when he asked."""
    payload = {'question': row['question'][:800], 'request': row['request'][:800], 'onHold': [step[:240] for step in row.get('held', [])[:4]],
               'askedAgo': asked_ago, 'timesAsked': times_asked, 'previousLine': previous[:300]}
    response = local_request(18791, '/v1/responses', {
        'model': 'openclaw/lockin', 'task': 'question_reminder', 'user': 'question-' + uuid.uuid4().hex,
        'input': json.dumps(payload, ensure_ascii=False),
    }, timeout=40)
    result = json.loads(response_text(response))
    line = clean_line(result.get('line') if isinstance(result, dict) else '')
    if not acceptable(line):
        raise ValueError('The model did not write a usable question')
    return line


class Questions:
    def __init__(self, controller, writer=None, clock=time.time, monotonic=time.monotonic):
        self.controller = controller
        self.writer = writer or reminder_line
        self.clock, self.monotonic = clock, monotonic
        self.lock = threading.RLock()
        # The question asked before anything started, if he has not answered it yet.
        self.direct = None
        # When each open question was last put to him, how often, and in which words.
        self.asked = {}
        self.prepared = None
        self.reminding = False

    def raised(self, request, question):
        """Jarvis has just asked a question before doing anything with the request."""
        with self.lock:
            identifier = 'question-' + uuid.uuid4().hex[:10]
            self.direct = {'id': identifier, 'taskId': None, 'request': request[:2000], 'question': question[:1200],
                           'raised': self.clock(), 'held': []}
            self.asked = {key: value for key, value in self.asked.items() if not key.startswith('question-')}
            self.asked[identifier] = {'at': self.clock(), 'count': 1, 'line': question[:1200]}
        diagnostic('question_waiting', source='conversation')
        return identifier

    def open(self):
        """Every question still waiting on the owner, the newest first."""
        tasks = getattr(self.controller, 'tasks', None)
        rows = tasks.questions() if tasks else []
        with self.lock:
            if self.direct:
                rows.append(dict(self.direct))
            live = {row['id'] for row in rows}
            for key in [key for key in self.asked if key not in live]:
                del self.asked[key]
        return sorted(rows, key=lambda row: row['raised'], reverse=True)

    def find(self, identifier):
        return next((row for row in self.open() if identifier and row['id'] == identifier), None)

    def context(self):
        """The open questions as the intent classifier sees them, so an answer can find its way back."""
        now = self.clock()
        rows = []
        for row in self.open()[:3]:
            asked = self.asked.get(row['id'], {})
            rows.append({'id': row['id'], 'request': row['request'][:240], 'question': row['question'][:320],
                         'asked': ago(now - asked.get('at', row['raised']))})
        return rows

    def spoken(self, identifier, line=None):
        """Jarvis has just put the question to him, in his own words when it was asked again."""
        with self.lock:
            asked = self.asked.setdefault(identifier, {'at': 0, 'count': 0, 'line': ''})
            asked.update(at=self.clock(), count=asked['count'] + 1, line=(line or asked.get('line') or '')[:1200])
            if self.prepared and self.prepared[0] == identifier:
                self.prepared = None

    def close(self, identifier, answered=True):
        """An answer arrived, or the question no longer applies."""
        with self.lock:
            asked = self.asked.pop(identifier, None)
            if self.direct and self.direct['id'] == identifier:
                self.direct = None
        if answered and asked:
            diagnostic('question_answered', source='task' if identifier.startswith('task-') else 'conversation',
                       attempts=asked['count'], seconds=round(self.clock() - asked['at'], 1))

    def superseded(self):
        """The owner asked for something new, which replaces a question asked before anything started.
        A paused task keeps waiting for its answer."""
        with self.lock:
            identifier = self.direct and self.direct['id']
        if identifier:
            self.close(identifier, answered=False)
            diagnostic('question_dropped', source='conversation', reason='new_request')

    def due(self):
        """The question to ask again now, if any. Questions asked too long ago are given up."""
        now = self.clock()
        chosen = None
        for row in self.open():
            asked = self.asked.get(row['id'])
            if not asked:
                # A task's question that has not been put to him yet waits for its report to be spoken.
                if now - row['raised'] >= GIVE_UP_AFTER + sum(ASK_AGAIN_AFTER):
                    self.give_up(row, 0)
                continue
            if asked['count'] > len(ASK_AGAIN_AFTER):
                if now - asked['at'] >= GIVE_UP_AFTER:
                    self.give_up(row, asked['count'])
                continue
            if chosen is None and now - asked['at'] >= ASK_AGAIN_AFTER[asked['count'] - 1]:
                chosen = row
        return chosen

    def dismiss(self):
        """The owner dropped what Jarvis was asking about: the newest open question goes, and so does a paused task
        waiting on it. Jarvis himself stays."""
        rows = self.open()
        if not rows:
            return None
        row = rows[0]
        self.close(row['id'], answered=False)
        tasks = getattr(self.controller, 'tasks', None)
        if row.get('taskId') and tasks:
            tasks.cancel(row['taskId'])
        diagnostic('question_dropped', source='task' if row.get('taskId') else 'conversation', reason='dismissed')
        return row

    def give_up(self, row, attempts):
        self.close(row['id'], answered=False)
        tasks = getattr(self.controller, 'tasks', None)
        if row.get('taskId') and tasks:
            tasks.expire(row['taskId'])
        diagnostic('question_given_up', source='task' if row.get('taskId') else 'conversation', attempts=attempts)

    def line(self, row):
        """His words for asking again, written once for this attempt. The question as first asked if none come."""
        with self.lock:
            asked = dict(self.asked.get(row['id']) or {'at': row['raised'], 'count': 1, 'line': ''})
            if self.prepared and self.prepared[:2] == (row['id'], asked['count']):
                return self.prepared[2]
        started = self.monotonic()
        try:
            text = self.writer(row, ago(self.clock() - asked['at']), asked['count'], asked.get('line', ''))
            diagnostic('question_line_written', duration_ms=round((self.monotonic() - started) * 1000))
        except Exception as exc:
            diagnostic('question_line_fallback', error_type=type(exc).__name__, duration_ms=round((self.monotonic() - started) * 1000))
            text = row['question']
        with self.lock:
            self.prepared = (row['id'], asked['count'], text)
        return text

    def quiet(self):
        c = self.controller
        return (c.voice_ready and not c.muted and not c.breaks.active and not c.busy.locked() and c.state == 'ready'
                and self.monotonic() - getattr(c, 'quiet_since', 0) >= QUIET_SECONDS)

    def remind(self):
        """Called about once a second: asks a question again when one is due and the room is quiet."""
        try:
            if self.reminding or not self.quiet():
                return
            row = self.due()
        except Exception as exc:
            diagnostic('question_again_failed', error_type=type(exc).__name__)
            return
        if not row:
            return
        self.reminding = True
        threading.Thread(target=self.ask_again, args=(row,), name='jarvis-question-again', daemon=True).start()

    def ask_again(self, row):
        c = self.controller
        try:
            text = self.line(row)
            current = self.find(row['id'])
            if not current or not self.quiet() or not c.busy.acquire(blocking=False):
                return
            try:
                if not self.find(row['id']):
                    return
                c.cancelled = threading.Event()
                self.spoken(row['id'], text)
                c.last_answer_text = text
                if row.get('taskId'):
                    c.last_task_id, c.last_domain, c.last_answer_domain = row['taskId'], 'tasks', 'tasks'
                    c.last_command_time = time.monotonic()
                diagnostic('question_asked_again', source='task' if row.get('taskId') else 'conversation',
                           attempts=self.asked.get(row['id'], {}).get('count'))
                c.speak(text)
            finally:
                c.release_response()
        except Exception as exc:
            diagnostic('question_again_failed', error_type=type(exc).__name__)
        finally:
            self.reminding = False
