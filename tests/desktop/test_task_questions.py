import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from desktop import questions as questions_module
from desktop.controller import Controller
from desktop.intent import classify_request, validate_decision
from desktop.questions import ASK_AGAIN_AFTER, GIVE_UP_AFTER, QUIET_SECONDS, TEXT_LIMIT, Questions, with_answers
from desktop.research import progress_reply
from desktop.task_progress import current_tasks
from tests.desktop.intent_fixtures import decision

QUESTION = 'Which version would you prefer, sir? I can switch to GPT six point two Astra or GPT six Astra.'


def wait_for(condition, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(.01)
    return False


def reply(text, ok=True, clarify=False):
    return {'ok': ok, 'text': text, 'needsClarification': clarify}


def plan():
    """The request from the failed task: switch models, then push the folder, which needs the switch first."""
    return validate_decision(decision(action='compound', model_tier='sol', steps=[
        decision('list_models', 'select_model', request='Switch yourself to GPT Astra.', depends_on=[]),
        decision(action='computer', request='Find the code folder and push it to its GitHub repository.', depends_on=[0]),
        decision(request='Tell me the time.', depends_on=[]),
    ]))


class Worker:
    """A task step's controller: answers from a script and records what each step was asked to do."""
    def __init__(self, script, seen):
        self.script, self.seen = script, seen

    def respond(self, text, speak=True):
        self.seen.append(text)
        return self.script(text)


class TaskQuestionTests(unittest.TestCase):
    def setUp(self):
        self.c = Controller(queue.Queue())
        self.c.breaks = SimpleNamespace(active=False)
        self.addCleanup(self.c.browser.close)
        self.addCleanup(self.c.research.close)
        self.addCleanup(self.c.tasks.close)
        self.seen = []

    def start(self, text, selected, script):
        patcher = patch.object(self.c, 'make_task_agent', side_effect=lambda *_args: Worker(script, self.seen))
        patcher.start()
        self.addCleanup(patcher.stop)
        job = self.c.tasks.prepare(text, 'tasks', decision=selected)
        self.c.tasks.release(job['id'])
        return job['id']

    def status(self, identifier):
        return self.c.activity_status(identifier)['status']

    def test_a_question_pauses_the_task_and_holds_only_the_actions_that_need_its_answer(self):
        def script(text):
            if text.startswith('Switch yourself'):
                return reply('Switched to GPT 6.2 Astra, sir.') if '6.2' in text else reply(QUESTION, clarify=True)
            return reply('Pushed the folder to GitHub, sir.') if 'push' in text else reply('It is 5 PM, sir.')
        identifier = self.start('Start GPT Astra, then push the folder to GitHub. And what time is it?', plan(), script)
        self.assertTrue(wait_for(lambda: self.status(identifier) == 'waiting'), self.c.activity_status(identifier))
        job = self.c.activity_status(identifier)
        self.assertEqual((job['note'], job['pendingQuestion'], job['summary']), ('Needs your answer', QUESTION, QUESTION))
        self.assertEqual(self.seen, ['Switch yourself to GPT Astra.', 'Tell me the time.'],
                         'The push waits for the switch; the unrelated action still runs')
        self.assertEqual([row['request'] for row in job['actionResults']], ['Tell me the time.'])
        reported = self.c.completed_tasks.get_nowait()
        self.assertEqual((reported['status'], reported['summary']), ('waiting', QUESTION), 'He asks the question when he is free')
        self.assertEqual(self.c.tasks.questions()[0]['held'], ['Find the code folder and push it to its GitHub repository.'])

        self.assertTrue(self.c.tasks.answer(identifier, 'The 6.2 one')['ok'])
        self.assertTrue(wait_for(lambda: self.status(identifier) == 'completed'), self.c.activity_status(identifier))
        retried = self.seen[2]
        self.assertTrue(retried.startswith('Switch yourself to GPT Astra.\n'))
        self.assertIn('You asked the owner: ' + QUESTION, retried)
        self.assertIn('The owner answered: The 6.2 one', retried)
        self.assertEqual(self.seen[3], 'Find the code folder and push it to its GitHub repository.')
        summary = self.c.activity_status(identifier)['summary']
        for part in ('Switched to GPT 6.2 Astra', 'Pushed the folder', 'It is 5 PM'):
            self.assertIn(part, summary)
        self.assertEqual(len(self.c.activity_status(identifier)['actionResults']), 3)
        self.assertEqual(self.c.tasks.questions(), [])

    def test_a_single_request_waits_and_carries_on_with_the_answer(self):
        selected = validate_decision(decision(action='computer', model_tier='sol'))
        answers = iter([reply('Which folder should I push, sir?', clarify=True), reply('Pushed it, sir.')])
        identifier = self.start('Push my folder to GitHub.', selected, lambda _text: next(answers))
        self.assertTrue(wait_for(lambda: self.status(identifier) == 'waiting'))
        self.c.tasks.answer(identifier, 'The jarvis one in Downloads')
        self.assertTrue(wait_for(lambda: self.status(identifier) == 'completed'))
        self.assertEqual(self.seen[1], 'Push my folder to GitHub.\nYou asked the owner: Which folder should I push, sir?\n'
                                       'The owner answered: The jarvis one in Downloads')
        self.assertEqual(self.c.activity_status(identifier)['summary'], 'Pushed it, sir.')

    def test_a_waiting_task_can_be_cancelled_and_then_takes_no_answer(self):
        identifier = self.start('Push my folder.', validate_decision(decision(action='computer')),
                                lambda _text: reply('Which folder, sir?', clarify=True))
        self.assertTrue(wait_for(lambda: self.status(identifier) == 'waiting'))
        self.assertTrue(self.c.activity_cancel(identifier)['ok'])
        self.assertEqual(self.status(identifier), 'cancelled')
        self.assertFalse(self.c.tasks.answer(identifier, 'The jarvis one')['ok'])
        self.assertEqual(self.c.tasks.questions(), [])

    def test_a_task_that_never_gets_its_answer_stops_waiting(self):
        identifier = self.start('Push my folder.', validate_decision(decision(action='computer')),
                                lambda _text: reply('Which folder, sir?', clarify=True))
        self.assertTrue(wait_for(lambda: self.status(identifier) == 'waiting'))
        self.assertTrue(self.c.tasks.expire(identifier))
        job = self.c.activity_status(identifier)
        self.assertEqual((job['status'], job['note'], job['summary']), ('failed', 'No answer', 'Which folder, sir?'))
        self.assertFalse(self.c.tasks.expire(identifier))

    def test_waiting_tasks_still_count_toward_the_four_at_once(self):
        for index in range(4):
            self.c.tasks.update(self.c.tasks.jobs[self.c.tasks.prepare(f'Task {index}', 'tasks')['id']], status='waiting')
        with self.assertRaisesRegex(RuntimeError, 'waiting for your answer'):
            self.c.tasks.prepare('One more', 'tasks')

    def test_status_and_progress_show_the_question(self):
        job = {'ok': True, 'status': 'waiting', 'scope': 'tasks', 'note': 'Needs your answer', 'pendingQuestion': 'Which folder, sir?',
               'summary': 'Which folder, sir?', 'id': 'task-a', 'question': 'Push my folder', 'stage': 'needs_answer'}
        self.assertEqual(progress_reply(job), ('Which folder, sir?', []))
        self.assertEqual(current_tasks([job])[0]['detail'], 'Needs your answer')


class AnswerTests(unittest.TestCase):
    """The owner's answer reaches what was asked about, however long it took him."""
    def setUp(self):
        self.c = Controller(queue.Queue())
        self.c.breaks = SimpleNamespace(active=False)
        self.addCleanup(self.c.browser.close)
        self.addCleanup(self.c.research.close)
        self.addCleanup(self.c.tasks.close)
        self.seen = []
        self.worker_replies = []
        patcher = patch.object(self.c, 'make_task_agent', side_effect=lambda *_args: Worker(lambda text: self.worker_replies.pop(0), self.seen))
        patcher.start()
        self.addCleanup(patcher.stop)

    def paused_task(self):
        self.worker_replies[:] = [reply('Which folder should I push, sir?', clarify=True), reply('Pushed it, sir.')]
        job = self.c.tasks.prepare('Push my folder to GitHub.', 'computer', decision=validate_decision(decision(action='computer')))
        self.c.tasks.release(job['id'])
        self.assertTrue(wait_for(lambda: self.c.tasks.waiting(job['id'])))
        return job['id']

    def test_his_answer_resumes_the_paused_task_after_the_acknowledgement(self):
        identifier = self.paused_task()
        contexts = []
        def classify(text, _session, context):
            contexts.append(context)
            return validate_decision(decision(action='task_answer', task_id=identifier), context)
        with patch('desktop.controller.classify_request', side_effect=classify), \
             patch('desktop.controller.acknowledge', return_value='Pushing the jarvis folder now, sir.') as ack:
            result = self.c.respond('The jarvis one in Downloads', speak=False)
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['text'], result['tools'], result['taskReference']),
                         ('Pushing the jarvis folder now, sir.', ['task_answer'], identifier))
        waiting = contexts[0]['waitingQuestions']
        self.assertEqual([(row['id'], row['question']) for row in waiting], [(identifier, 'Which folder should I push, sir?')])
        self.assertEqual(ack.call_args.args[2]['answering']['question'], 'Which folder should I push, sir?')
        self.assertTrue(wait_for(lambda: self.c.activity_status(identifier)['status'] == 'completed'))
        self.assertIn('The owner answered: The jarvis one in Downloads', self.seen[-1])
        self.assertEqual(self.c.last_task_id, identifier)
        self.assertIsNone(self.c.questions.find(identifier))

    def test_a_question_asked_before_anything_started_is_answered_as_one_request(self):
        first = validate_decision(decision(action='computer', needs_clarification=True))
        model = {'taskStatus': 'needs_clarification', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': 'Which folder should I push, sir?'}]}]}
        with patch('desktop.controller.classify_request', return_value=first), \
             patch.object(Controller, 'ask_model', return_value=model):
            asked = self.c.respond('Push my folder to GitHub.', speak=False)
        self.assertTrue(asked['needsClarification'])
        waiting = self.c.questions.context()
        self.assertEqual([row['question'] for row in waiting], ['Which folder should I push, sir?'])

        utterances = []
        def classify(text, _session, context):
            utterances.append(text)
            if len(utterances) == 1:
                return validate_decision(decision(action='task_answer', task_id=context['waitingQuestions'][0]['id']), context)
            self.assertEqual(context['waitingQuestions'], [], 'The answered question is no longer waiting')
            return validate_decision(decision(action='computer'), context)
        self.worker_replies[:] = [reply('Pushed it, sir.')]
        with patch('desktop.controller.classify_request', side_effect=classify), \
             patch('desktop.controller.acknowledge', return_value='Pushing the jarvis folder, sir.'):
            result = self.c.respond('The jarvis one in Downloads', speak=False)
        combined = ('Push my folder to GitHub.\nYou asked the owner: Which folder should I push, sir?\n'
                    'The owner answered: The jarvis one in Downloads')
        self.assertEqual(utterances, ['The jarvis one in Downloads', combined])
        self.assertEqual(self.c.tasks.jobs[result['taskId']]['question'], combined, 'The task gets the whole request')
        self.assertTrue(wait_for(lambda: self.c.activity_status(result['taskId'])['status'] == 'completed'))
        self.assertEqual(self.seen, [combined])
        self.assertEqual(self.c.questions.open(), [])

    def test_something_new_replaces_a_question_asked_before_anything_started_but_a_paused_task_keeps_waiting(self):
        identifier = self.paused_task()
        self.c.questions.raised('Open my notes.', 'Which notes app, sir?')
        model = {'taskStatus': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'It is 5 PM, sir.'}]}]}
        with patch('desktop.controller.classify_request', return_value=validate_decision(decision())), \
             patch.object(Controller, 'ask_model', return_value=model):
            self.c.respond('What time is it?', speak=False)
        self.assertEqual([row['id'] for row in self.c.questions.open()], [identifier])

    def test_the_question_is_spoken_once_he_is_free_and_not_after_it_was_answered(self):
        identifier = self.paused_task()
        self.c.voice_ready = True
        with patch.object(self.c, 'speak') as speak:
            self.c.deliver_task_completion()
        speak.assert_called_once_with('Which folder should I push, sir?')
        self.assertEqual(self.c.questions.asked[identifier]['count'], 1)
        self.assertEqual(self.c.last_task_id, identifier)
        self.c.completed_tasks.put({**self.c.activity_status(identifier), 'status': 'waiting'})
        self.c.tasks.answer(identifier, 'The jarvis one', release=False)
        with patch.object(self.c, 'speak') as speak:
            self.c.deliver_task_completion()
        speak.assert_not_called()

    def test_an_answer_needs_a_question_that_is_still_waiting(self):
        selected = decision(action='task_answer', task_id='task-gone')
        with self.assertRaisesRegex(RuntimeError, 'no longer waiting'):
            validate_decision(dict(selected), {'waitingQuestions': [{'id': 'task-other'}]})
        accepted = validate_decision(dict(selected, capabilities=['start_computer_task']), {'waitingQuestions': [{'id': 'task-gone'}]})
        self.assertEqual(accepted['capabilities'], [], 'The host carries the answer; no tools are granted')

    def test_waiting_questions_give_way_before_the_request_when_the_context_is_too_long(self):
        sent = []
        def local(_port, _path, payload, timeout):
            sent.append(payload['input'])
            return {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"action": "nope"}'}]}]}
        rows = [{'id': f'task-{index}', 'request': 'r' * 240, 'question': 'q' * 320, 'asked': 'just now'} for index in range(3)]
        with patch('desktop.intent.local_request', side_effect=local), self.assertRaises(RuntimeError):
            classify_request('x' * 4000, 'fixture', {'waitingQuestions': rows, 'lastAnswer': 'a' * 1000})
        self.assertLessEqual(len(sent[0]), 5800)
        self.assertIn('x' * 4000, sent[0])


