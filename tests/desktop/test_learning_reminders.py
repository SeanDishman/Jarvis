import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from desktop import learning_reminders
from desktop.learning_reminders import QuoteHistory, Wikiquote, learning_reminder, sourced_lines


def response(**fields):
    return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(fields)}]}]}


def candidate(author, quote, lead, close='Take that with you.'):
    return {'author': author, 'quote': quote, 'lead': lead, 'close': close}


FEYNMAN = candidate('Richard Feynman', 'What I cannot create, I do not understand.', 'Maxwell tonight, sir. Feynman put it well:',
                    'Try deriving one yourself afterwards.')
FAKE = candidate('Albert Einstein', 'Learning never exhausts the mind if you keep at it.', 'Sir, as Einstein said,')
SENECA = candidate('Seneca the Younger', 'While we are postponing, life speeds by.', 'As Seneca wrote, sir,')


class FakeWikiquote:
    def __init__(self, sourced=(), failure=None, wording=None):
        self.sourced, self.failure, self.wording, self.asked = set(sourced), failure, wording or {}, []
        self.open = True

    def reset_budget(self):
        pass

    def available(self):
        return self.open

    def source(self, author, quote):
        self.asked.append(author)
        if self.failure:
            raise self.failure
        return (author, self.wording.get(author, quote)) if author in self.sourced else None


