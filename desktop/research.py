from __future__ import annotations

import copy
from datetime import date
import json
import queue
import re
import threading
import time
import uuid

from desktop.common import local_request
from desktop import email_policy
from desktop.web_tools import search_web, read_webpage, validate_url

TERMINAL = {'completed', 'failed', 'cancelled'}


def response_text(response):
    return ''.join(part.get('text', '') for item in response.get('output', [])
        if item.get('type') == 'message' for part in item.get('content', [])
        if part.get('type') == 'output_text').strip()


def json_object(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError('The search model returned an unreadable result. Please try the search again.') from None
    if not isinstance(value, dict):
        raise ValueError('Expected a structured response')
    return value


def validate_plan(value, scope):
    if not isinstance(value.get('queries'), list) or not isinstance(value.get('keywords'), list):
        raise ValueError('The search plan did not contain queries and keywords')
    queries = []
    for query in value['queries'][:3]:
        if not isinstance(query, str) or len(query) > 400:
            raise ValueError('Invalid search query')
        query = ' '.join(query.split())
        if (query or scope == 'email') and query.casefold() not in [row.casefold() for row in queries]:
            queries.append(query)
    if not queries:
        raise ValueError('The search plan contained no useful query')
    keywords = [term.strip() for term in value['keywords'][:8] if isinstance(term, str) and 0 < len(term.strip()) <= 100]
    return {'queries': queries, 'keywords': keywords, 'unreadOnly': value.get('unreadOnly') is True}


def validate_summary(value, evidence):
    summary = value.get('summary', '')
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 6000:
        raise ValueError('The summarizer did not return a usable answer')
    available = {source['sourceId']: source for source in evidence}
    ids = value.get('sourceIds')
    if not isinstance(ids, list) or not ids or any(type(key) is not int or key not in available for key in ids):
        raise ValueError('The summary references unknown sources')
    sources = [{key: val for key, val in available[index].items() if key not in ('text', 'snippet')}
               for index in dict.fromkeys(ids)]
    return summary.strip(), sources


class JobStopped(Exception):
    pass


class ResearchJobs:
    def __init__(self, events, mail, computer=None):
        self.events, self.mail = events, mail
        self.computer = computer
        self.lock = threading.RLock()
        self.jobs = {}
        self.pending = queue.Queue()
        self.workers = []
        self.closed = False

    def _public(self, job):
        result = {key: copy.deepcopy(value) for key, value in job.items() if not key.startswith('_') and key not in ('cancel', 'started', 'deadline')}
        result['elapsedSeconds'] = round((job.get('finished', time.time()) - job['created']), 1)
        return result

    def list(self, scope=None):
        with self.lock:
            self._expire()
            return [self._public(job) for job in reversed(list(self.jobs.values())) if scope is None or job['scope'] == scope]

    def status(self, job_id=None, scope=None):
        rows = self.list(scope)
        job = next((row for row in rows if row['id'] == job_id), None) if job_id else next(iter(rows), None)
        return {'ok': True, **job} if job else {'ok': False, 'error': 'There is no matching search yet'}

    def _expire(self):
        now = time.time()
        for key, job in list(self.jobs.items()):
            if job['status'] in TERMINAL and now - job.get('finished', now) > 1800:
                del self.jobs[key]

    def start(self, question, scope='web', provider='all', model_tier='default', project=None, powershell_command=None, foreground=None):
        if model_tier not in ('default', 'sol', 'astra'):
            return {'ok': False, 'error': 'Invalid reasoning model tier'}
        if scope == 'email' and not email_policy.enabled():
            return email_policy.unavailable()
        if scope not in ('web', 'email', 'computer', 'powershell') or provider not in ('all', 'gmail', 'outlook'):
            return {'ok': False, 'error': 'Invalid search scope'}
        if scope == 'computer' and self.computer is None:
            return {'ok': False, 'error': 'PC control is unavailable'}
        if scope == 'powershell' and (not isinstance(powershell_command, str) or not 1 <= len(powershell_command.strip()) <= 4000):
            return {'ok': False, 'error': 'A PowerShell diagnostic command is required.'}
        limit = 16000 if project and re.fullmatch(r'[a-f0-9]{32}', project) else 4000
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= limit:
            return {'ok': False, 'error': 'Ask a search question between 1 and 4000 characters'}
        with self.lock:
            self._expire()
            if self.closed:
                return {'ok': False, 'error': 'Jarvis is closing'}
            for job in self.jobs.values():
                if job['status'] not in TERMINAL and job['question'] == question.strip() and job['scope'] == scope and job['provider'] == provider and job.get('_powershellCommand') == powershell_command:
                    return {'ok': True, **self._public(job)}
            if sum(job['status'] not in TERMINAL for job in self.jobs.values()) >= 4:
                return {'ok': False, 'error': 'Four searches are already queued or running. Wait or cancel one.'}
            while len(self.jobs) >= 12:
                oldest = next((key for key, job in self.jobs.items() if job['status'] in TERMINAL), None)
                if oldest is None:
                    break
                del self.jobs[oldest]
            job_id = uuid.uuid4().hex[:12]
            job = {'id': job_id, 'question': question.strip(), 'scope': scope, 'provider': provider,
                   'status': 'queued', 'stage': 'queued', 'note': 'Waiting for a background worker', 'created': time.time(),
                   'keywords': [], 'queries': [], 'sources': [], 'summary': '', 'warnings': [], 'model': None,
                   'queriesDone': 0, 'sourcesFound': 0, 'pagesRead': 0, 'cancel': threading.Event(), '_modelTier': model_tier}
            if scope == 'computer':
                from desktop.computer import USER
                job['_foreground'] = int(USER.GetForegroundWindow() or 0) if foreground is None else int(foreground)
            if scope == 'powershell':
                job['_powershellCommand'] = powershell_command
                job['command'] = powershell_command
            self.jobs[job_id] = job
            if project:
                job['projectId'] = project
            if not self.workers:
                for index in range(2):
                    worker = threading.Thread(target=self._worker, name=f'jarvis-search-{index}', daemon=True)
                    self.workers.append(worker)
                    worker.start()
            snapshot = self._public(job)
            self.pending.put(job_id)
        self.events.put(('research', snapshot))
        return {'ok': True, **snapshot}

    def _update(self, job, **values):
        with self.lock:
            if job['cancel'].is_set() or self.closed:
                raise JobStopped()
            job.update(values)
            snapshot = self._public(job)
        self.events.put(('research', snapshot))

    def _check(self, job):
        if job['cancel'].is_set() or self.closed:
            raise JobStopped()
        if time.monotonic() > job['deadline']:
            raise TimeoutError('Search time limit reached')

    def _llm(self, job, task, payload):
        self._check(job)
        response = local_request(18791, '/v1/responses', {
            'model': 'openclaw/research', 'selectedModel': job['model'], 'task': task, 'modelTier': job.get('_modelTier', 'default'),
            'input': json.dumps(payload, ensure_ascii=False), 'user': 'research-' + job['id'],
        }, timeout=80)
        self._check(job)
        if response.get('model'):
            self._update(job, model=response['model'].removeprefix('openai/').removeprefix('anthropic/'), reasoning=response.get('reasoning'), modelFallback=response.get('modelFallback', False))
        return json_object(response_text(response))

    def frame(self, job_id):
        with self.lock:
            return self.jobs.get(job_id, {}).get('_frame')

    def _computer_step(self, job, frame, steps, readonly):
        self._check(job)
        payload = {'question': job['question'], 'app': job.get('app'), 'readOnly': readonly,
                   'imageSize': [frame['width'], frame['height']], 'previousSteps': steps[-12:],
                   'desktop': job.get('_desktop', {})}
        response = local_request(18791, '/v1/responses', {
            'model': 'openclaw/computer', 'selectedModel': job['model'], 'user': 'research-' + job['id'], 'modelTier': job.get('_modelTier', 'default'),
            'input': json.dumps(payload), 'image': frame['image'],
        }, timeout=80)
        self._check(job)
        if response.get('model'):
            self._update(job, model=response['model'].removeprefix('openai/').removeprefix('anthropic/'), reasoning=response.get('reasoning'), modelFallback=response.get('modelFallback', False))
        return json_object(response_text(response))

    def _worker(self):
        while True:
            job_id = self.pending.get()
            if job_id is None:
                self.pending.task_done()
                return
            with self.lock:
                job = self.jobs.get(job_id)
            if not job or job['cancel'].is_set():
                self.pending.task_done()
                continue
            try:
                job['deadline'] = time.monotonic() + 300
                if job['scope'] == 'powershell':
                    from desktop.powershell import run_job
                    run_job(job, self._update)
                    continue
                self._update(job, status='running', stage='planning', note='Preparing PC task' if job['scope'] == 'computer' else 'Choosing focused search terms')
                catalog = local_request(18791, '/models', timeout=5)
                self._update(job, model=catalog['conversationModel'] if job['scope'] == 'computer' else catalog['resolvedSearchModel'])
                if job['scope'] == 'computer':
                    self.computer.run(job, self._update, self._check, self._computer_step)
                    continue
                plan = validate_plan(self._llm(job, 'plan', {
                    'question': job['question'], 'scope': job['scope'], 'provider': job['provider'], 'date': str(date.today()),
                }), job['scope'])
                self._update(job, **plan)
                evidence, warnings = self._web(job) if job['scope'] == 'web' else self._email(job)
                self._check(job)
                if not evidence:
                    if warnings:
                        raise RuntimeError('No usable results. ' + ' '.join(warnings)[:450])
                    self._update(job, status='completed', stage='completed', note='No matching messages',
                                 summary='I found no matching emails in the connected mailboxes, sir.', finished=time.time())
                    continue
                self._update(job, stage='summarizing', note=f'Summarizing {len(evidence)} sources', warnings=warnings)
                summary, sources = validate_summary(self._llm(job, 'summarize', {
                    'question': job['question'], 'scope': job['scope'], 'keywords': job['keywords'],
                    'queries': job['queries'], 'warnings': warnings, 'sources': evidence,
                }), evidence)
                self._update(job, status='completed', stage='completed', note='Your search results are ready',
                             summary=summary, sources=sources, finished=time.time())
            except JobStopped:
                pass
            except Exception as exc:
                message = 'The search could not finish. Check the selected model, Codex sign-in and connection.'
                if isinstance(exc, (RuntimeError, TimeoutError, ValueError)):
                    message = str(exc)[:550]
                try:
                    self._update(job, status='failed', stage='failed', note=message, error=message, finished=time.time())
                except JobStopped:
                    pass
            finally:
                if job and job['scope'] == 'computer':
                    try:
                        local_request(18791, '/session/end', {'user': 'research-' + job['id']}, timeout=3)
                    except Exception:
                        pass
                evidence = plan = summary = sources = job = None
                self.pending.task_done()

    def _web(self, job):
        candidates, warnings = {}, []
        for index, query in enumerate(job['queries']):
            self._check(job)
            self._update(job, stage='searching', note=f"Searching query {index + 1} of {len(job['queries'])}")
            try:
                result = search_web(query)
                for row in result.get('results', []):
                    url = validate_url(row['url']).split('#')[0]
                    candidates.setdefault(url, {**row, 'url': url})
                if not result.get('ok'):
                    warnings.append(result.get('error', 'A search returned no results'))
            except Exception:
                warnings.append('One web search failed or was blocked by the provider')
            self._update(job, queriesDone=index + 1, sourcesFound=len(candidates))
        evidence = []
        for index, row in enumerate(list(candidates.values())[:5]):
            self._check(job)
            source = {'sourceId': index + 1, 'title': row['title'], 'url': row['url'],
                      'text': row.get('snippet', '')[:900], 'coverage': 'search snippet'}
            if index < 3:
                self._update(job, stage='reading', note=f'Reading source {index + 1} of {min(3, len(candidates))}')
                try:
                    page = read_webpage(row['url'])
                    if not page.get('ok'):
                        raise RuntimeError('Page unreadable')
                    source.update(text=page['text'][:6500], title=page.get('title', row['title']), coverage='page text')
                    self._update(job, pagesRead=job['pagesRead'] + 1)
                except Exception:
                    warnings.append(f'Source {index + 1} could only be read as a search snippet')
            evidence.append(source)
        if not evidence and not warnings:
            warnings.append('No readable public web results were returned')
        return evidence, list(dict.fromkeys(warnings))

    def _email(self, job):
        candidates, warnings = {}, []
        for index, query in enumerate(job['queries']):
            self._check(job)
            self._update(job, stage='searching', note=f"Searching mail query {index + 1} of {len(job['queries'])}")
            result = self.mail.search(job['provider'], query, job.get('unreadOnly', False), 5)
            warnings.extend(result.get('errors', []))
            if 'bounded' in result.get('scope', '').lower():
                warnings.append('Search coverage: ' + result['scope'])
            for row in result.get('messages', []):
                candidates.setdefault(row['id'], row)
            self._update(job, queriesDone=index + 1, sourcesFound=len(candidates))
        evidence = []
        for index, row in enumerate(list(candidates.values())[:5]):
            self._check(job)
            source = {'sourceId': index + 1, 'id': row['id'], 'provider': row.get('provider'),
                      'title': row.get('subject', ''), 'from': row.get('from'), 'date': row.get('date'),
                      'text': row.get('snippet', '')[:1200], 'coverage': 'message preview'}
            if index < 3:
                self._update(job, stage='reading', note=f'Reading message {index + 1} of {min(3, len(candidates))}')
                message = self.mail.read(row['id'])
                if message.get('ok'):
                    source.update(text=message.get('body', '')[:6500], coverage='message body')
                    self._update(job, pagesRead=job['pagesRead'] + 1)
                else:
                    warnings.append('One matching email could only be read as a preview')
            evidence.append(source)
        return evidence, list(dict.fromkeys(warnings))

    def cancel(self, job_id=None, scope=None):
        with self.lock:
            rows = list(reversed(self.jobs.values()))
            job = next((row for row in rows if (not job_id or row['id'] == job_id)
                        and (scope is None or row['scope'] == scope) and row['status'] not in TERMINAL), None)
            if not job:
                return {'ok': False, 'error': 'No matching search is running'}
            job['cancel'].set()
            job.update(status='cancelled', stage='cancelled', note='Search cancelled', finished=time.time())
            result = self._public(job)
        self.events.put(('research', result))
        def stop_model():
            try:
                local_request(18791, '/session/end', {'user': 'research-' + job['id']}, timeout=2)
            except Exception:
                pass
        threading.Thread(target=stop_model, daemon=True).start()
        return {'ok': True, **result}

    def close(self):
        with self.lock:
            ids = [job['id'] for job in self.jobs.values() if job['status'] not in TERMINAL]
            self.closed = True
        for job_id in ids:
            self.cancel(job_id)
        for _ in self.workers:
            self.pending.put(None)


def progress_reply(job):
    label = 'PowerShell command' if job.get('scope') == 'powershell' else 'PC task' if job.get('scope') == 'computer' else 'search' if job.get('scope') in ('web','email') else 'task'
    if not job.get('ok'):
        return 'There is no matching task yet, sir.', []
    if job['status'] == 'completed':
        return job['summary'], job.get('sources', [])
    if job['status'] == 'waiting':
        # The task is paused on a question, so the update is that question again.
        return job.get('pendingQuestion') or job.get('summary') or job['note'], []
    if job['status'] == 'failed':
        detail = job.get('summary') or job.get('error') or job['note']
        verified = [row for row in job.get('windowResults', []) if row.get('verified')]
        outcomes = []
        for action, verb in (('close_window', 'Closed'), ('minimize_window', 'Minimized')):
            count = sum(row.get('action') == action and (action != 'close_window' or row.get('state') == 'closed') for row in verified)
            if count:
                outcomes.append(f'{verb} {count} window' + ('s' if count != 1 else '') + '.')
        hidden = sum(row.get('action') == 'close_window' and row.get('state') == 'hidden' for row in verified)
        if hidden:
            outcomes.append(f'{hidden} window(s) hidden; the app may still be running.')
        if outcomes and not detail.startswith(tuple(outcomes)):
            detail = ' '.join(outcomes) + ' ' + detail
        elif not job.get('windowResults') and job.get('steps'):
            detail = job['steps'][-1] + '. ' + detail
        return f'The {label} could not finish, sir. ' + detail, []
    if job['status'] == 'cancelled':
        return f'That {label} was cancelled, sir.', []
    count = job.get('sourcesFound', 0)
    detail = f" I have found {count} sources so far." if count else ''
    return f"{job['note']}, sir.{detail} You can keep talking to me while I work.", []
