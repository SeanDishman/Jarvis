"""Welcome back: after hours away from the computer, Jarvis greets the owner in the middle of his main monitor.

Windows records the time of the last keyboard or mouse input. When the first input arrives after at least six
hours without any, Jarvis flies to the main monitor, whichever monitor he normally lives on, grows there, shows
a card and says a greeting for the time of day built around a real quotation. If Jarvis was not running while
the owner was away, for example because the computer was off overnight, he greets him once it starts.

The same input tells Jarvis the owner is there for his reminders. A due reminder waits until his first input after
its time, so one that falls due while he is away is given when he is back. Reminders due when he is greeted, and one
he asked for that morning, are part of the greeting.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import json
from pathlib import Path
import threading
import time

from desktop.common import ROOT
from desktop.diagnostics import event as diagnostic
from desktop.learning_reminders import passed_on, welcome_greeting, write_json
from desktop.lock_screens import covered, wait_until_clear

AWAY_SECONDS = 6 * 3600.0
POLL_SECONDS = 2.0
# Input this recent means he is at the computer now.
PRESENT_SECONDS = 120.0
PRESENCE_SAVE_SECONDS = 300.0
VOICE_WAIT_SECONDS = 90.0
BUSY_WAIT_SECONDS = 20.0
WELCOME_SCALE = 2.6
WELCOME_HOLD_MS = 45000
# A greeting held this long behind a lock screen is rewritten, so the time of day and hours away are still right.
STALE_SECONDS = 900.0
TICKS = 2 ** 32
SALUTATIONS = {'morning': 'Good morning', 'afternoon': 'Good afternoon', 'evening': 'Good evening', 'night': 'Welcome back'}


class LastInputInfo(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.UINT), ('dwTime', wintypes.DWORD)]


_user = ctypes.WinDLL('user32', use_last_error=True)
_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_user.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInputInfo)]
_user.GetLastInputInfo.restype = wintypes.BOOL
_kernel.GetTickCount.restype = wintypes.DWORD


def last_input_tick():
    """The tick count, in milliseconds, of the last keyboard or mouse input in this Windows session."""
    info = LastInputInfo(ctypes.sizeof(LastInputInfo), 0)
    if not _user.GetLastInputInfo(ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), 'GetLastInputInfo failed')
    return int(info.dwTime)


def current_tick():
    return int(_kernel.GetTickCount())


def part_of_day(hour):
    if 5 <= hour < 12:
        return 'morning'
    if 12 <= hour < 17:
        return 'afternoon'
    if 17 <= hour < 22:
        return 'evening'
    return 'night'


def fallback_greeting(part):
    return f"{SALUTATIONS[part]}, sir. Let's get to work."


def main_work_area():
    """The work area of the main monitor, in physical pixels, whichever monitor the orb lives on."""
    from desktop.windows import monitors
    screens = monitors()
    screen = next((row for row in screens if row['primary']), screens[0] if screens else None)
    return list(screen['work']) if screen else None


class PresenceLog:
    """When the owner was last at the computer, kept on this PC so a restart can tell he has been away.
    Only that one time is stored."""

    def __init__(self, path=None):
        self.path = Path(path or ROOT / '.openclaw/presence.json')

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        value = data.get('active') if isinstance(data, dict) else None
        return float(value) if type(value) in (int, float) else None

    def write(self, moment):
        write_json(self.path, {'active': round(moment, 1)})


class WelcomeBack:
    def __init__(self, controller, last_input=last_input_tick, tick=current_tick, wall=time.time, writer=None,
                 presence=None, work_area=main_work_area, local_time=datetime.now, cover=covered,
                 until_clear=wait_until_clear, monotonic=time.monotonic):
        self.controller = controller
        self.last_input, self.tick, self.wall, self.local_time = last_input, tick, wall, local_time
        self.cover, self.until_clear, self.monotonic = cover, until_clear, monotonic
        self.writer = writer or welcome_greeting
        self.presence = presence or PresenceLog()
        self.work_area = work_area
        self.lock = threading.Lock()
        self.thread = self.worker = None
        self.last = self.away_at_start = None
        self.saved = float('-inf')
        self.visits = 0

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='jarvis-welcome-back', daemon=True)
            self.thread.start()
        return self.thread

    def run(self):
        self.started()
        while not self.controller.closed.wait(POLL_SECONDS):
            try:
                self.check()
            except Exception as exc:
                diagnostic('welcome_check_failed', error_type=type(exc).__name__)
                self.release()
                self.controller.closed.wait(10)

    def started(self):
        """Jarvis was not running while he was away, for example because the computer was off overnight."""
        seen = self.presence.read()
        if seen is not None and self.wall() - seen >= AWAY_SECONDS:
            self.away_at_start = self.wall() - seen

    def check(self):
        tick = self.last_input()
        idle = ((self.tick() - tick) % TICKS) / 1000
        now = self.wall()
        if self.last is not None and tick != self.last:
            # The first input after a long stretch without any: how long he was gone is the gap between the two.
            away = ((tick - self.last) % TICKS) / 1000
            if away >= AWAY_SECONDS:
                self.greet(away, 'return')
        self.last = tick
        if self.away_at_start is not None and idle <= PRESENT_SECONDS:
            self.greet(self.away_at_start + idle, 'startup')
        # A greeting comes first and carries the reminders due by then; the rest wait for his next input.
        if idle <= PRESENT_SECONDS and not self.greeting():
            self.remind(now - idle)
        if idle <= PRESENT_SECONDS and now - self.saved >= PRESENCE_SAVE_SECONDS:
            self.saved = now
            try:
                self.presence.write(now - idle)
            except Exception as exc:
                diagnostic('welcome_presence_unsaved', error_type=type(exc).__name__)

    def greeting(self):
        """Whether a greeting is on its way or under way."""
        return self.away_at_start is not None or (self.worker is not None and self.worker.is_alive())

    def remind(self, seen):
        """Give the due reminders he has not had yet, now that he has touched the mouse or keyboard since their time.
        seen is the moment of that input."""
        controller = self.controller
        waiting = getattr(controller, 'waiting_reminders', None)
        if waiting is None or controller.breaks.active or not waiting.ready(seen):
            return []
        # His input may only have woken a lock screen, where typing the password is input too.
        try:
            if self.cover():
                return []
        except Exception as exc:
            diagnostic('reminder_lock_check_failed', error_type=type(exc).__name__)
        return controller.deliver_reminders(waiting.take(seen))

    def release(self):
        """While his input cannot be read, a due reminder is given at once rather than held for a moment never seen."""
        waiting = getattr(self.controller, 'waiting_reminders', None)
        if waiting is None or self.controller.breaks.active or self.greeting():
            return
        try:
            self.controller.deliver_reminders(waiting.take())
        except Exception as exc:
            diagnostic('reminders_undelivered', error_type=type(exc).__name__)

    def due_reminders(self):
        """The reminders a greeting carries: those waiting for him, any that has just fallen due, and one he asked for
        this morning, since the greeting is the first time Jarvis sees him that day."""
        controller = self.controller
        waiting = getattr(controller, 'waiting_reminders', None)
        if waiting is None:
            return []
        rows = waiting.take()
        try:
            rows += controller.reminders.for_greeting()
        except Exception as exc:
            diagnostic('welcome_reminders_unavailable', error_type=type(exc).__name__)
        return rows

    def greet(self, away, source):
        self.away_at_start = None
        if self.worker is not None and self.worker.is_alive():
            return None
        if self.controller.breaks.active:
            diagnostic('welcome_skipped', reason='break', seconds=round(away))
            return None
        diagnostic('welcome_back', source=source, seconds=round(away))
        self.worker = threading.Thread(target=self.deliver, args=(away,), name='jarvis-welcome', daemon=True)
        self.worker.start()
        return self.worker

    def compose(self, away, reminders=()):
        moment = self.local_time()
        part = part_of_day(moment.hour)
        texts = [row['text'] for row in reminders]
        try:
            details = (away / 3600, part, moment.strftime('%A'))
            text = self.writer(*details, reminders=texts) if texts else self.writer(*details)
            if not text:
                raise ValueError('Empty welcome greeting')
        except Exception as exc:
            diagnostic('welcome_greeting_fallback', error_type=type(exc).__name__)
            text = ' '.join([fallback_greeting(part)] + ([passed_on(texts)] if texts else []))
        return text, part

    def deliver(self, away):
        controller = self.controller
        began = self.monotonic()
        reminders = self.due_reminders()
        text, part = self.compose(away, reminders)
        # The first input may only have woken a lock screen, Alpenlock or Windows' own, and typing the password there
        # is input too. The greeting is written while he types and waits until he is past it.
        try:
            cover = self.cover()
        except Exception as exc:
            diagnostic('welcome_lock_check_failed', error_type=type(exc).__name__)
            cover = None
        if cover:
            diagnostic('welcome_waiting', reason=cover)
            if not self.until_clear(controller.closed):
                return
            waited = self.monotonic() - began
            diagnostic('welcome_unlocked', reason=cover, seconds=round(waited))
            if waited >= STALE_SECONDS:
                reminders += self.due_reminders()
                text, part = self.compose(away + waited, reminders)
        try:
            rect = self.work_area()
        except Exception as exc:
            diagnostic('welcome_monitor_unavailable', error_type=type(exc).__name__)
            rect = None
        with self.lock:
            self.visits += 1
            visit = self.visits
        if rect:
            controller.events.put(('welcome', {'visit': visit, 'text': text, 'title': SALUTATIONS[part], 'rect': rect}))
        # Their cards come up with the greeting, which says them.
        shown = controller.deliver_reminders(reminders, spoken=True) if reminders else []
        spoken = False
        try:
            spoken = self.speak(text)
            diagnostic('welcome_greeted', spoken=spoken, reply_chars=len(text), reminders=len(shown))
        except Exception as exc:
            diagnostic('welcome_voice_failed', error_type=type(exc).__name__)
        finally:
            # A greeting he did not hear leaves its reminders to be said on their own once Jarvis can speak.
            if shown and not spoken:
                controller.pending_announcements.extend(shown)
            controller.events.put(('welcome_finished', visit))

    def speak(self, text):
        controller = self.controller
        deadline = time.monotonic() + VOICE_WAIT_SECONDS
        while not controller.voice_ready and time.monotonic() < deadline:
            if controller.closed.wait(.5):
                return False
        if controller.muted or not controller.voice_ready or controller.breaks.active:
            # Leave the card up long enough to read before Jarvis flies home.
            controller.closed.wait(max(6.0, 2 + len(text.split()) / 2.5))
            return False
        if not controller.busy.acquire(timeout=BUSY_WAIT_SECONDS):
            controller.closed.wait(6.0)
            return False
        try:
            controller.cancelled = threading.Event()
            controller.speak(text)
        finally:
            controller.release_response()
        return True
