from __future__ import annotations

import json
import re
import time
import uuid

from desktop.common import local_request
from desktop.diagnostics import event as diagnostic
from desktop.research import response_text

TONES = ('calm and direct', 'dry', 'a little amused', 'firm', 'plain and short', 'pointed about the repeat')
BANNED = {
    'you cannot be here. you need to lock in. you cannot be here right now. you need to lock in.',
    'you cannot be here. you need to lock in.',
    'you cannot be here right now. you need to lock in.',
    'you cannot be here right now.',
    'you need to lock in.',
    'sir, you cannot be here. you need to lock in.',
    'sir, you need to lock in.',
    'sir you cannot be here you need to lock in.',
}


# Why a site is closed, for sites that are not about doomscrolling. The model words the line itself around this.
REASONS = {'DoorDash': 'Food delivery. Ordering is the craving talking: what his brain wants right now, not what is '
                       'optimal for him. It costs far too much, and the macros are not it.'}
FALLBACKS = {'DoorDash': "Sir, you know this is what your brain wants, not what's optimal for you. It costs too much, "
                         "and the macros aren't it."}
SITE_NAMES = ('X', 'Instagram', 'DoorDash')


def fallback_line(name, attempt, timing=None):
    if name in FALLBACKS:
        return FALLBACKS[name]
    site = name if name in SITE_NAMES else 'That site'
    since = (timing or {}).get('secondsSincePrevious')
    if attempt > 1 and since is not None and since < 120:
        return f'Again already, sir? {site} is closed, just like a moment ago.'
    if attempt <= 1:
        return f'{site} is closed, sir. Back to the work.'
    if attempt == 2:
        return f'{site} is closed again, sir. Stay with the work.'
    return f'That is attempt {attempt}, sir. {site} stays closed.'


def clean_line(text):
    text = str(text or '').strip()
    text = re.sub(r'^(?:jarvis\s*:\s*)', '', text, flags=re.I)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        text = text[1:-1].strip()
    return re.sub(r'\s+', ' ', text).strip()


def acceptable(text):
    if not text or len(text) > 180 or len(text.split()) > 32:
        return False
    if any(mark in text for mark in ('\n', '\r', '`', '*', '#', '/', '\\', 'http', 'www.')):
        return False
    folded = re.sub(r'\s+', ' ', re.sub(r'[^a-z. ]', '', text.lower())).strip()
    banned = {re.sub(r'\s+', ' ', re.sub(r'[^a-z. ]', '', line)).strip() for line in BANNED}
    return folded not in banned and folded.rstrip('.') not in {line.rstrip('.') for line in banned}


TIMING_KEYS = ('localTime', 'previousAttempt', 'previousSite', 'previousAttemptOnThisSite', 'attemptsInLastTenMinutes',
               'attemptsInLastHour', 'attemptsToday')


def lock_in_line(name, attempt, session=None, timing=None):
    started = time.monotonic()
    attempt = int(attempt)
    user = session if isinstance(session, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session) else 'lockin-' + uuid.uuid4().hex
    # When this attempt came, next to the earlier ones, so an instant retry sounds different from one days later.
    when = {key: timing[key] for key in TIMING_KEYS if isinstance(timing, dict) and timing.get(key) is not None}
    try:
        response = local_request(18791, '/v1/responses', {
            'model': 'openclaw/lockin', 'user': user,
            'input': json.dumps({
                'site': name if name in SITE_NAMES else 'That site',
                **({'reason': REASONS[name]} if name in REASONS else {}),
                'attempt': attempt,
                'tone': TONES[(attempt - 1) % len(TONES)],
                **({'timing': when} if when else {}),
            }, ensure_ascii=False),
        }, timeout=5)
        answer = clean_line(response_text(response))
        if not acceptable(answer):
            raise ValueError('The lock in line was not a short sentence')
        diagnostic('lock_in_line', attempts=attempt, reply_chars=len(answer), duration_ms=round((time.monotonic() - started) * 1000))
        return answer
    except Exception as exc:
        diagnostic('lock_in_line_fallback', error_type=type(exc).__name__, attempts=attempt, duration_ms=round((time.monotonic() - started) * 1000))
        return fallback_line(name, attempt, timing)
