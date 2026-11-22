"""Reminders and timers the owner asks for, kept on this PC.

A reminder waits for him: it pops up and is spoken at his first keyboard or mouse input after its time, so one that
falls due while he is away is kept until he is back (desktop.welcome watches for that input). A reminder for the
morning, or for a day without a time, is due when the morning begins. The good-morning greeting he gets on coming
back to the computer that day carries it, even if he is up before the morning has begun.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, time as clock_time, timedelta
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid

from desktop.common import ROOT
from desktop.diagnostics import event as diagnostic

REPEATS = ('none', 'daily', 'weekdays', 'weekly', 'interval')
# The morning begins when Jarvis's greeting turns to good morning (desktop.welcome.part_of_day).
MORNING_HOUR = 5


def next_occurrence(item, now):
    due = item['due']
    if item['repeat'] == 'interval':
        step = item['interval']
        return due + (math.floor(max(0, now - due) / step) + 1) * step
    local = datetime.fromtimestamp(due)
    while local.timestamp() <= now:
        local += timedelta(days=7 if item['repeat'] == 'weekly' else 1)
        if item['repeat'] == 'weekdays':
            while local.weekday() > 4:
                local += timedelta(days=1)
    return local.timestamp()


def came_due(row):
    """When this occurrence of a reminder fell due: the end of its snooze, otherwise its time."""
    return row.get('snoozed_until') or row['due']


def day_began(row):
    """Midnight at the start of the day a reminder falls due."""
    return datetime.fromtimestamp(row['due']).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def morning(day):
    """The moment the morning of a local date begins, as a timestamp."""
    return datetime.combine(day, clock_time(MORNING_HOUR)).timestamp()


class WaitingReminders:
    """Due reminders that have not reached the owner yet. Each is given at his first keyboard or mouse input after it
    fell due, or with the greeting he gets when he comes back after hours away."""

    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}

    def add(self, row):
        with self.lock:
            self.rows[row['id']] = row

    def ready(self, seen):
        """Whether any reminder fell due at or before seen, the time of his latest input."""
        with self.lock:
            return any(came_due(row) <= seen for row in self.rows.values())

    def take(self, seen=None):
        """Remove and return the reminders that fell due by seen, or all of them, oldest first."""
        with self.lock:
            rows = [row for row in self.rows.values() if seen is None or came_due(row) <= seen]
            for row in rows:
                del self.rows[row['id']]
        return sorted(rows, key=came_due)


class Reminders:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path or ROOT / '.openclaw/reminders.json')
        self.clock = clock
        self.lock = threading.RLock()
        self.notified = set()
        self.items = []
        self.load_error = None
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if data.get('version') != 1 or not isinstance(data.get('items'), list):
                    raise ValueError('Invalid reminders file')
                for item in data['items']:
                    if (not isinstance(item.get('id'), str) or not isinstance(item.get('text'), str)
                            or not isinstance(item.get('due'), (int, float)) or not math.isfinite(item['due'])
                            or item.get('repeat') not in REPEATS
                            or item.get('state') not in ('scheduled', 'due', 'done', 'cancelled')
                            or item.get('morning', False) not in (True, False)
                            or (item['repeat'] == 'interval' and item.get('interval', 0) < 60)):
                        raise ValueError('Invalid reminder')
                self.items = data['items']
            except (ValueError, OSError, TypeError, AttributeError):
                self.load_error = 'The reminders file could not be read. It has been preserved; restore it from a backup before saving reminders.'

    def _save(self):
        if self.load_error:
            raise RuntimeError(self.load_error)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'items': self.items}, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _public(self, item):
        row = copy.deepcopy(item)
        row['due_at'] = datetime.fromtimestamp(row['due']).astimezone().isoformat(timespec='seconds')
        return row

    def list(self, include_done=False):
        with self.lock:
            return {'ok': not bool(self.load_error), 'error': self.load_error,
                    'reminders': [self._public(row) for row in sorted(self.items, key=lambda x: x['due'])
                                  if include_done or row['state'] in ('scheduled', 'due')]}

    def create(self, text, due_at='', delay_seconds=0, repeat='none', interval_seconds=0, morning_of=''):
        """Save a reminder for a time (due_at), after a delay (delay_seconds), or for a morning (morning_of, a local
        date): one for the morning is given when the owner first comes to the computer that day."""
        if not isinstance(text, str) or not text.strip() or len(text) > 500:
            raise ValueError('Give the reminder a short description')
        now = self.clock()
        if due_at and delay_seconds:
            raise ValueError('Choose a time or a delay, not both')
        if morning_of and (due_at or delay_seconds):
            raise ValueError('Choose a morning, a time or a delay, only one of them')
        if morning_of:
            found = re.match(r'\s*(\d{4}-\d{2}-\d{2})', str(morning_of))
            try:
                day = date.fromisoformat(found[1])
            except (TypeError, ValueError):
                raise ValueError('Give the morning as a local date, YYYY-MM-DD') from None
            due = morning(day)
            if due <= now:
                raise ValueError('That morning has already begun. For later today use a time; for tomorrow morning use '
                                 "tomorrow's date.")
            if repeat == 'interval':
                raise ValueError('A morning reminder repeats daily, on weekdays or weekly')
        elif due_at:
            try:
                parsed = datetime.fromisoformat(due_at.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                raise ValueError('Use an ISO date and time with a timezone offset') from None
            if parsed.tzinfo is None:
                raise ValueError('Include the local timezone offset in the reminder time')
            due = parsed.timestamp()
        else:
            due = now + float(delay_seconds)
        if not math.isfinite(due) or due <= now or due > now + 10 * 366 * 86400:
            raise ValueError('Choose a future reminder time within ten years')
        if repeat not in REPEATS:
            raise ValueError('Choose none, daily, weekdays, weekly or interval')
        interval = float(interval_seconds)
        if repeat == 'interval' and (not math.isfinite(interval) or not 60 <= interval <= 366 * 86400):
            raise ValueError('Recurring intervals must be between a minute and a year')
        with self.lock:
            if len([r for r in self.items if r['state'] in ('scheduled', 'due')]) >= 200:
                raise ValueError('There are already 200 active reminders')
            item = {'id': uuid.uuid4().hex[:10], 'text': text.strip(), 'due': due, 'repeat': repeat,
                    'interval': interval if repeat == 'interval' else 0, 'state': 'scheduled', 'created': now}
            if morning_of:
                item['morning'] = True
            previous = copy.deepcopy(self.items)
            self.items.append(item)
            try:
                self._save()
            except Exception:
                self.items = previous
                raise
            heard = ('He hears it with the greeting he gets when he first comes to the computer that day, or at his first '
                     'input after the morning begins.' if morning_of else
                     'He hears it at his first mouse or keyboard input after this time; if he is away, it waits until he '
                     'is back.')
            return {'ok': True, 'reminder': self._public(item), 'heard': heard}

    def change(self, action, reference='', seconds=600):
        if action not in ('snooze', 'dismiss', 'cancel'):
            raise ValueError('Choose snooze, dismiss or cancel')
        with self.lock:
            active = [r for r in self.items if r['state'] in ('scheduled', 'due')]
            due = [r for r in active if r['state'] == 'due']
            matches = [r for r in active if r['id'] == reference] if reference else due
            if len(matches) != 1:
                return {'ok': False, 'error': 'Choose the reminder by its ID from list_reminders.',
                        'reminders': [self._public(r) for r in active]}
            item = matches[0]
            previous = copy.deepcopy(item)
            if action == 'snooze':
                seconds = float(seconds)
                if not math.isfinite(seconds) or not 1 <= seconds <= 366 * 86400:
                    raise ValueError('Choose a snooze between a second and a year')
                item['snoozed_until'] = self.clock() + seconds
                item['state'] = 'scheduled'
            elif action == 'cancel':
                item['state'] = 'cancelled'
            elif item['repeat'] != 'none':
                item['due'] = next_occurrence(item, max(self.clock(), item['due']))
                item['state'] = 'scheduled'
                item.pop('snoozed_until', None)
            else:
                item['state'] = 'done'
            try:
                self._save()
            except Exception:
                item.clear()
                item.update(previous)
                raise
            self.notified.discard(item['id'])
            return {'ok': True, 'reminder': self._public(item)}

    def poll(self):
        with self.lock:
            if self.load_error:
                return []
            now, changed = self.clock(), False
            previous = copy.deepcopy(self.items)
            for row in self.items:
                if row['state'] == 'scheduled' and row.get('snoozed_until', row['due']) <= now:
                    row['state'] = 'due'
                    changed = True
                if row['state'] == 'due' and row['repeat'] != 'none':
                    if row['repeat'] == 'interval':
                        latest = row['due'] + math.floor(max(0, now - row['due']) / row['interval']) * row['interval']
                    else:
                        latest = row['due']
                        probe = dict(row)
                        following = next_occurrence(probe, latest)
                        while following <= now:
                            latest = following
                            probe['due'] = latest
                            following = next_occurrence(probe, latest)
                    if latest > row['due']:
                        row['due'] = latest
                        row.pop('snoozed_until', None)
                        self.notified.discard(row['id'])
                        changed = True
            if changed:
                try:
                    self._save()
                except Exception:
                    self.items = previous
                    raise
            pending = [self._public(r) for r in self.items if r['state'] == 'due' and r['id'] not in self.notified]
            self.notified.update(r['id'] for r in pending)
            return pending

    def for_greeting(self):
        """The reminders a greeting brings now, besides those already waiting for the owner: any that has just fallen
        due, and a morning reminder from midnight on its day, because the greeting is the first time Jarvis sees him
        after he has slept. A reminder taken here is not given again by poll."""
        with self.lock:
            rows = self.poll()
            now = self.clock()
            early = [row for row in self.items if row['state'] == 'scheduled' and row.get('morning')
                     and 'snoozed_until' not in row and day_began(row) <= now < row['due']]
            if early:
                previous = copy.deepcopy(self.items)
                for row in early:
                    row['state'] = 'due'
                try:
                    self._save()
                except Exception as exc:
                    # Those stay for their own morning; the reminders already due still reach him with the greeting.
                    self.items = previous
                    diagnostic('reminders_unsaved', error_type=type(exc).__name__)
                    return rows
                self.notified.update(row['id'] for row in early)
                rows += [self._public(row) for row in early]
            return rows
