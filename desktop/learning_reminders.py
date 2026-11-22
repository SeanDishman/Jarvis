"""Reminders for YouTube and LinkedIn, the sites lock-in mode leaves open.

The model proposes real quotations that suit the visit, each with the spoken reminder built around it.
Every quotation is checked against Wikiquote's sourced quotes before Jarvis says it, so a misremembered
or misattributed line is never spoken. Nothing here is a stored quote or a canned reminder.
"""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

import requests

from desktop.common import ROOT, local_request
from desktop.diagnostics import event as diagnostic
from desktop.research import response_text

SITES = ('LinkedIn', 'YouTube')
WIKIQUOTE = 'https://en.wikiquote.org/w/api.php'
HEADERS = {'User-Agent': 'JarvisDesktop/1.0 (focus reminder; Wikiquote reader)'}
# Wikiquote keeps doubtful lines and lines about a person under these headings.
UNSOURCED = re.compile(r'disputed|unsourced|misattributed|attributed|\babout\b', re.I)
HEADING = re.compile(r'^(={2,6})\s*(.*?)\s*\1\s*$')
DATE_PAGE = re.compile(r'^(?:january|february|march|april|may|june|july|august|september|october|november|december)'
                       r'(?: \d{1,2})?(?:,? \d{4})?$', re.I)
NAME_FILLER = frozenset({'the', 'von', 'van', 'der', 'den', 'del', 'younger', 'elder', 'jr', 'sr'})
MATCH_SHARE = .8
# Wikimedia allows anonymous apps 10 requests a minute, so stay under that and spend at most 8 on one reminder.
REQUEST_SPACING = .5
WINDOW_SECONDS, WINDOW_REQUESTS, WINDOW_WAIT_SECONDS = 60.0, 8, 20.0
LOOKUP_BUDGET = 8
ROUNDS = 2
CANDIDATES = 4
HISTORY = 40
REJECTED = 120
AVOID = 12
SPOKEN_LIMIT = 280
# What the welcome greeting says to pass on the owner's due reminders.
REMINDERS_LIMIT = 400
# Words that say nothing about what a reminder is for, left out when checking that the greeting passed it on.
REMINDER_FILLER = frozenset({
    'about', 'after', 'again', 'also', 'asked', 'before', 'could', 'from', 'going', 'have', 'into', 'just', 'make',
    'must', 'need', 'needs', 'remember', 'remind', 'reminder', 'should', 'sure', 'that', 'them', 'then', 'there',
    'they', 'this', 'today', 'tomorrow', 'want', 'wants', 'what', 'when', 'will', 'with', 'would', 'your'})


def spoken(value, limit):
    if not isinstance(value, str):
        return ''
    line = ' '.join(value.split())
    return line if len(line) <= limit and not re.search(r'https?://|www\.|[`*#]', line, re.I) else ''


def words(value):
    return ' '.join(re.findall(r"[^\W_]+", str(value or '').casefold()))


def contains_words(text, part):
    return bool(part) and (' ' + words(part) + ' ') in (' ' + words(text) + ' ')


def names(author):
    return [name for name in words(author).split() if len(name) >= 3 and name not in NAME_FILLER]


def surname(author):
    found = names(author)
    return found[-1] if found else ''


def named(author, text):
    spoken_words = set(words(text).split())
    return any(name in spoken_words for name in names(author))


def sourced_lines(extract):
    """Lines under Wikiquote's sourced headings, skipping the introduction and doubtful sections."""
    level, skipped = 0, None
    for line in str(extract or '').splitlines():
        heading = HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if skipped is not None and level <= skipped:
                skipped = None
            if skipped is None and UNSOURCED.search(heading.group(2)):
                skipped = level
            continue
        if level and skipped is None and line.strip():
            yield line


