import copy
import queue
import threading
import time
import uuid

from desktop.questions import with_answers

TERMINAL = {'completed', 'failed', 'cancelled'}
# A task holds here when one of its actions needs the owner's answer: it keeps that action and every one after it,
# and carries on from there once he answers.
WAITING = 'waiting'


class TaskEvents:
    def __init__(self, tasks, job):
        self.tasks, self.job = tasks, job

    def put(self, event):
        kind = event[0]
        if kind == 'model_used':
            self.tasks.update(self.job, model=event[1], reasoning=event[2], stage='working', note='Working on your request')
        elif kind == 'tool':
            self.tasks.update(self.job, stage='working', note='Running ' + event[1].replace('_', ' '))
        elif kind == 'task_stage':
            self.tasks.update(self.job, stage=event[1], note=event[2])
        elif kind not in ('state', 'question', 'answer', 'finished', 'notice'):
            self.tasks.owner.events.put(event)


class TaskResearch:
    def __init__(self, tasks, job):
        self.tasks, self.job = tasks, job
        self.base = tasks.owner.research

    def __getattr__(self, name):
        return getattr(self.base, name)

    def start(self, *args, **kwargs):
        if self.job['_cancel'].is_set():
            return {'ok':False, 'error':'Task cancelled'}
        if self.job.get('childId'):
            return {'ok':False, 'error':'This step already has a background job. Check its progress.'}
        scope = kwargs.get('scope', args[1] if len(args) > 1 else 'web')
        if scope == 'computer':
            kwargs['foreground'] = self.job['_foreground']
        result = self.base.start(*args, **kwargs)
        if result.get('ok'):
            values = {'childId': result['id'], 'childIds': [*self.job.get('childIds', []), result['id']]}
            if (self.job.get('_decision') or {}).get('action') != 'compound':
                values['scope'] = result['scope']
            self.tasks.update(self.job, **values)
            if self.job['_cancel'].is_set():
                self.base.cancel(result['id'])
        return result