class Room:
    """Just enough of Jarvis to ask a question again: whether he is free, and what he says."""
    def __init__(self, tasks=None):
        self.tasks = tasks
        self.voice_ready, self.muted, self.state = True, False, 'ready'
        self.breaks = SimpleNamespace(active=False)
        self.busy = threading.Lock()
        self.quiet_since = -10_000.0
        self.said = []
        self.cancelled = threading.Event()
        self.last_answer_text = self.last_answer_domain = self.last_domain = self.last_task_id = None
        self.last_command_time = 0

    def speak(self, text):
        self.said.append(text)

    def release_response(self):
        self.busy.release()


class PausedTasks:
    def __init__(self, *rows):
        self.rows, self.expired = list(rows), []

    def questions(self):
        return [dict(row) for row in self.rows if row['id'] not in self.expired]

    def expire(self, identifier):
        self.expired.append(identifier)
        return True


class AskingAgainTests(unittest.TestCase):
    def setUp(self):
        self.now = [50_000.0]
        self.writes = []
        def writer(row, asked_ago, times, previous):
            self.writes.append((asked_ago, times, previous))
            return f'Still need this, sir: {row["question"]} ({times})'
        self.writer = writer

    def questions(self, room):
        return Questions(room, writer=self.writer, clock=lambda: self.now[0], monotonic=lambda: self.now[0])

    def wait(self, seconds):
        self.now[0] += seconds

    def test_he_asks_again_a_little_later_each_time_then_gives_up(self):
        room = Room()
        q = self.questions(room)
        q.raised('Push my folder to GitHub.', 'Which folder should I push, sir?')
        for gap, times in zip(ASK_AGAIN_AFTER, (1, 2, 3)):
            self.wait(gap - 1)
            self.assertIsNone(q.due(), f'Not yet after {gap - 1} seconds')
            self.wait(1)
            row = q.due()
            self.assertIsNotNone(row, f'Due after {gap} seconds')
            q.ask_again(row)
            self.assertEqual(room.said[-1], f'Still need this, sir: Which folder should I push, sir? ({times})')
        self.assertEqual([write[1] for write in self.writes], [1, 2, 3])
        self.assertEqual(self.writes[0][0], 'a minute ago')
        self.assertEqual(self.writes[1][2], room.said[0], 'He knows what he said last time')
        self.wait(GIVE_UP_AFTER - 1)
        self.assertIsNone(q.due())
        self.assertEqual(len(q.open()), 1, 'Still waiting just before he gives up')
        self.wait(1)
        self.assertIsNone(q.due())
        self.assertEqual(q.open(), [], 'Given up after the last time he asked')

    def test_a_paused_task_stops_waiting_when_he_gives_up(self):
        tasks = PausedTasks({'id': 'task-a', 'taskId': 'task-a', 'request': 'Push my folder', 'question': 'Which folder, sir?',
                             'raised': self.now[0], 'held': ['Open GitHub Desktop']})
        q = self.questions(Room(tasks))
        q.spoken('task-a')
        self.wait(ASK_AGAIN_AFTER[0])
        self.assertEqual(q.due()['held'], ['Open GitHub Desktop'])
        for _ in ASK_AGAIN_AFTER:
            q.spoken('task-a')
        self.wait(GIVE_UP_AFTER)
        q.due()
        self.assertEqual(tasks.expired, ['task-a'])

    def test_he_waits_for_a_quiet_moment_and_never_talks_over_anyone(self):
        room = Room()
        q = self.questions(room)
        q.raised('Push my folder.', 'Which folder, sir?')
        self.wait(ASK_AGAIN_AFTER[0])
        for change in ({'state': 'listening'}, {'state': 'speaking'}, {'muted': True}, {'voice_ready': False},
                       {'quiet_since': self.now[0] - QUIET_SECONDS + 1}):
            busy = Room()
            busy.__dict__.update(change)
            q.controller = busy
            with self.subTest(change=change):
                self.assertFalse(q.quiet())
        q.controller = room
        with room.busy:
            self.assertFalse(q.quiet())
        self.assertTrue(q.quiet())
        with patch.object(questions_module.threading, 'Thread') as thread:
            q.remind()
        thread.assert_called_once()

    def test_he_does_not_ask_once_the_question_is_answered(self):
        room = Room()
        q = self.questions(room)
        identifier = q.raised('Push my folder.', 'Which folder, sir?')
        self.wait(ASK_AGAIN_AFTER[0])
        row = q.due()
        q.close(identifier)
        q.ask_again(row)
        self.assertEqual(room.said, [])

    def test_when_no_words_come_back_he_asks_the_question_as_he_first_did(self):
        room = Room()
        q = Questions(room, writer=Mock(side_effect=TimeoutError), clock=lambda: self.now[0], monotonic=lambda: self.now[0])
        q.raised('Push my folder.', 'Which folder, sir?')
        self.wait(ASK_AGAIN_AFTER[0])
        q.ask_again(q.due())
        self.assertEqual(room.said, ['Which folder, sir?'])

    def test_his_words_are_written_once_per_attempt(self):
        room = Room()
        q = self.questions(room)
        q.raised('Push my folder.', 'Which folder, sir?')
        self.wait(ASK_AGAIN_AFTER[0])
        row = q.due()
        with room.busy:
            q.ask_again(row)
        self.assertEqual(room.said, [], 'Someone started talking, so he holds the question')
        q.ask_again(q.due())
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(len(room.said), 1)

    def test_the_model_line_must_be_short_spoken_words(self):
        good = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"line": "Sir, which folder should I push?"}'}]}]}
        bad = {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '{"line": "**Which** folder?"}'}]}]}
        row = {'question': 'Which folder, sir?', 'request': 'Push my folder', 'held': []}
        with patch.object(questions_module, 'local_request', return_value=good) as local:
            self.assertEqual(questions_module.reminder_line(row, 'a minute ago', 1), 'Sir, which folder should I push?')
        self.assertEqual((local.call_args.args[2]['model'], local.call_args.args[2]['task']), ('openclaw/lockin', 'question_reminder'))
        with patch.object(questions_module, 'local_request', return_value=bad), self.assertRaises(ValueError):
            questions_module.reminder_line(row, 'a minute ago', 1)

    def test_answers_stay_within_one_request(self):
        exchanges = [{'question': f'Question {index}?', 'answer': 'a' * 900} for index in range(4)]
        text = with_answers('r' * 4000, exchanges)
        self.assertLessEqual(len(text), TEXT_LIMIT)
        self.assertNotIn('Question 1?', text)
        self.assertIn('You asked the owner: Question 3?', text)
        self.assertEqual(with_answers('Push my folder.', []), 'Push my folder.')


if __name__ == '__main__':
    unittest.main()