def sourced_wording(extract, quote):
    """Wikiquote's own wording of this quotation from a sourced section, or ''.

    A quotation recalled with a word or two out, a different translation, or British spelling still counts
    when nearly all of its words appear in order in one sourced line; Jarvis then says Wikiquote's words."""
    wanted = words(quote).split()
    if not wanted:
        return ''
    needed, best, best_score = set(wanted), '', 0.0
    for line in sourced_lines(extract):
        tokens = list(re.finditer(r'[^\W_]+', line))
        folded = [token.group().casefold() for token in tokens]
        if len(needed & set(folded)) < MATCH_SHARE * len(needed):
            continue
        blocks = [block for block in difflib.SequenceMatcher(None, folded, wanted, autojunk=False).get_matching_blocks()
                  if block.size]
        matched = sum(block.size for block in blocks)
        start, end = (blocks[0].a, blocks[-1].a + blocks[-1].size) if blocks else (0, 0)
        if matched < MATCH_SHARE * len(wanted) or not .7 * len(wanted) <= end - start <= 1.4 * len(wanted):
            continue
        score = matched / max(len(wanted), end - start)
        if score > best_score:
            stop = tokens[end - 1].end()
            text = line[tokens[start].start():stop] + (line[stop] if line[stop:stop + 1] in ('.', '!', '?') else '')
            best, best_score = text[:1].upper() + text[1:], score
    return best


class Wikiquote:
    """A small, polite Wikiquote reader: spaced requests, a page cache, and a pause when asked to slow down."""

    def __init__(self, get=None, clock=time.monotonic, sleep=time.sleep):
        self.get = get or requests.get
        self.clock, self.sleep = clock, sleep
        self.lock = threading.Lock()
        self.pages = {}
        self.last, self.paused_until = float('-inf'), 0.0
        self.sent = []
        self.budget = LOOKUP_BUDGET

    def query(self, **params):
        with self.lock:
            now = self.clock()
            if self.budget <= 0:
                raise LookupError('Wikiquote lookup budget spent')
            if now < self.paused_until:
                raise LookupError('Wikiquote asked for a pause')
            self.sent = [moment for moment in self.sent if now - moment < WINDOW_SECONDS]
            if len(self.sent) >= WINDOW_REQUESTS:
                # Two reminders close together can share a minute; wait a little rather than give up.
                wait = self.sent[0] + WINDOW_SECONDS - now
                if wait > WINDOW_WAIT_SECONDS:
                    raise LookupError('Wikiquote request window is full')
                self.sleep(wait)
                now = self.clock()
                self.sent = [moment for moment in self.sent if now - moment < WINDOW_SECONDS]
            if now < self.last + REQUEST_SPACING:
                self.sleep(self.last + REQUEST_SPACING - now)
            self.budget -= 1
            self.last = self.clock()
            self.sent.append(self.last)
        response = self.get(WIKIQUOTE, params={**params, 'format': 'json', 'formatversion': 2}, headers=HEADERS, timeout=(3, 5))
        if response.status_code == 429:
            retry = str(response.headers.get('retry-after') or '')
            self.paused_until = self.clock() + min(900, max(30, int(retry) if retry.isdigit() else 120))
            raise LookupError('Wikiquote rate limit')
        response.raise_for_status()
        return response.json()

    def page(self, title):
        key = title.casefold()
        if key not in self.pages:
            found = self.query(action='query', prop='extracts', titles=title, explaintext=1,
                               exsectionformat='wiki', redirects=1)['query']['pages'][0]
            if len(self.pages) >= 24:
                self.pages.clear()
            self.pages[key] = None if found.get('missing') or found.get('invalid') else (found['title'], found.get('extract') or '')
        return self.pages[key]

    def search(self, text, limit):
        """Pages that carry the words, best match first, each with its introduction, in one request."""
        found = self.query(action='query', generator='search', gsrsearch=text, gsrlimit=limit, gsrnamespace=0,
                           prop='extracts', exintro=1, explaintext=1, exlimit=limit)
        pages = (found.get('query') or {}).get('pages') or []
        return [(page['title'], page.get('extract') or '') for page in sorted(pages, key=lambda page: page.get('index', 0))]

    def source(self, author, quote):
        """(page title, Wikiquote's wording) when a sourced section gives this quotation to this person, else None.

        The person's own page counts, and so does a page for one of their works, such as Walden, whose
        introduction names them. Date and theme pages that merely list the line do not, and neither do the
        disputed, misattributed or 'about' sections of any page."""
        who = set(names(author))
        if not who or not 4 <= len(words(quote).split()) <= 40:
            return None
        page = self.page(author)
        wording = sourced_wording(page[1], quote) if page and who <= set(words(page[0]).split()) else ''
        if wording:
            return page[0], wording
        # Search ranks theme pages above the person's own, so it only looks for works, or for the right page
        # when the name led to a disambiguation page. The opening words are enough to find the line.
        phrase = ' '.join(words(quote).split()[:8])
        hits = [(title, intro) for title, intro in self.search('"' + phrase + '"', 5)
                if not DATE_PAGE.match(title) and not (page and title == page[0])]
        chosen = ([title for title, _intro in hits if who <= set(words(title).split())]
                  + [title for title, intro in hits if who <= set(words(intro[:400]).split())])
        for title in chosen[:1]:
            other = self.page(title)
            wording = sourced_wording(other[1], quote) if other else ''
            if wording:
                return other[0], wording
        return None

    def available(self):
        with self.lock:
            return self.budget >= 2 and self.clock() >= self.paused_until

    def reset_budget(self):
        with self.lock:
            self.budget = LOOKUP_BUDGET