class BackgroundTasks:
    def __init__(self, owner):
        self.owner = owner
        self.lock = threading.RLock()
        self.jobs = {}
        self.controllers = {}
        self.pending = queue.Queue()
        self.workers = []
        self.closed = False

    def public(self, job):
        result = {key:copy.deepcopy(value) for key,value in job.items() if not key.startswith('_')}
        result['elapsedSeconds'] = round(job.get('finished', time.time())-job['created'], 1)
        return result

    def list(self):
        with self.lock:
            for key,job in list(self.jobs.items()):
                if job['status'] in TERMINAL and time.time()-job['finished'] > 1800:
                    del self.jobs[key]
            return [self.public(job) for job in reversed(list(self.jobs.values()))]

    def prepare(self, text, domain, foreground=0, decision=None):
        with self.lock:
            self.list()
            active = [job for job in self.jobs.values() if job['status'] not in TERMINAL]
            if self.closed or len(active) >= 4:
                if any(job['status'] == WAITING for job in active):
                    raise RuntimeError('Four tasks are already running or waiting for your answer. Answer or cancel one, sir.')
                raise RuntimeError('Four tasks are already queued or running. Wait or cancel one, sir.')
            while len(self.jobs) >= 24:
                oldest = next(key for key,job in self.jobs.items() if job['status'] in TERMINAL)
                del self.jobs[oldest]
            identifier = 'task-' + uuid.uuid4().hex[:12]
            job = {'id':identifier, 'scope':domain, 'question':text, 'created':time.time(),
                   'status':'queued', 'stage':'acknowledging', 'note':'Acknowledging your request', 'summary':'',
                   'sources':[], 'keywords':[], 'queries':[], 'sourcesFound':0, 'pagesRead':0, 'warnings':[], 'model':None,
                   '_cancel':threading.Event(), '_foreground':foreground,
                   '_previousDomain':self.owner.last_domain, '_previousAnswer':self.owner.last_answer_text,
                   '_decision':copy.deepcopy(decision)}
            self.jobs[identifier] = job
            return self.public(job)

    def release(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job['status'] in TERMINAL:
                return
            self.update(job, stage='queued', note='Waiting for a task worker')
            if not self.workers:
                for index in range(2):
                    worker = threading.Thread(target=self.run, name=f'jarvis-task-{index}', daemon=True)
                    self.workers.append(worker)
                    worker.start()
            self.pending.put(identifier)

    def describe(self, identifier, acknowledgement):
        with self.lock:
            job = self.jobs.get(identifier)
            if job and job['status'] not in TERMINAL:
                job['acknowledgement'] = acknowledgement

    def update(self, job, **values):
        with self.lock:
            if job['status'] in TERMINAL:
                return
            job.update(values)
            public = self.public(job)
        self.owner.events.put(('research', public))

    def tool(self, identifier, name, arguments):
        with self.lock:
            worker = next((worker for worker in self.controllers.values() if worker.request and worker.request['id'] == identifier), None)
        if worker:
            return worker.tool(identifier, name, arguments)
        return {'ok':False, 'error':'This task request has ended'}

    def run_step(self, job, text, decision):
        worker = self.owner.make_task_agent(TaskEvents(self, job), job)
        worker.task_decision = decision
        # Every step has its own controller, request ID and tool references. Prior
        # replies from another step must not become instructions to this one.
        if (job.get('_decision') or {}).get('action') == 'compound':
            worker.last_domain = worker.last_answer_domain = None
            worker.last_answer_text = ''
        with self.lock:
            self.controllers[job['id']] = worker
        browser_locked = False
        try:
            if job['_cancel'].is_set():
                return {'ok': False, 'error': 'Task cancelled'}
            self.update(job, childId=None)
            if any(tool.startswith('browser_') for tool in (decision or {}).get('capabilities', [])):
                self.update(job, stage='waiting', note='Waiting for the dedicated browser')
                while not job['_cancel'].is_set():
                    if self.owner.browser.task_lock.acquire(timeout=.1):
                        browser_locked = True
                        break
                if not browser_locked:
                    return {'ok': False, 'error': 'Task cancelled'}
            result = worker.respond(text, speak=False)
            if result.get('needsClarification'):
                result = {**result, 'ok': False}
            child_id = job.get('childId')
            if child_id:
                while not job['_cancel'].is_set():
                    child = self.owner.research.status(child_id)
                    if not child.get('ok'):
                        raise RuntimeError('The background task result is unavailable.')
                    keys = ('stage','note','model','reasoning','keywords','queries','sourcesFound','pagesRead','sources','steps','warnings','windowResults','command','stdout','stderr','exitCode','outputTruncated')
                    values = {key:child[key] for key in keys if key in child}
                    if (job.get('_decision') or {}).get('action') != 'compound':
                        values['scope'] = child.get('scope', job['scope'])
                    self.update(job, **values)
                    if child['status'] in TERMINAL:
                        if child['status'] == 'cancelled':
                            self.cancel(job['id'])
                        child_ok = child['status'] == 'completed'
                        failure = result.get('text') or result.get('error') or ''
                        result = {**result, 'ok': result.get('ok', False) and child_ok,
                                  'outcome': 'completed' if result.get('ok') and child_ok else 'failed',
                                  'text': (failure + ' ' if not result.get('ok') else '') + (child.get('summary') or child.get('note', '')),
                                  'sources': child.get('sources', []), 'model': child.get('model'), 'reasoning': child.get('reasoning')}
                        break
                    job['_cancel'].wait(.2)
            return result
        except Exception as exc:
            message = str(exc)[:300] if isinstance(exc, (ValueError, RuntimeError, OSError)) else 'This action could not finish.'
            return {'ok': False, 'error': message}
        finally:
            if browser_locked:
                self.owner.browser.task_lock.release()

    @staticmethod
    def plan_steps(job):
        """The task's actions: each step of a compound request, or the whole request as its one action."""
        decision = job.get('_decision') or {}
        if decision.get('action') == 'compound':
            return decision['steps'], True
        return [{'request': job['question'], 'depends_on': []}], False

    def run_plan(self, job):
        decision = job.get('_decision') or {}
        steps, compound = self.plan_steps(job)
        # What each action has done survives a pause for the owner's answer, so the task resumes where it stopped.
        records = job.setdefault('_steps', [{'state': 'pending', 'exchanges': []} for _ in steps])
        for index, step in enumerate(steps):
            if job['_cancel'].is_set():
                return {'ok': False, 'error': 'Task cancelled'}
            record = records[index]
            if record['state'] in ('done', 'failed', WAITING):
                continue
            if compound:
                self.update(job, stage='working', note=f'Action {index + 1} of {len(steps)}')
            needed = [records[prior]['state'] for prior in step['depends_on']]
            if 'failed' in needed:
                record.update(state='failed', result={'ok': False, 'error': 'Skipped: ' + step['request'] + ' A required earlier action did not complete.'})
            elif any(state != 'done' for state in needed):
                # An earlier action it needs is waiting for the owner's answer, so this one waits as well.
                record['state'] = 'held'
                continue
            else:
                result = self.run_step(job, with_answers(step['request'], record['exchanges']), step if compound else (decision or None))
                question = (result.get('text') or result.get('error') or '').strip() if result.get('needsClarification') else ''
                if question and not job['_cancel'].is_set():
                    # A question is not a failure: the action keeps its place until he answers it.
                    record.update(state=WAITING, question=question, result={**result, 'ok': False})
                else:
                    record.update(state='done' if result.get('ok') else 'failed', result=result)
            if compound:
                self.update(job, actionResults=[{**row['result'], 'request': steps[number]['request']}
                                                for number, row in enumerate(records) if row['state'] in ('done', 'failed')])
        waiting = next((row for row in records if row['state'] == WAITING), None)
        if waiting:
            return {'ok': False, 'needsClarification': True, 'waiting': True, 'text': waiting['question'],
                    'question': waiting['question'], 'model': waiting['result'].get('model') or job.get('model'),
                    'reasoning': waiting['result'].get('reasoning') or job.get('reasoning')}
        if not compound:
            return records[0]['result']
        results = [{**row['result'], 'request': step['request']} for row, step in zip(records, steps)]
        return {'ok': all(row['ok'] for row in results),
                'needsClarification': any(row.get('needsClarification') for row in results),
                'text': ' '.join(row.get('text') or row.get('error') or 'An action did not return a result.' for row in results),
                'sources': [source for row in results for source in row.get('sources', [])],
                'toolFailures': [failure for row in results for failure in row.get('toolFailures', [])],
                'model': job.get('model'), 'reasoning': job.get('reasoning')}

    def run(self):
        while True:
            identifier = self.pending.get()
            if identifier is None:
                self.pending.task_done()
                return
            job = None
            try:
                with self.lock:
                    job = self.jobs.get(identifier)
                    if not job or job['_cancel'].is_set():
                        continue
                self.update(job, status='running', stage='classifying',
                            note='Continuing with your answer' if job.get('_steps') else 'Choosing Luna, Sol or Astra for this task')
                result = self.run_plan(job)
                if job['_cancel'].is_set():
                    continue
                if result.get('waiting'):
                    self.hold(job, result)
                    continue
                ok = result.get('ok', False)
                if result.get('taskReference'):
                    self.update(job, scope='task_status', taskReference=result['taskReference'])
                self.update(job, status='completed' if ok else 'failed', stage='completed' if ok else 'failed',
                    note='Needs your answer' if result.get('needsClarification') else 'Task complete' if ok else 'Task failed',
                    summary=result.get('text') or result.get('error') or 'The task did not return a result.',
                    sources=result.get('sources', []), toolFailures=result.get('toolFailures', []), model=result.get('model') or job.get('model'),
                    reasoning=result.get('reasoning') or job.get('reasoning'), finished=time.time())
                completed = self.public(job)
                self.owner.completed_tasks.put(completed)
                self.owner.events.put(('task_completed', completed))
            except Exception as exc:
                message = str(exc)[:300] if isinstance(exc, (ValueError, RuntimeError)) else 'The background task could not finish. Check Activity, sir.'
                if job and not job['_cancel'].is_set():
                    self.update(job, status='failed', stage='failed', note=message, summary=message, finished=time.time())
                    completed = self.public(job)
                    self.owner.completed_tasks.put(completed)
                    self.owner.events.put(('task_completed', completed))
            finally:
                with self.lock:
                    self.controllers.pop(identifier, None)
                self.pending.task_done()

    def hold(self, job, result):
        """Pause the task on its question. Jarvis asks it as soon as he is free, and the answer resumes the task."""
        question = result['question']
        self.update(job, status=WAITING, stage='needs_answer', note='Needs your answer', pendingQuestion=question,
                    questionRaised=time.time(), summary=question, model=result.get('model') or job.get('model'),
                    reasoning=result.get('reasoning') or job.get('reasoning'))
        row = self.public(job)
        self.owner.completed_tasks.put(row)
        self.owner.events.put(('task_completed', row))

    def questions(self):
        """The question each paused task is waiting on, the action that asked it and the actions waiting with it."""
        rows = []
        with self.lock:
            for job in self.jobs.values():
                if job['status'] != WAITING or not job.get('pendingQuestion'):
                    continue
                steps, _ = self.plan_steps(job)
                records = job.get('_steps') or []
                asking = next((index for index, row in enumerate(records) if row['state'] == WAITING), None)
                rows.append({'id': job['id'], 'taskId': job['id'], 'question': job['pendingQuestion'],
                             'request': steps[asking]['request'] if asking is not None else job['question'],
                             'raised': job.get('questionRaised') or job['created'],
                             'held': [steps[index]['request'] for index, row in enumerate(records)
                                      if row['state'] in ('held', WAITING) and index != asking]})
        return rows

    def waiting(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            return bool(job) and job['status'] == WAITING

    def answer(self, identifier, text, release=True):
        """The owner's answer to the question a task is waiting on. The action that asked runs again with the
        question and answer added to its request, then the actions that waited for it."""
        with self.lock:
            job = self.jobs.get(identifier)
            record = next((row for row in (job or {}).get('_steps', []) if row['state'] == WAITING), None)
            if not job or job['status'] != WAITING or not record:
                return {'ok': False, 'error': 'That task is no longer waiting for an answer'}
            record['exchanges'] = [*record['exchanges'], {'question': record['question'], 'answer': text[:1000]}]
            record['state'] = 'answered'
            self.update(job, status='queued', stage='queued', note='Continuing with your answer', pendingQuestion=None, summary='')
            public = self.public(job)
        if release:
            self.release(identifier)
        return {'ok': True, **public}

    def expire(self, identifier):
        """Stop waiting on a question that went unanswered after Jarvis asked it several times."""
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job['status'] != WAITING:
                return False
            self.update(job, status='failed', stage='failed', note='No answer', pendingQuestion=None, finished=time.time())
            return True

    def cancel(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job['status'] in TERMINAL:
                return {'ok':False, 'error':'No matching task is running'}
            job['_cancel'].set()
            worker = self.controllers.get(identifier)
            self.update(job, status='cancelled', stage='cancelled', note='Task cancelled', summary='', finished=time.time())
            if job.get('childId'):
                self.owner.research.cancel(job['childId'])
            if worker:
                worker.cancel()
            return {'ok':True, **self.public(job)}

    def close(self):
        with self.lock:
            self.closed = True
            for identifier in list(self.jobs):
                self.cancel(identifier)
            for _ in self.workers:
                self.pending.put(None)