@patch('desktop.learning_reminders.diagnostic')
class LearningReminderTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'learning-quotes.json'
        self.history = QuoteHistory(self.path)

    def write(self, replies, wikiquote, page='Lecture 12: Maxwell - electromagnetic waves', name='YouTube'):
        with patch('desktop.learning_reminders.local_request', side_effect=replies) as request:
            text = learning_reminder(name, page, history=self.history, wikiquote=wikiquote)
        return text, [json.loads(call.args[2]['input']) for call in request.call_args_list], request

    def test_the_first_confirmed_quotation_is_spoken_word_for_word_with_its_author(self, _log):
        wikiquote = FakeWikiquote({'Richard Feynman'})
        text, inputs, request = self.write([response(candidates=[FAKE, FEYNMAN, SENECA], plain='Plain words.')], wikiquote)
        self.assertEqual(text, 'Maxwell tonight, sir. Feynman put it well: “What I cannot create, I do not understand.” '
                               'Try deriving one yourself afterwards.')
        self.assertEqual(wikiquote.asked, ['Albert Einstein', 'Richard Feynman'])
        payload = request.call_args.args[2]
        self.assertEqual((payload['model'], payload['task']), ('openclaw/lockin', 'learning_reminder'))
        self.assertEqual(inputs, [{'site': 'YouTube', 'page': 'Lecture 12: Maxwell - electromagnetic waves', 'avoid': [],
                                   'unverified': [], 'previousReminder': ''}])
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(stored['quotes'], [{'author': 'Richard Feynman', 'quote': FEYNMAN['quote']}])
        self.assertEqual(stored['rejected'], [{'author': 'Albert Einstein', 'quote': FAKE['quote']}])
        # The reminder itself can mention the page, so it is kept in memory only.
        self.assertNotIn('Maxwell', self.path.read_text(encoding='utf-8'))

    def test_spoken_and_rejected_quotations_are_never_offered_or_looked_up_again(self, _log):
        wikiquote = FakeWikiquote({'Richard Feynman', 'Seneca the Younger'})
        self.write([response(candidates=[FAKE, FEYNMAN], plain='')], wikiquote)
        text, inputs, _request = self.write([response(candidates=[FEYNMAN, FAKE, SENECA], plain='')], wikiquote, page='')
        self.assertTrue(text.startswith('As Seneca wrote, sir, “While we are postponing'))
        self.assertEqual(wikiquote.asked, ['Albert Einstein', 'Richard Feynman', 'Seneca the Younger'])
        self.assertEqual(inputs[0]['avoid'], [{'author': 'Richard Feynman', 'quote': FEYNMAN['quote']}])
        self.assertEqual(inputs[0]['unverified'], [{'author': 'Albert Einstein', 'quote': FAKE['quote']}])
        self.assertTrue(inputs[0]['previousReminder'].startswith('Maxwell tonight'))

    def test_a_lead_that_does_not_name_the_author_is_skipped_without_a_lookup(self, _log):
        wikiquote = FakeWikiquote({'Richard Feynman', 'Seneca the Younger'})
        nameless = {**FEYNMAN, 'lead': 'Sir, a great physicist once said,'}
        text, _inputs, _request = self.write([response(candidates=[nameless, SENECA], plain='')], wikiquote)
        self.assertIn('Seneca', text)
        self.assertEqual(wikiquote.asked, ['Seneca the Younger'])

    def test_wikiquotes_wording_replaces_a_slightly_different_recollection(self, _log):
        sourced = 'For the things we have to learn before we can do, we learn by doing.'
        aristotle = candidate('Aristotle', 'For the things we have to learn before we can do them, we learn by doing them.',
                              'As Aristotle put it, sir,', 'So build something with it.')
        wikiquote = FakeWikiquote({'Aristotle'}, wording={'Aristotle': sourced})
        text, _inputs, _request = self.write([response(candidates=[aristotle], plain='')], wikiquote)
        self.assertEqual(text, f'As Aristotle put it, sir, “{sourced}” So build something with it.')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['quotes'], [{'author': 'Aristotle', 'quote': sourced}])

    def test_with_nothing_confirmed_a_second_round_runs_then_the_models_plain_reminder(self, _log):
        wikiquote = FakeWikiquote()
        replies = [response(candidates=[FAKE], plain='First plain.'), response(candidates=[SENECA], plain='Second plain.')]
        text, inputs, _request = self.write(replies, wikiquote)
        self.assertEqual(text, 'Second plain.')
        self.assertEqual(inputs[1]['unverified'], [{'author': 'Albert Einstein', 'quote': FAKE['quote']}])
        self.assertEqual(wikiquote.asked, ['Albert Einstein', 'Seneca the Younger'])

    def test_a_failed_second_round_keeps_the_first_rounds_plain_reminder(self, _log):
        replies = [response(candidates=[FAKE], plain='First plain.'), OSError('model timed out')]
        text, _inputs, _request = self.write(replies, FakeWikiquote())
        self.assertEqual(text, 'First plain.')

    def test_no_second_round_once_wikiquote_cannot_check_anything(self, _log):
        wikiquote = FakeWikiquote()
        wikiquote.open = False
        text, inputs, _request = self.write([response(candidates=[FAKE], plain='Only round.')], wikiquote)
        self.assertEqual((text, len(inputs)), ('Only round.', 1))

    def test_an_unreachable_wikiquote_rejects_nothing(self, _log):
        wikiquote = FakeWikiquote({'Richard Feynman'}, failure=LookupError('Wikiquote rate limit'))
        replies = [response(candidates=[FEYNMAN], plain='Plain one.'), response(candidates=[FEYNMAN], plain='Plain two.')]
        text, _inputs, _request = self.write(replies, wikiquote)
        self.assertEqual(text, 'Plain two.')
        self.assertFalse(self.path.exists())

    def test_invalid_model_responses_cannot_turn_into_canned_copy(self, _log):
        for data in (response(reminder='Old shape.'), response(candidates=[], plain=''),
                     response(candidates=[{**FEYNMAN, 'lead': ''}], plain='')):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.write([data, data], FakeWikiquote({'Richard Feynman'}))

    def test_the_welcome_greeting_goes_through_the_same_quotation_checks(self, _log):
        wikiquote = FakeWikiquote({'Seneca the Younger'})
        greeting = {**SENECA, 'lead': 'Good morning, sir. As Seneca wrote,', 'close': "Let's get to work."}
        with patch('desktop.learning_reminders.local_request', side_effect=[response(candidates=[FAKE, greeting], plain='')]) as request:
            text = learning_reminders.welcome_greeting(7.0, 'morning', 'Thursday', history=self.history, wikiquote=wikiquote)
        self.assertEqual(text, "Good morning, sir. As Seneca wrote, “While we are postponing, life speeds by.” Let's get to work.")
        payload = request.call_args.args[2]
        self.assertEqual((payload['model'], payload['task']), ('openclaw/lockin', 'welcome_greeting'))
        self.assertEqual(json.loads(payload['input']), {'awayHours': 7.0, 'partOfDay': 'morning', 'weekday': 'Thursday',
                                                        'reminders': [], 'avoid': [], 'unverified': [], 'previousReminder': ''})
        self.assertEqual(wikiquote.asked, ['Albert Einstein', 'Seneca the Younger'])
        for hours, part in ((7, 'teatime'), (-1, 'morning'), ('7', 'morning')):
            with self.subTest(hours=hours, part=part), self.assertRaises(ValueError):
                learning_reminders.welcome_greeting(hours, part, 'Thursday', history=self.history, wikiquote=wikiquote)

    def test_unknown_sites_are_refused(self, _log):
        with self.assertRaises(ValueError):
            learning_reminder('X', history=self.history, wikiquote=FakeWikiquote())