def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open('w', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)


def quotations(rows):
    return [{'author': row['author'], 'quote': row['quote']} for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and isinstance(row.get('author'), str) and isinstance(row.get('quote'), str)]


class QuoteHistory:
    """Kept on this PC: quotations already spoken, so none repeats, and quotations Wikiquote could not confirm,
    so they are neither looked up nor offered again. The last reminder stays in memory only, because it can
    mention the page that was open."""

    def __init__(self, path=None):
        self.path = Path(path or ROOT / '.openclaw/learning-quotes.json')
        self.lock = threading.Lock()
        self.last = ''

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        return {'quotes': quotations(data.get('quotes'))[-HISTORY:],
                'rejected': quotations(data.get('rejected'))[-REJECTED:], 'last': self.last}

    def save(self, data):
        write_json(self.path, {'quotes': data['quotes'][-HISTORY:], 'rejected': data['rejected'][-REJECTED:]})

    def remember(self, reminder, author='', quote=''):
        with self.lock:
            self.last = reminder
            if author and quote:
                data = self.read()
                data['quotes'].append({'author': author, 'quote': quote})
                self.save(data)

    def reject(self, author, quote):
        with self.lock:
            data = self.read()
            if not repeated(author, quote, data['rejected']):
                data['rejected'].append({'author': author, 'quote': quote})
                self.save(data)


def repeated(author, quote, recent):
    return any(words(row['quote']) == words(quote) or (contains_words(row['quote'], quote) and words(row['author']) == words(author))
               for row in recent)


def ask(task, details, avoid, unverified, previous):
    response = local_request(18791, '/v1/responses', {
        'model': 'openclaw/lockin', 'task': task, 'user': task.split('_')[0] + '-' + uuid.uuid4().hex,
        'input': json.dumps({**details, 'avoid': avoid, 'unverified': unverified, 'previousReminder': previous},
                            ensure_ascii=False),
    }, timeout=40)
    result = json.loads(response_text(response))
    if not isinstance(result, dict) or not isinstance(result.get('candidates'), list):
        raise ValueError('The model returned no candidates')
    return result


_history = QuoteHistory()
_wikiquote = Wikiquote()
PARTS_OF_DAY = ('morning', 'afternoon', 'evening', 'night')


def learning_reminder(name, page='', history=None, wikiquote=None):
    """Return one spoken reminder for this visit, built around a quotation Wikiquote sources to its author."""
    if name not in SITES:
        raise ValueError('Unknown learning site')
    return quoted_line('learning_reminder', {'site': name, 'page': spoken(page, 160)}, history, wikiquote)


def welcome_greeting(away_hours, part_of_day, weekday, history=None, wikiquote=None, reminders=()):
    """Return the spoken welcome-back greeting, built around a quotation Wikiquote sources to its author. Reminders
    that are due, in the owner's own words, are passed on right after it in Jarvis's words, or plainly when the
    model's wording leaves one out."""
    if part_of_day not in PARTS_OF_DAY or not isinstance(away_hours, (int, float)) or away_hours < 0:
        raise ValueError('Unknown greeting context')
    texts = [' '.join(str(text).split())[:200] for text in reminders if str(text or '').strip()]
    answers = []
    line = quoted_line('welcome_greeting', {'awayHours': round(float(away_hours), 1), 'partOfDay': part_of_day,
                                            'weekday': spoken(weekday, 12), 'reminders': texts}, history, wikiquote, answers)
    if not texts:
        return line
    worded = [spoken(answer.get('reminders'), REMINDERS_LIMIT) for answer in answers]
    sentence = next((said for said in worded if passes_on(said, texts)), '')
    if not sentence:
        diagnostic('welcome_reminders_plain', reminders=len(texts), worded=sum(map(bool, worded)))
        sentence = passed_on(texts)
    return f'{line} {sentence}'


