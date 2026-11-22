"""Check, with the real models, that a question no longer fails a task.

An isolated model service serves the prompts on disk. The task's own actions are scripted, so nothing on the computer
changes: the first action asks the question from the failed task in the owner's screenshot, and the action after it
needs that answer. Everything else is real: how the request is understood, how the owner's answer finds its way back
to the paused task, what Jarvis says when it resumes, and the words he chooses when he asks again.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from desktop.common import local_request  # noqa: E402
from desktop.controller import Controller  # noqa: E402
from desktop.intent import classify_request  # noqa: E402

REQUEST = ("You find the folder. Start a GPT Astra. Have it find the folder and push it to GitHub, okay? "
           "It's already all set up, basically. You just need to push it.")
QUESTION = 'Which version would you prefer, sir? I can switch to GPT six point two Astra or GPT six Astra.'


@contextmanager
def model_service():
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix='task-questions-') as temporary:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        environment = {**os.environ, 'JARVIS_MODEL_PORT': str(port)}
        with open(Path(temporary) / 'model.log', 'w', encoding='utf-8') as output:
            server = subprocess.Popen([str(ROOT / '.runtime/node-v24.21.0-win-x64/node.exe'), str(ROOT / 'phone/luna-server.mjs')],
                                      cwd=ROOT, env=environment, stdout=output, stderr=output, stdin=subprocess.DEVNULL,
                                      creationflags=subprocess.CREATE_NO_WINDOW)

            def request(service, route, payload=None, timeout=10):
                return local_request(port if service == 18791 else service, route, payload, timeout)
            try:
                deadline = time.monotonic() + 40
                while True:
                    try:
                        request(18791, '/health', timeout=1)
                        break
                    except Exception:
                        if server.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError('Isolated model service failed to start') from None
                        time.sleep(.2)
                yield request
            finally:
                server.terminate()
                server.wait(timeout=10)


class Step:
    """A scripted task action: records what it was asked to do, and asks which Astra until it has that answer."""
    def __init__(self, seen, done):
        self.seen, self.done = seen, done

    def respond(self, text, speak=True):
        self.seen.append(text)
        lower = text.lower()
        results = []
        if 'astra' in lower:
            if 'You asked the owner: ' + QUESTION not in text:
                return {'ok': True, 'text': QUESTION, 'needsClarification': True}
            results.append('Switched to GPT 6.2 Astra, sir.')
        if 'push' in lower or 'github' in lower:
            results.append('Pushed the folder to GitHub, sir.')
        self.done.extend(results)
        return {'ok': True, 'text': ' '.join(results) or 'Done, sir.'}


def wait_for(condition, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(.05)
    return False


def main():
    report, failures = {}, []

    def check(name, passed, detail=None):
        report[name] = {'passed': bool(passed), **({'detail': detail} if detail is not None else {})}
        if not passed:
            failures.append(name)

    with model_service() as request:
        redirect = [patch(f'desktop.{module}.local_request', side_effect=request)
                    for module in ('intent', 'acknowledgements', 'questions')]
        for patcher in redirect:
            patcher.start()
        c = Controller(queue.Queue())
        c.breaks = SimpleNamespace(active=False)
        seen, said, classified, done = [], [], [], []
        real = classify_request

        def classify(*args, **kwargs):
            classified.append(real(*args, **kwargs))
            print('CLASSIFIED', json.dumps({key: classified[-1][key] for key in ('action', 'task_id', 'needs_clarification')}), flush=True)
            return classified[-1]
        try:
            with patch.object(c, 'make_task_agent', side_effect=lambda *_args: Step(seen, done)), \
                 patch.object(c, 'speak', side_effect=said.append), \
                 patch('desktop.controller.classify_request', side_effect=classify):
                c.voice_ready = c.microphone_ready = True
                c.idle()
                # The request from the screenshot, understood by the real classifier.
                started = c.respond(REQUEST, speak=False)
                identifier = started.get('taskId')
                print('REPLIED', json.dumps(started.get('text')), flush=True)
                if not identifier:
                    # He asked before starting anything, as he did in the owner's session. That question waits too,
                    # and the owner's answer becomes part of the request.
                    waiting = c.questions.open()
                    check('first_question_waits', len(waiting) == 1 and not waiting[0]['taskId'], waiting)
                    count = len(classified)
                    follow = c.respond("You find it yourself, it's the jarvis folder in my Downloads. "
                                       "Start GPT Astra and have it push it to GitHub.", speak=False)
                    print('REPLIED', json.dumps(follow.get('text')), flush=True)
                    check('first_answer_routed', len(classified) == count + 2 and classified[count]['action'] == 'task_answer',
                          [row['action'] for row in classified[count:]])
                    identifier = follow.get('taskId')
                    check('answer_and_request_became_one_task', bool(identifier) and 'jarvis folder' in c.tasks.jobs[identifier]['question'],
                          identifier and c.tasks.jobs[identifier]['question'])
                if not identifier:
                    raise RuntimeError('No task started: ' + json.dumps(report))
                check('task_paused_on_its_question', wait_for(lambda: c.tasks.waiting(identifier)),
                      c.activity_status(identifier).get('status'))
                job = c.activity_status(identifier)
                print('PLAN', json.dumps([step['request'] for step in c.tasks.jobs[identifier]['_decision'].get('steps', [])]), flush=True)
                check('status_is_waiting_not_failed', job['status'] == 'waiting' and job['note'] == 'Needs your answer',
                      [job['status'], job['note']])
                check('push_held_not_skipped', not any('Pushed' in result for result in done), done)

                # He asks as soon as he is free, and the clock for asking again starts then.
                c.deliver_task_completion()
                check('question_spoken', said[-1:] == [QUESTION], said)

                # He asks again, in his own words, when the owner has not answered.
                lines = []
                for attempt in (1, 2):
                    c.questions.asked[identifier]['at'] -= 3600
                    c.quiet_since -= 3600
                    row = c.questions.due()
                    c.questions.ask_again(row)
                    lines.append(said[-1])
                    print(f'ASKED AGAIN {attempt}', json.dumps(said[-1]), flush=True)
                folded = [line.lower() for line in lines]
                check('asked_again_in_new_words', len(lines) == 2 and lines[0] != QUESTION and lines[0] != lines[1], lines)
                check('asked_again_keeps_both_choices', all(('6.2' in line or 'six point two' in line)
                                                            and ('6 astra' in line or 'six astra' in line) for line in folded), lines)
                check('asked_again_counted', c.questions.asked[identifier]['count'] == 3, c.questions.asked[identifier])

                # Something unrelated is not taken as the answer, and the task keeps waiting.
                context = {'topic': None, 'now': 'Thursday 7:50 PM', 'lastRequest': '', 'lastAnswer': '',
                           'recentTasks': c.task_context(), 'lastTaskId': identifier, 'waitingQuestions': c.questions.context()}
                unrelated = classify_request("What's the weather like in London today?", 'verify-task-questions', context)
                print('UNRELATED', json.dumps({key: unrelated[key] for key in ('action', 'task_id', 'capabilities')}), flush=True)
                check('unrelated_request_is_not_an_answer', unrelated['action'] != 'task_answer', unrelated['action'])
                dropped = classify_request('Actually never mind the GitHub thing, cancel that task.', 'verify-task-questions', context)
                print('DROP', json.dumps({key: dropped[key] for key in ('action', 'task_id')}), flush=True)
                check('dropping_it_cancels_the_task', dropped['action'] == 'task_cancel' and dropped['task_id'] == identifier, dropped['action'])

                # The answer the owner actually gave in the screenshot's session resumes the task.
                count = len(classified)
                answered = c.respond('Switch to GPT 6.2 Astra.', speak=False)
                print('ANSWER ACKNOWLEDGED', json.dumps(answered.get('text')), flush=True)
                check('answer_routed_to_the_paused_task', classified[count]['action'] == 'task_answer' and classified[count]['task_id'] == identifier,
                      classified[count]['action'])
                check('task_completed_after_answer', wait_for(lambda: c.activity_status(identifier)['status'] == 'completed'),
                      c.activity_status(identifier).get('status'))
                retried = [text for text in seen if 'The owner answered: Switch to GPT 6.2 Astra.' in text]
                check('switch_retried_with_the_answer', len(retried) == 1 and 'You asked the owner: ' + QUESTION in retried[0], seen)
                check('push_ran_once_after_the_answer', [result for result in done if 'Pushed' in result] == ['Pushed the folder to GitHub, sir.'], done)
                summary = c.activity_status(identifier)['summary']
                print('SUMMARY', json.dumps(summary), flush=True)
                check('summary_has_both_results', 'Switched to GPT 6.2 Astra' in summary and 'Pushed the folder' in summary, summary)
                check('no_question_left_waiting', c.questions.open() == [], c.questions.open())

                # A question asked before anything started: the answer and the request become one request.
                direct = c.questions.raised('Check on that thing for me.', 'What would you like me to check on, sir?')
                context['waitingQuestions'], context['recentTasks'] = c.questions.context(), c.task_context()
                reply = classify_request('The weather in London.', 'verify-task-questions', context)
                print('DIRECT ANSWER', json.dumps({key: reply[key] for key in ('action', 'task_id')}), flush=True)
                check('direct_answer_routed', reply['action'] == 'task_answer' and reply['task_id'] == direct, reply['action'])
                from desktop.questions import with_answers
                combined = classify_request(with_answers('Check on that thing for me.', [{'question': 'What would you like me to check on, sir?',
                                                                                            'answer': 'The weather in London.'}]),
                                            'verify-task-questions', {**context, 'waitingQuestions': []})
                print('DIRECT COMBINED', json.dumps({key: combined[key] for key in ('action', 'capabilities', 'needs_clarification')}), flush=True)
                check('combined_request_understood', combined['action'] == 'normal' and not combined['needs_clarification']
                      and bool(set(combined['capabilities']) & {'get_weather', 'start_web_search'}), combined['capabilities'])
        finally:
            c.tasks.close()
            c.research.close()
            c.browser.close()
            for patcher in redirect:
                patcher.stop()
    print(json.dumps({'passed': not failures, 'failures': failures, 'checks': report}, indent=2, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