class FakeResponse:
    def __init__(self, data=None, status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise learning_reminders.requests.HTTPError(str(self.status_code))

    def json(self):
        return self.data


AUTHOR_PAGE = '''Example Author (1900-1980) was a writer.
== Quotes ==
=== Essays (1950) ===
These are the exact words, as published.
Essays, p. 12
In the fields of observation chance favours only the prepared mind.
=== Disputed ===
This was never said by the author.
== Misattributed ==
Someone else wrote these words.
== Quotes about Example Author ==
A critic said the author was brilliant beyond measure.
'''
BOOK_PAGE = '''Example Book (1950) is a book by Example Author.
== Quotes ==
A sentence that lives in the book page only.
'''
THEME_PAGE = '''Time is what clocks measure.
== Quotes ==
A line that appears on a theme page only.
Example Author
'''


class WikiquoteTests(unittest.TestCase):
    def reader(self, hits):
        pages = {'Example Author': AUTHOR_PAGE, 'Example Book': BOOK_PAGE, 'Time': THEME_PAGE, 'October 27': THEME_PAGE}
        self.requests = []
        def get(_url, params, headers, timeout):
            self.requests.append(params)
            self.assertIn('JarvisDesktop', headers['User-Agent'])
            if params.get('generator') == 'search':
                self.assertTrue(params['exintro'])
                found = [{'title': title, 'index': rank, 'extract': pages[title].split('\n==')[0]}
                         for rank, title in reversed(list(enumerate(hits, 1)))]
                return FakeResponse({'query': {'pages': found}} if found else {'batchcomplete': True})
            title = params['titles']
            return FakeResponse({'query': {'pages': [{'title': title, 'extract': pages[title]} if title in pages
                                                     else {'title': title, 'missing': True}]}})
        return Wikiquote(get=get, clock=lambda: 0.0, sleep=lambda _seconds: None)

    def test_sourced_words_count_on_the_persons_page_and_their_works_only(self):
        everywhere = ['October 27', 'Time', 'Example Author', 'Example Book']
        self.assertEqual(self.reader(everywhere).source('Example Author', 'These are the exact words as published'),
                         ('Example Author', 'These are the exact words, as published.'))
        self.assertEqual(self.reader(everywhere).source('Example Author', 'A sentence that lives in the book page only.'),
                         ('Example Book', 'A sentence that lives in the book page only.'))
        for doubtful in ('This was never said by the author.', 'Someone else wrote these words.',
                         'A critic said the author was brilliant beyond measure.', 'A line that appears on a theme page only.'):
            with self.subTest(quote=doubtful):
                self.assertIsNone(self.reader(everywhere).source('Example Author', doubtful))
        self.assertIsNone(self.reader([]).source('Example Author', 'A sentence that lives in the book page only.'))
        self.assertIsNone(self.reader(everywhere).source('Other Writer', 'These are the exact words as published'))

    def test_a_close_recollection_is_spoken_in_wikiquotes_own_words(self):
        self.assertEqual(self.reader([]).source('Example Author', 'Chance favors only the prepared mind'),
                         ('Example Author', 'Chance favours only the prepared mind.'))
        for different in ('The prepared mind is a blessing to every working scientist', 'Fortune favours the bold'):
            with self.subTest(quote=different):
                self.assertIsNone(self.reader([]).source('Example Author', different))

    def test_the_persons_page_comes_first_and_pages_are_fetched_once(self):
        reader = self.reader(['Time', 'Example Author'])
        self.assertEqual(reader.source('Example Author', 'These are the exact words as published')[0], 'Example Author')
        self.assertIsNone(reader.source('Example Author', 'This was never said by the author.'))
        # The theme page that also carries the line is never even fetched.
        self.assertEqual([params.get('generator') or params['titles'] for params in self.requests], ['Example Author', 'search'])
        self.assertEqual(self.requests[1]['gsrsearch'], '"this was never said by the author"')

    def test_a_rate_limit_pauses_every_lookup(self):
        now = [0.0]
        calls = []
        def get(*_args, **_kwargs):
            calls.append(now[0])
            return FakeResponse(status=429, headers={'retry-after': '45'})
        reader = Wikiquote(get=get, clock=lambda: now[0], sleep=lambda _seconds: None)
        with self.assertRaises(LookupError):
            reader.source('Example Author', 'These are the exact words as published')
        now[0] += 44
        with self.assertRaises(LookupError):
            reader.source('Example Author', 'These are the exact words as published')
        self.assertEqual(calls, [0.0])

    def test_requests_are_spaced_budgeted_and_kept_under_wikimedias_anonymous_limit(self):
        now, waits = [0.0], []
        reader = Wikiquote(get=lambda *_a, **_k: FakeResponse({'query': {'search': []}}), clock=lambda: now[0], sleep=waits.append)
        for _ in range(learning_reminders.LOOKUP_BUDGET):
            reader.search('words', 1)
        self.assertEqual(len(waits), learning_reminders.LOOKUP_BUDGET - 1)
        with self.assertRaises(LookupError):
            reader.search('words', 1)
        # A new reminder gets a fresh budget, but not more requests inside the same minute.
        reader.reset_budget()
        with self.assertRaises(LookupError):
            reader.search('words', 1)
        now[0] += learning_reminders.WINDOW_SECONDS
        reader.search('words', 1)
        self.assertLessEqual(learning_reminders.WINDOW_REQUESTS, 10)

    def test_sourced_lines_skip_the_introduction_and_doubtful_sections(self):
        self.assertEqual(list(sourced_lines(AUTHOR_PAGE)), ['These are the exact words, as published.', 'Essays, p. 12',
                                                            'In the fields of observation chance favours only the prepared mind.'])


if __name__ == '__main__':
    unittest.main()