def key_words(text):
    """The words that say what a reminder is about, by their first five letters so a changed ending still counts."""
    return {word[:5] for word in words(text).split() if len(word) >= 4 and word not in REMINDER_FILLER}


def passes_on(sentence, reminders):
    """Whether a spoken sentence carries every reminder: at least half the words that say what each one is about."""
    said = key_words(sentence)
    for text in reminders:
        wanted = key_words(text)
        if wanted and 2 * len(wanted & said) < len(wanted):
            return False
    return bool(sentence)


def passed_on(reminders):
    """A plain sentence passing on the owner's reminders, for when the model's own wording is missing."""
    items = [' '.join(str(text).split()).rstrip(' .!?;,') for text in reminders if str(text or '').strip()]
    if not items:
        return ''
    joined = items[0] if len(items) == 1 else '; '.join(items[:-1]) + '; and ' + items[-1]
    return f'You asked me to remind you: {joined}.'


def quoted_line(task, details, history=None, wikiquote=None, answers=None):
    """Ask the model for candidates, speak the first whose quotation Wikiquote confirms, else the model's plain line.
    Each answer the model gives is added to answers when a list is passed."""
    history = history or _history
    wikiquote = wikiquote or _wikiquote
    wikiquote.reset_budget()
    past = history.read()
    avoid = past['quotes'][-AVOID:]
    unverified, plain, started = past['rejected'][-AVOID:], '', time.monotonic()
    for round_number in range(ROUNDS):
        # Another round is only worth asking for while Wikiquote can still check what comes back.
        if round_number and not wikiquote.available():
            break
        try:
            result = ask(task, details, avoid, unverified, past['last'])
        except Exception as exc:
            if not round_number:
                raise
            # The first round's plain reminder still stands when the model fails on the second.
            diagnostic('learning_reminder_round_failed', error_type=type(exc).__name__)
            break
        if answers is not None:
            answers.append(result)
        plain = spoken(result.get('plain'), 200) or plain
        for candidate in result['candidates'][:CANDIDATES]:
            if not isinstance(candidate, dict):
                continue
            author = spoken(candidate.get('author'), 70)
            quote = spoken(candidate.get('quote'), 240).strip(' "\'“”‘’')
            lead, close = spoken(candidate.get('lead'), 160), spoken(candidate.get('close'), 120)
            if lead and lead[-1] not in ',:;.!?—–-':
                lead += ','
            # The lead has to say whose words they are, and a reminder too long to hear comfortably is not checked.
            if (not author or not quote or not lead or not close or not named(author, lead)
                    or len(f'{lead} “{quote}” {close}') > SPOKEN_LIMIT
                    or repeated(author, quote, past['quotes']) or repeated(author, quote, past['rejected'])):
                continue
            try:
                source = wikiquote.source(author, quote)
            except (requests.RequestException, LookupError, ValueError, KeyError, IndexError, TypeError) as exc:
                # Wikiquote being unreachable says nothing about the quotation, so it is not marked as rejected.
                diagnostic('learning_quote_lookup_failed', error_type=type(exc).__name__)
                unverified.append({'author': author, 'quote': quote})
                continue
            if not source:
                history.reject(author, quote)
                past['rejected'].append({'author': author, 'quote': quote})
                unverified.append({'author': author, 'quote': quote})
                continue
            # Jarvis says the words exactly as Wikiquote's sourced section gives them.
            wording = source[1]
            reminder = spoken(f'{lead} “{wording}” {close}', SPOKEN_LIMIT + 40)
            if not reminder or repeated(author, wording, past['quotes']):
                continue
            history.remember(reminder, author, wording)
            diagnostic('learning_quote_verified', reply_chars=len(reminder),
                       duration_ms=round((time.monotonic() - started) * 1000))
            return reminder
    if plain:
        diagnostic('learning_quote_unverified', reason='no_sourced_candidate', attempts=len(unverified))
        history.remember(plain)
        return plain
    raise ValueError('The model returned no usable reminder')
