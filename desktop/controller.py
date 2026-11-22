from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import queue
import re
import threading
import time
import uuid
import wave

from desktop.common import ROOT, auth_token, local_request
from desktop import email_policy
from desktop.mail import MailTools, status as mail_status
from desktop.research import ResearchJobs, progress_reply, response_text
from desktop.reminders import Reminders, WaitingReminders
from desktop.breaks import BreakState
from desktop.intent import classify_request
from desktop.screen import capture_screen
from desktop.processes import inspect_processes
from desktop.interruptions import NAME
from desktop.preferences import Preferences
from desktop.personal import PersonalTools
from desktop.briefing import Briefing
from desktop.files import FileTools
from desktop.browser import BrowserTools
from desktop.windows import AppCatalog, startup_enabled, desktop_name
from desktop.computer import ComputerControl
from desktop.model_commands import switch_model
from desktop.speech import speech_status
from desktop.projects import Projects, PROJECT_TOOLS
from desktop.window_layouts import WindowLayouts
from desktop.tasks import BackgroundTasks, TaskResearch
from desktop.questions import Questions, with_answers
from desktop.acknowledgements import acknowledge, FALLBACK_ACKNOWLEDGEMENT, WAKE_REPLY
from desktop.diagnostics import event as diagnostic
from desktop.network import inspect_network
from desktop.routing import capabilities_for, require_capability
from desktop import process_control
from desktop import self_settings
from desktop.focus_guard import NAMES as LOCK_IN_NAMES, PASS_AWAY_SECONDS, PASS_SITES, PASS_WAIT_SECONDS, FocusGuard
from desktop.welcome import WelcomeBack
from desktop.visuals import Visuals
from desktop.timeline import ago, local_time

EMAIL_TOOLS = ['email_accounts', 'search_email', 'read_email', 'start_email_search', 'email_search_status']
WEB_TOOLS = ['start_web_search', 'web_search_status']
CLARIFYING_TOOLS = tuple(WEB_TOOLS)
# A task started this recently is the one the owner means when he drops what Jarvis is doing.
RECENT_TASK_SECONDS = 300
# How long Jarvis waits for the owner to confirm that he should go away, and what he asks if no wording comes.
BREAK_CONFIRM_SECONDS = 60
BREAK_QUESTION = 'Sir, just to confirm, you want me to shut off for now?'
CLARIFYING_NOTE = ('\nThis request may be missing details. If a public web search can supply what is missing, such as an unfamiliar '
                   'name, brand, product, place or recent event, start it with start_web_search instead of asking. Otherwise ask one '
                   'concise clarifying question before doing anything. Do not guess which app, file or setting to operate. '
                   'You can always search the public web; never say you have no web search.')
REMINDER_TOOLS = ['create_reminder', 'list_reminders', 'change_reminder']
FILE_TOOLS = ['search_files', 'search_document_content', 'read_file']
COMPUTER_TOOLS = ['start_computer_task', 'computer_status']
BROWSER_TOOLS = ['browser_navigate', 'browser_read', 'browser_click', 'browser_fill', 'browser_scroll']
POWERSHELL_TOOLS = ['start_powershell', 'powershell_status']
LAUNCH = re.compile(r'^(?:(?:can|could|would) you\s+)?(?:please\s+)?(?:open|start|launch|run)\s+(?:the\s+)?(.+?)(?:\s+for me|\s+please)?[.!?]*$', re.I)
# Speech is synthesized a piece at a time while the previous piece plays. The first piece is short so Jarvis starts
# talking almost at once; each later piece may be at most twice as long as the one before it, so it is ready before
# that one finishes playing, even when the computer is busy.
FIRST_SPEECH_CHUNK, MIN_FIRST_SPEECH_CHUNK, SPEECH_CHUNK = 120, 40, 220
# A clause ends at sentence punctuation, or at a comma, semicolon, colon or dash, together with any closing quote,
# when the next word does not carry on the same sentence: "Nov. 3" and "3.5" are not ends.
CLAUSE = re.compile(r'.+?(?:[.!?;:,—–]+["”’)\]]*(?=\s+(?![a-z0-9])|$)\s*|$)', re.S)
SENTENCE_END = re.compile(r'[.!?]+["”’)\]]*\s*$')
ABBREVIATION = re.compile(r'\b(?:mr|mrs|ms|dr|st|jr|sr|vs|etc|e\.g|i\.e|no|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.\s*$', re.I)


def words_within(text, size):
    """Split one long clause at spaces into pieces no longer than size, keeping every character."""
    pieces = []
    while len(text) > size:
        cut = text.rfind(' ', 0, size + 1)
        cut = cut + 1 if cut > 0 else size
        pieces.append(text[:cut])
        text = text[cut:]
    return pieces + [text] if text else pieces


def speech_chunks(text, first=FIRST_SPEECH_CHUNK, least=MIN_FIRST_SPEECH_CHUNK, limit=SPEECH_CHUNK):
    """The pieces Jarvis synthesizes and plays in turn. They always join back to exactly the original text."""
    clauses = []
    for clause in CLAUSE.findall(text) if text else []:
        # A title such as Mr. or a month such as Nov. belongs with the words after it.
        if clauses and ABBREVIATION.search(clauses[-1]):
            clauses[-1] += clause
        else:
            clauses.append(clause)
    if ''.join(clauses) != text:
        clauses = [text] if text else []
    units = [piece for clause in clauses for piece in words_within(clause, limit)]
    chunks, current = [], ''
    for unit in units:
        if not chunks:
            room = first
            # Start talking as soon as the opening reaches a full sentence of a reasonable length.
            if current and len(current) >= least and SENTENCE_END.search(current):
                chunks.append(current)
                current = ''
                room = min(limit, max(first, 2 * len(chunks[-1])))
        else:
            room = min(limit, max(first, 2 * len(chunks[-1])))
        if current and len(current) + len(unit) > room:
            chunks.append(current)
            current = ''
            room = min(limit, max(first, 2 * len(chunks[-1])))
        while not current and len(unit) > room:
            # A clause too long for this piece is broken at a space, the opening one so speech starts quickly.
            head = words_within(unit, room)
            chunks.append(head[0])
            unit = ''.join(head[1:])
            room = min(limit, max(first, 2 * len(chunks[-1])))
        current += unit
    if current:
        chunks.append(current)
    return chunks or [text]


class Controller:
    def __init__(self, events):
        self.events = events
        self.apps = AppCatalog()
        self.mail = MailTools()
        self.computer = ComputerControl(self.apps)
        # Jarvis's own pointer, drawn by the interface wherever a PC task acts.
        self.computer.pointer = lambda **row: self.events.put(('agent_pointer', row))
        self.research = ResearchJobs(events, self.mail, self.computer)
        self.reminders = Reminders()
        # Due reminders wait here until the owner is at the computer; desktop.welcome hands them over.
        self.waiting_reminders = WaitingReminders()
        self.preferences = Preferences()
        self.breaks = BreakState()
        self.files = FileTools(self.preferences)
        self.personal = PersonalTools(self.preferences, self.files, events)
        self.projects = Projects()
        self.layouts = WindowLayouts()
        self.browser = BrowserTools(events)
        self.briefing = Briefing(self.preferences, self.reminders, self.mail)
        self.briefing_running = False
        self.next_briefing_check = 0
        self.closed = threading.Event()
        self.pending_announcements = []
        self.busy = threading.Lock()
        self.state_lock = threading.RLock()
        self.request = None
        self.last_domain = None
        self.last_command_time = 0
        self.last_task_id = None
        self.session = 'desktop-' + uuid.uuid4().hex
        self.sessions = {domain: self.session + '-' + domain for domain in ('email', 'web', 'apps', 'reminders', 'briefing', 'weather', 'files', 'browser')}
        self.state = 'starting'
        self.sessions['computer'] = self.session + '-computer'
        self.sessions['screen'] = self.session + '-screen'
        self.sessions['memory'] = self.session + '-memory'
        self.sessions['system'] = self.session + '-system'
        self.sessions['monitor'] = self.session + '-monitor'
        self.sessions['processes'] = self.session + '-processes'
        self.sessions['powershell'] = self.session + '-powershell'
        self.sessions['network'] = self.session + '-network'
        self.sessions['projects'] = self.session + '-projects'
        self.sessions['layout'] = self.session + '-layout'
        self.sessions['settings'] = self.session + '-settings'
        self.sessions['tasks'] = self.session + '-tasks'
        # A drawing usually follows what was just being discussed, so it shares the general conversation.
        self.sessions['visuals'] = self.sessions['web']
        self.note = 'Starting Jarvis'
        self.voice_ready = False
        self.microphone_ready = False
        self.microphone_name = None
        self.muted = False
        self.cancelled = threading.Event()
        self.speech_paused = threading.Event()
        self.interruption_pause = None
        self.speech_pause_lock = threading.Lock()
        self.playback = None
        self.spoken_chunk = ''
        self.spoken_text = ''
        self.last_answer_text = ''
        self.last_owner_text = ''
        self.last_owner_at = None
        # A shut-off Jarvis asked the owner to confirm, and when he asked.
        self.pending_break = None
        self.last_answer_domain = None
        self.last_model = None
        self.last_reasoning = None
        self.interrupt_lock = threading.Lock()
        self.server = None
        self.windows_desktop = desktop_name()
        self.tasks = BackgroundTasks(self)
        self.completed_tasks = queue.Queue()
        # Questions he has asked and is still waiting on; he asks again when the owner does not answer.
        self.questions = Questions(self)
        self.quiet_since = 0
        self.speech_cache = {}
        self.focus_guard = FocusGuard(self)
        self.visuals = Visuals(self.preferences)
        self.welcome = WelcomeBack(self)

    def update(self, state, note=''):
        with self.state_lock:
            if self.breaks.active:
                state, note = 'away', 'Jarvis is taking a break'
            if state != self.state:
                diagnostic('state_changed', previous_state=self.state, state=state)
            self.state, self.note = state, note
            self.events.put(('state', state, note))

    def warmup(self):
        from phone.voice import speech_voice, synthesize_wav
        speech_voice()
        self.speech_cache[FALLBACK_ACKNOWLEDGEMENT] = synthesize_wav(FALLBACK_ACKNOWLEDGEMENT)
        self.speech_cache[WAKE_REPLY] = synthesize_wav(WAKE_REPLY)
        self.voice_ready = True
        self.apps.refresh()
        if self.microphone_ready or self.muted or getattr(getattr(self, 'listener', None), 'hidden', False):
            self.idle()
        else:
            self.update('starting', 'Starting microphone')

    def background_loop(self):
        while not self.closed.wait(1):
            try:
                if self.breaks.finish_due():
                    self.events.put(('break_changed',))
                    self.idle()
                if self.breaks.active:
                    continue
                self.deliver_task_completion()
                self.questions.remind()
                if getattr(self, 'personal', None):
                    try:
                        self.personal.monitor.poll()
                    except Exception:
                        self.personal.monitor.last_error = 'Monitoring is temporarily unavailable.'
                for row in self.reminders.poll():
                    self.waiting_reminders.add(row)
                if self.pending_announcements and not self.muted and self.voice_ready and self.busy.acquire(blocking=False):
                    try:
                        row = self.pending_announcements.pop(0)
                        active = self.reminders.list()['reminders']
                        if any(r['id'] == row['id'] and r['state'] == 'due' for r in active):
                            self.cancelled = threading.Event()
                            self.speak('Reminder, sir. ' + row['text'])
                    finally:
                        self.release_response()
                if not self.muted and not self.busy.locked() and not self.briefing_running and time.monotonic() >= self.next_briefing_check:
                    self.next_briefing_check = time.monotonic() + 60
                    if self.briefing.scheduled_today():
                        self.briefing_running = True
                        threading.Thread(target=self.scheduled_briefing, daemon=True).start()
            except Exception:
                self.events.put(('notice', 'A reminder could not be delivered. Open Reminders and timers to check it.'))
                self.closed.wait(15)

    def scheduled_briefing(self):
        try:
            if not self.closed.is_set() and not self.breaks.active:
                result = self.respond('Give me my morning briefing: weather, reminders, and important unread emails.')
                if result.get('ok'):
                    self.briefing.mark_delivered()
                else:
                    self.next_briefing_check = time.monotonic() + 900
        finally:
            self.briefing_running = False

    def deliver_reminders(self, rows, spoken=False):
        """Pop up due reminders beside the orb now that the owner is here, and say each one unless it was already said,
        as part of a greeting. One dismissed or cancelled while it waited is left out."""
        if not rows:
            return []
        try:
            due = {row['id'] for row in self.reminders.list()['reminders'] if row['state'] == 'due'}
            rows = [row for row in rows if row['id'] in due]
        except Exception as exc:
            diagnostic('reminders_unchecked', error_type=type(exc).__name__)
        for row in rows:
            self.events.put(('reminder_due', row))
            if not spoken:
                self.pending_announcements.append(row)
        if rows:
            diagnostic('reminders_delivered', count=len(rows), spoken=spoken)
        return rows

    def health(self):
        from phone.voice import SETTINGS as phone_settings
        listener = getattr(self, 'listener', None)
        return {'service': 'jarvis-desktop', 'version': 44, 'taskQuestionsWait': True, 'compoundActions': True, 'processGroupTargets': True, 'continuationWordConfirmation': True, 'backgroundControlClicks': True, 'interruptionRecovery': True, 'processTermination': True, 'orbMonitorControl': True, 'speechPrefetch': True, 'semanticToolRouting': True, 'structuredTaskOutcomes': True, 'computerToolConversation': True, 'generalDesktopControl': True, 'multiMonitorControl': True, 'state': self.state,
                'uiVisible': getattr(self, 'ui_visible', None), 'insertBehavior': 'visibility_and_listening',
                'listeningHidden': getattr(listener, 'hidden', False) is True,
                'diagnosticsLog': 'logs/jarvis-events.jsonl',
                'conversationActive': bool(listener and listener.conversation_active),
                'wakeWordRequiredForEveryCommand': True,
                'voiceInterruptionMode': 'wake_then_listen',
                'listeningWindowOpen': bool(listener and listener.is_listening()),
                'emailEnabled': email_policy.enabled(),
                'break': self.breaks.snapshot(),
                'lastModel': self.last_model, 'lastReasoning': self.last_reasoning,
                'hotkey': self.hotkey.status() if getattr(self, 'hotkey', None) else {'key': 'Insert', 'installed': False, 'availableOnUserDesktop': False},
                'speechToText': {**speech_status(getattr(self, 'whisper_model', 'whisper-large-v3')), 'lastError': getattr(self, 'speech_error', None)},
                'pcControlAvailable': self.windows_desktop.lower() == 'default',
                'voiceReady': self.voice_ready, 'voiceModel': 'jarvis-high.onnx', 'voiceSpeed': phone_settings['voice'].get('speed', 1.0),
                'microphoneReady': self.microphone_ready, 'microphone': self.microphone_name,
                'muted': self.muted, 'startupEnabled': startup_enabled(), 'installedApps': len(self.apps.apps),
                'orbWindowCreated': True, 'interface': dict(getattr(self, 'interface', None) or {}),
                'windowsDesktop': self.windows_desktop,
                'visibleOnUserDesktop': self.windows_desktop.lower() == 'default', 'note': self.note,
                'backgroundSearches': sum(row['status'] in ('queued', 'running') for row in self.research.list()),
                'reminders': True, 'voiceInterruptions': True, 'briefing': True, 'fileSearch': True, 'browserActions': True,
                'obsidianMemory': True, 'documentEditing': True, 'contentSearch': True, 'proactiveMonitoring': True,
                'windowsControls': True, 'offlineCommands': False,
                'savedProjects': True, 'windowLayouts': True, 'independentTasks': True,
                'backgroundTasks': sum(row['status'] in ('queued','running') for row in self.activity_list()),
                'powershellDiagnostics': True, 'networkAddresses': True, 'lockInMode': True, 'learningReminders': True, 'welcomeBack': True}

    def idle(self):
        with self.state_lock:
            if self.breaks.active:
                self.update('away', 'Jarvis is taking a break')
            elif self.muted:
                self.update('muted', 'Microphone muted')
            elif getattr(getattr(self, 'listener', None), 'hidden', False) is True:
                self.update('hidden', 'Hidden and not listening. Press Insert to talk.')
            elif self.busy.locked():
                return
            elif not self.microphone_ready:
                note = 'Microphone unavailable. Click the orb to type.'
                if self.windows_desktop.lower() != 'default':
                    note = 'Run setup-jarvis.cmd from your Windows desktop to enable the microphone and orb.'
                self.update('error', note)
            else:
                listener = getattr(self, 'listener', None)
                if listener and listener.is_listening():
                    self.update('listening', 'Listening')
                else:
                    self.update('ready', 'Say Jarvis')

    def release_response(self):
        with self.state_lock:
            self.busy.release()
            self.quiet_since = time.monotonic()
            self.idle()

    def speak(self, text):
        if self.breaks.active:
            return
        from phone.voice import synthesize_wav
        import sounddevice as sd
        self.update('speaking', 'Speaking')
        # Lines synthesized ahead of time, such as the wake reply, play from the cache in one piece.
        chunks = [text] if text in self.speech_cache else speech_chunks(text)
        cancelled = self.cancelled
        self.spoken_text = ''
        request_id = (self.request or {}).get('id')
        started = time.monotonic()
        completed = False
        synthesis = ThreadPoolExecutor(max_workers=1, thread_name_prefix='jarvis-speech')

        def prepare(chunk):
            if cancelled.is_set():
                return None
            synthesis_started = time.monotonic()
            diagnostic('synthesis_started', request_id=request_id, reply_chars=len(chunk))
            data = self.speech_cache.get(chunk) or synthesize_wav(chunk)
            diagnostic('synthesis_finished', request_id=request_id, duration_ms=round((time.monotonic()-synthesis_started)*1000))
            return data

        diagnostic('speech_started', request_id=request_id, reply_chars=len(text))
        try:
            pending = synthesis.submit(prepare, chunks[0])
            with ExitStack() as streams:
                output = audio_format = None
                for index, chunk in enumerate(chunks):
                    waiting = time.monotonic()
                    while not pending.done() and not cancelled.wait(.02):
                        pass
                    if cancelled.is_set():
                        break
                    data = pending.result()
                    diagnostic('playback_buffer_ready', request_id=request_id, source='initial' if index == 0 else 'continuation',
                               duration_ms=round((time.monotonic()-waiting)*1000))
                    if index + 1 < len(chunks):
                        pending = synthesis.submit(prepare, chunks[index + 1])
                    self.spoken_chunk = chunk
                    self.spoken_text += chunk
                    with wave.open(io.BytesIO(data), 'rb') as audio:
                        playback_started = time.monotonic()
                        diagnostic('playback_started', request_id=request_id, sample_rate=audio.getframerate(), audio_ms=round(audio.getnframes()*1000/audio.getframerate()))
                        current_format = (audio.getframerate(), audio.getnchannels())
                        if current_format != audio_format:
                            streams.close()
                            output = streams.enter_context(sd.RawOutputStream(samplerate=audio.getframerate(), channels=audio.getnchannels(),
                                                            dtype='int16', blocksize=0, latency='low'))
                            self.playback = output
                            audio_format = current_format
                        while not cancelled.is_set():
                            if self.speech_paused.is_set():
                                cancelled.wait(.02)
                                continue
                            block = audio.readframes(max(1, int(audio.getframerate() * .025)))
                            if not block:
                                break
                            output.write(block)
                        diagnostic('playback_finished', request_id=request_id, cancelled=cancelled.is_set(), duration_ms=round((time.monotonic()-playback_started)*1000))
                if cancelled.is_set() and output is not None:
                    output.abort()
            completed = not cancelled.is_set()
        except Exception as exc:
            diagnostic('speech_failed', request_id=request_id, error_type=type(exc).__name__, error_code=getattr(exc, 'winerror', None))
            raise
        finally:
            synthesis.shutdown(wait=False, cancel_futures=True)
            self.playback = None
            self.spoken_chunk = ''
            self.spoken_text = ''
            diagnostic('speech_finished', request_id=request_id, completed=completed, cancelled=cancelled.is_set(), duration_ms=round((time.monotonic()-started)*1000))

    def cancel(self):
        diagnostic('response_cancelled', request_id=(self.request or {}).get('id'))
        self.cancelled.set()
        self.speech_paused.clear()
        playback = self.playback
        if playback is not None:
            try:
                playback.abort()
            except Exception:
                pass
        context = self.request
        if context and context.get('modelRunning') and context.get('session'):
            session = context['session']
            self.sessions[context['domain']] = self.session + '-' + context['domain'] + '-' + uuid.uuid4().hex[:6]
            def abort_model():
                try:
                    local_request(18791, '/session/end', {'user': session}, timeout=2)
                except Exception:
                    pass
            threading.Thread(target=abort_model, daemon=True).start()

    def stop_computer_tasks(self):
        active = [job for job in self.research.list('computer') if job['status'] in ('queued', 'running')]
        for job in active:
            self.research.cancel(job['id'], 'computer')
        return bool(active)

    def take_break(self, decision):
        if 'delay_seconds' not in decision or 'wake_at' not in decision:
            raise ValueError('The break needs an explicit time decision')
        state = self.breaks.start(decision.get('delay_seconds'), decision.get('wake_at'))
        self.cancel()
        if self.tasks:
            for job in self.tasks.list():
                self.tasks.cancel(job['id'])
        for job in self.research.list():
            if job['status'] in ('queued', 'running'):
                self.research.cancel(job['id'])
        self.events.put(('break_changed',))
        self.idle()
        return {'ok': True, 'break': state, 'spoken': False, 'tools': []}

    def resume(self):
        self.breaks.resume()
        self.events.put(('break_changed',))
        self.idle()

    def ask_model(self, model, text, decision, **extra):
        context = self.request
        payload = {'model': model, 'input': text, 'user': context['session'],
                   'modelTier': decision.get('model_tier', 'default'), **extra}
        if model == 'openclaw/desktop':
            payload.update(desktopRequestId=context['id'], capabilities=context['capabilities'],
                           ephemeral=hasattr(self, 'task_cancel'), jarvisSettings=self.settings_context())
        context['modelRunning'] = True
        started = time.monotonic()
        diagnostic('model_started', request_id=context['id'], model=model, model_tier=decision.get('model_tier', 'default'))
        try:
            # Writing a whole page to draw something takes longer than a spoken answer.
            response = local_request(18791, '/v1/responses', payload,
                                     timeout=240 if 'show_visual' in context.get('capabilities', []) else 120)
        except Exception as exc:
            diagnostic('model_failed', request_id=context['id'], error_type=type(exc).__name__, duration_ms=round((time.monotonic()-started)*1000))
            raise
        finally:
            context['modelRunning'] = False
        diagnostic('model_finished', request_id=context['id'], model=response.get('model'), duration_ms=round((time.monotonic()-started)*1000))
        self.last_model = response.get('model')
        self.last_reasoning = response.get('reasoning')
        context['modelUsed'], context['reasoningUsed'] = self.last_model, self.last_reasoning
        self.events.put(('model_used', self.last_model, self.last_reasoning))
        if response.get('modelFallback'):
            self.events.put(('notice', 'The requested reasoning model is unavailable. Used ' + str(self.last_model) + '.'))
        # Say once when Claude takes over because OpenAI is not answering, and once when OpenAI is back.
        backup = response.get('backup') is True
        if backup != getattr(self, 'claude_backup', False):
            self.claude_backup = backup
            diagnostic('model_backup_changed', model=self.last_model, state='claude' if backup else 'openai')
            self.events.put(('notice', 'OpenAI is not answering, so Claude is answering for now.' if backup else 'OpenAI is answering again.'))
        return response

    def settings_context(self):
        try:
            return self_settings.describe(self_settings.snapshot(self))
        except Exception as exc:
            diagnostic('settings_context_failed', error_type=type(exc).__name__)
            return ''

    def change_setting(self, setting, value):
        result = self_settings.change(self.preferences, setting, value)
        if result['key'] in ('briefingTime', 'weatherUnits'):
            self.next_briefing_check = 0
        diagnostic('setting_changed', setting=setting, changed=result['changed'])
        self.events.put(('preferences_changed', self.preferences.read()))
        return result

    def allow_lock_in_visit(self, site):
        choices = {'x': 'x', 'instagram': 'instagram', 'any': None}
        if site not in choices:
            raise ValueError('Choose x, instagram or any for the lock in pass.')
        if self.preferences.read().get('lockInMode', True) is not True:
            return {'ok': True, 'granted': False,
                    'note': 'Lock in mode is off, so X and Instagram are not being closed. No pass is needed.'}
        granted = self.focus_guard.grant_pass(choices[site])
        name = ' or '.join(LOCK_IN_NAMES[site] for site in PASS_SITES) if granted['site'] is None else LOCK_IN_NAMES[granted['site']]
        return {'ok': True, 'granted': True, 'site': name, 'visits': 1,
                'note': f'Lock in mode stays on. The first {name} tab the owner goes to stays open, and only that tab: any '
                        f'other {name} tab is still closed. Once he has been off that tab for {round(PASS_AWAY_SECONDS / 60)} '
                        f'minutes the pass is used up and later visits are closed again. An unused pass lapses in '
                        f'{round(PASS_WAIT_SECONDS / 60)} minutes.'}

    def interruption_context(self):
        return {'answer': self.cancelled,
                'ownerRequest': (self.request or {}).get('ownerText', '')[:1500],
                'assistantSpeech': self.spoken_text or self.spoken_chunk}

    def pause_interruption(self, context, *, transcribing=False):
        with self.speech_pause_lock, self.state_lock:
            if self.cancelled is not context['answer'] or self.cancelled.is_set():
                return False
            if self.interruption_pause is not context:
                context['resumeState'] = (self.state, self.note)
            self.interruption_pause = context
            self.speech_paused.set()
            if self.busy.locked():
                self.update('thinking' if transcribing else 'listening',
                            'Understanding your interruption' if transcribing else 'Listening to your interruption')
        diagnostic('interruption_listening_started')
        return True

    def resume_interruption(self, context):
        with self.speech_pause_lock, self.state_lock:
            if context is not None and self.interruption_pause is context:
                self.interruption_pause = None
                self.speech_paused.clear()
                if self.cancelled is context['answer'] and not self.cancelled.is_set() and self.busy.locked():
                    state, note = ('speaking', 'Speaking') if self.spoken_chunk else context['resumeState']
                    self.update(state, note)

    def interrupt(self, text, context=None):
        context = context or self.interruption_context()
        if self.cancelled is not context['answer']:
            self.resume_interruption(context)
            diagnostic('interruption_ignored', reason='newer_response')
            return
        if self.breaks.active:
            self.resume_interruption(context)
            return
        if not self.interrupt_lock.acquire(blocking=False):
            self.resume_interruption(context)
            diagnostic('interruption_ignored', reason='decision_in_progress')
            return
        replacement = confirm = None
        started = time.monotonic()
        try:
            answer = context['answer']
            if self.cancelled is not answer:
                diagnostic('interruption_ignored', reason='newer_response')
                return
            diagnostic('interruption_decision_started', request_id=(self.request or {}).get('id'), state=self.state, heard_chars=len(text))
            try:
                response = local_request(18791, '/v1/responses', {
                    'model': 'openclaw/interrupt', 'user': self.session + '-interrupt',
                    'input': json.dumps({'utterance': text,
                                         'ownerRequest': context['ownerRequest'],
                                         'assistantSpeech': context['assistantSpeech'],
                                         'openQuestion': self.open_question()}, ensure_ascii=False),
                }, timeout=12)
                decision = json.loads(response_text(response))
                action = decision.get('action')
                if action not in ('stop', 'shorter', 'followup', 'ignore', 'break', 'dismiss'):
                    raise ValueError('Invalid interruption decision')
                diagnostic('interruption_decision_finished', action=action, duration_ms=round((time.monotonic()-started)*1000))
            except Exception as exc:
                diagnostic('interruption_decision_failed', error_type=type(exc).__name__, duration_ms=round((time.monotonic()-started)*1000))
                self.events.put(('notice', 'Could not understand the interruption. Try again or use Stop current response.'))
                return
            if self.cancelled is not answer:
                diagnostic('interruption_ignored', reason='newer_response')
                return
            if action == 'break':
                # He never goes on an interruption alone: he stops and asks first.
                self.cancel()
                confirm = decision
                return
            if action == 'dismiss':
                self.cancel()
                self.dismiss_current()
                return
            if action not in ('stop', 'shorter', 'followup'):
                diagnostic('interruption_ignored', reason='no_directed_request')
                return
            command = NAME.sub('', text).strip(' ,.!')
            if action == 'followup' and not command:
                return
            self.cancel()
            if action == 'stop':
                self.events.put(('notice', 'Stopped.'))
                return
            if action == 'shorter':
                replacement = ('Give me a shorter version of your last answer, in one sentence.', {'shorten': True})
            elif command:
                replacement = (command, {})
        finally:
            self.resume_interruption(context)
            self.interrupt_lock.release()
            if confirm:
                # Asked once the interruption is over, so the question is spoken like any reply.
                self.confirm_break_aloud(text, confirm)
        if replacement:
            diagnostic('interruption_followup_started', command_chars=len(replacement[0]))
            result = self.submit(replacement[0], wait=True, **replacement[1])
            if not result.get('ok') and result.get('error') != 'Cancelled' and not self.breaks.active:
                self.events.put(('notice', result.get('error', 'The follow-up could not start. Please try again.')))

    def break_confirmation_pending(self):
        """The shut-off Jarvis asked the owner to confirm, while the question is still fresh."""
        pending = self.pending_break
        if pending and time.monotonic() - pending['at'] <= BREAK_CONFIRM_SECONDS:
            return pending['decision']
        return None

    def ask_to_confirm_break(self, text, decision, speak=True):
        """Before going away, Jarvis asks the owner to confirm in his own words. He only goes on a yes."""
        line = acknowledge(text, self.session, {'confirmingBreak': {'delaySeconds': decision.get('delay_seconds'),
                                                                    'wakeAt': decision.get('wake_at')}})
        if line == FALLBACK_ACKNOWLEDGEMENT or '?' not in line:
            line = BREAK_QUESTION
        self.pending_break = {'decision': {key: decision.get(key) for key in ('action', 'delay_seconds', 'wake_at')},
                              'at': time.monotonic()}
        diagnostic('break_confirmation_asked')
        self.events.put(('answer', line, []))
        self.last_answer_text = line
        spoken = False
        if speak:
            self.speak(line)
            spoken = not self.cancelled.is_set()
        return {'ok': True, 'text': line, 'spoken': spoken, 'confirmBreak': True, 'tools': []}

    def confirm_break_aloud(self, text, decision):
        if not self.busy.acquire(timeout=5):
            return
        try:
            self.cancelled = threading.Event()
            self.ask_to_confirm_break(text, decision)
        except Exception as exc:
            diagnostic('break_confirmation_failed', error_type=type(exc).__name__)
        finally:
            self.release_response()

    def open_question(self):
        """The question Jarvis is waiting on an answer to, if any, so an interruption can be read against it."""
        questions = getattr(self, 'questions', None)
        try:
            rows = questions.open() if questions else []
        except Exception:
            rows = []
        return rows[0]['question'][:400] if rows else None

    def dismiss_current(self):
        """The owner dropped what Jarvis was asking about or doing for him, such as never mind or we're not doing it.
        The open question goes, with a task paused on it, or else the task he just started. Jarvis stays."""
        questions = getattr(self, 'questions', None)
        dropped = questions.dismiss() if questions else None
        cancelled = None
        if not dropped and self.last_task_id:
            row = next((row for row in self.activity_list() if row['id'] == self.last_task_id), None)
            if row and row['status'] in ('queued', 'running') and time.time() - row.get('created', 0) <= RECENT_TASK_SECONDS:
                cancelled = self.activity_cancel(row['id']).get('ok') and row['id']
        diagnostic('interruption_dismissed', question=bool(dropped), task_cancelled=bool(cancelled))
        self.events.put(('notice', 'Dropped it.' if dropped or cancelled else 'Stopped.'))

    def tool(self, request_id, name, arguments):
        context = self.request
        if self.tasks and (not context or context['id'] != request_id):
            return self.tasks.tool(request_id, name, arguments)
        if self.breaks.active or not context or context['id'] != request_id or self.cancelled.is_set():
            return {'ok': False, 'error': 'This desktop request has ended'}
        if name not in context['capabilities']:
            return {'ok': False, 'error': 'This tool was not requested by the owner'}
        if not isinstance(arguments, dict):
            return {'ok': False, 'error': 'Invalid tool arguments'}
        if len(context['executed']) >= 10:
            return {'ok': False, 'error': 'Tool limit reached for this request'}
        context['executed'].append(name)
        self.events.put(('tool', name))
        try:
            result = self.execute_tool(context, name, arguments)
        except (ValueError, RuntimeError, OSError) as exc:
            result = {'ok': False, 'error': str(exc)[:400]}
        if result.get('ok') is False:
            context.setdefault('toolFailures', []).append({'tool': name, 'error': result.get('error', 'Tool failed')})
        return result

    def execute_tool(self, context, name, arguments):
        require_capability(context, name)
        if name == 'list_running_processes':
            return process_control.listing(context)
        if name == 'terminate_processes':
            return process_control.terminate(context, arguments.get('targets'), self.cancelled)
        if name in ('get_jarvis_interface', 'move_jarvis_orb'):
            result = queue.Queue(maxsize=1)
            deadline = time.monotonic() + 8
            self.events.put(('interface_tool', name, arguments, result, self.cancelled, deadline))
            while time.monotonic() < deadline and not self.cancelled.is_set():
                try:
                    return result.get(timeout=.05)
                except queue.Empty:
                    pass
            raise RuntimeError('The interface request was cancelled or did not finish in time')
        if name == 'list_models':
            return local_request(18791, '/models', timeout=5)
        if name == 'select_model':
            if arguments.get('scope') not in ('conversation', 'search'):
                raise ValueError('Choose conversation or search model settings.')
            result = switch_model(arguments['scope'], arguments.get('model', ''))
            if result.get('ok'):
                self.events.put(('models_changed',))
            return result
        if name == 'show_model_settings':
            self.events.put(('model_settings',))
            return {'ok': True, 'status': 'opened'}
        if name == 'change_jarvis_setting':
            return self.change_setting(arguments.get('setting'), arguments.get('value'))
        if name == 'allow_lock_in_visit':
            return self.allow_lock_in_visit(arguments.get('site'))
        if name == 'list_layout_windows':
            return self.layouts.listing(context)
        if name == 'arrange_windows':
            return self.layouts.arrange(context, arguments.get('placements'), self.cancelled)
        if name in PROJECT_TOOLS:
            owner = context.get('ownerText', '')
            if name == 'list_projects':
                result = self.projects.list(arguments.get('query', ''))
                result['recent_tasks'] = [{key: row.get(key) for key in ('id', 'question', 'scope')}
                                          for row in self.activity_list() if row['status'] == 'completed' and row['scope'] != 'projects'][:8]
                return result
            if name == 'read_project':
                return self.projects.read(arguments.get('id', ''))
            if name == 'resume_project':
                require_capability(context, name)
                return self.projects.resume(arguments.get('id', ''), self.research, owner)
            require_capability(context, name)
            source = arguments.get('job_id', '')
            job = None
            if source:
                rows = [row for row in self.activity_list() if row['status'] == 'completed' and row['scope'] != 'projects']
                job = next((row for row in rows if source == 'latest' or row['id'] == source), None)
                if not job:
                    raise ValueError('There is no matching completed task to save.')
            result = self.projects.save(arguments.get('title', ''), arguments.get('findings', ''),
                arguments.get('decisions', ''), arguments.get('next_steps', ''),
                arguments.get('id', ''), arguments.get('revision', ''), job=job)
            self.events.put(('projects_changed',))
            return result
        personal = self.personal.execute(context, name, arguments) if getattr(self, 'personal', None) else None
        if personal is not None:
            return personal
        if name == 'list_apps':
            query = str(arguments.get('query', ''))
            apps = self.apps.match(query) if query else self.apps.apps
            return {'ok': True, 'apps': [row['Name'] for row in apps[:150]]}
        if name == 'launch_app':
            chosen = self.apps.match(str(arguments.get('name', '')))
            require_capability(context, name)
            if len(chosen) != 1:
                return {'ok': False, 'error': 'Choose one app from the current installed app catalog'}
            return self.apps.launch(chosen[0]['Name'])
        if name in ('start_web_search', 'start_email_search'):
            scope = 'email' if name == 'start_email_search' else 'web'
            return self.research.start(arguments.get('question', ''), scope, arguments.get('provider', 'all'), model_tier=context.get('modelTier', 'default'))
        if name in ('web_search_status', 'email_search_status'):
            return self.activity_status(arguments.get('id') or None, 'email' if name == 'email_search_status' else 'web')
        if name == 'start_computer_task':
            return self.research.start(context.get('ownerText', ''), 'computer', model_tier=context.get('modelTier', 'default'))
        if name == 'computer_status':
            return self.activity_status(arguments.get('id') or None, 'computer')
        if name == 'email_accounts':
            return self.mail.accounts()
        if name == 'search_email':
            return self.mail.search(arguments.get('provider', 'all'), arguments.get('query', ''),
                                    arguments.get('unread_only', False), arguments.get('limit', 5))
        if name == 'read_email':
            return self.mail.read(arguments.get('id', ''))
        if name == 'get_briefing':
            return self.briefing.collect()
        if name == 'get_weather':
            return self.briefing.weather.current()
        if name == 'show_visual':
            return self.visuals.show(arguments.get('title', ''), arguments.get('html', ''))
        if name == 'get_network_info':
            return inspect_network(arguments.get('kind', 'public'))
        if name == 'start_powershell':
            require_capability(context, name)
            return self.research.start(context['ownerText'], 'powershell', powershell_command=arguments.get('command', ''))
        if name == 'powershell_status':
            return self.activity_status(arguments.get('id') or None, 'powershell')
        if name == 'search_files':
            result = self.files.search(arguments.get('query', ''), arguments.get('after', ''), arguments.get('before', ''),
                arguments.get('date_field', 'created'), arguments.get('extension', ''), arguments.get('limit', 12))
            self.events.put(('files_found', result))
            return result
        if name == 'read_file':
            return self.files.read(arguments.get('id', ''))
        if name in BROWSER_TOOLS:
            return self.browser.execute(name, arguments, context.get('ownerText', ''), self.cancelled, browser=context.get('browser', ''))
        if name == 'list_reminders':
            from datetime import datetime
            return {**self.reminders.list(), 'local_time': datetime.now().astimezone().isoformat()}
        if name == 'create_reminder':
            result = self.reminders.create(arguments.get('text', ''), arguments.get('due_at', ''),
                arguments.get('delay_seconds', 0), arguments.get('repeat', 'none'), arguments.get('interval_seconds', 0),
                arguments.get('morning_of', ''))
            self.events.put(('reminders_changed',))
            return result
        if name == 'change_reminder':
            result = self.reminders.change(arguments.get('action'), arguments.get('id', ''), arguments.get('seconds', 600))
            self.events.put(('reminders_changed',))
            return result
        return {'ok': False, 'error': 'Unknown tool'}

    def activity_list(self):
        tasks = self.tasks.list() if self.tasks else []
        children = {identifier for job in tasks for identifier in [job.get('childId'), *job.get('childIds', [])]}
        return sorted(tasks + [job for job in self.research.list() if job['id'] not in children], key=lambda row:row['created'], reverse=True)

    def activity_status(self, identifier=None, scope=None):
        rows = [row for row in self.activity_list() if (identifier or row['scope'] != 'task_status')
                and (not scope or row['scope'] == scope)]
        if not identifier:
            identifier = self.last_task_id if any(row['id'] == self.last_task_id for row in rows) else None
        job = next((row for row in rows if not identifier or identifier in (row['id'], row.get('childId'), *row.get('childIds', []))), None)
        return {'ok':True, **job} if job else {'ok':False, 'error':'There is no matching task yet'}

    def task_context(self):
        rows = [row for row in self.activity_list() if row['scope'] != 'task_status'
                and row['id'] != getattr(self, 'task_job_id', None)][:4]
        now = time.time()
        return [{'id': row['id'], 'scope': row['scope'], 'question': row['question'][:220],
                 'started': ago(now - row['created']) if row.get('created') else None,
                 'finished': ago(now - row['finished']) if row.get('finished') else None,
                 'status': row['status'], 'note': row.get('note', '')[:240],
                 'summary': row.get('summary', '')[:400], 'steps': [step[:160] for step in row.get('steps', [])[-2:]]}
                for row in rows]

    def activity_cancel(self, identifier=None, scope=None):
        row = next((row for row in self.activity_list() if (not identifier or row['id'] == identifier)
                    and (not scope or row['scope'] == scope) and row['status'] in ('queued','running','waiting')), None)
        if not row:
            return {'ok':False, 'error':'No matching task is running'}
        return self.tasks.cancel(row['id']) if self.tasks and row['id'].startswith('task-') else self.research.cancel(row['id'], scope)

    def activity_frame(self, identifier):
        job = self.activity_status(identifier)
        return self.research.frame(job.get('childId', identifier))

    def make_task_agent(self, events, job):
        worker = Controller(events)
        for name in ('apps','mail','computer','reminders','preferences','breaks','files','personal','projects','layouts','browser','closed'):
            setattr(worker, name, getattr(self, name))
        worker.tasks = None
        worker.research = TaskResearch(self.tasks, job)
        worker.task_cancel = job['_cancel']
        worker.task_foreground = job['_foreground']
        worker.task_job_id = job['id']
        worker.task_decision = job.get('_decision')
        worker.last_task_id = self.last_task_id
        worker.last_domain = job['_previousDomain']
        worker.last_answer_text = job['_previousAnswer']
        worker.last_answer_domain = job['_previousDomain']
        worker.last_command_time = time.monotonic()
        worker.take_break = self.take_break
        worker.activity_list = self.activity_list
        worker.activity_status = self.activity_status
        worker.activity_cancel = self.activity_cancel
        worker.settings_context = self.settings_context
        worker.change_setting = self.change_setting
        worker.allow_lock_in_visit = self.allow_lock_in_visit
        worker.layouts.lock = self.computer.lock
        return worker

    def submit(self, text, speak=True, research_id=None, shorten=False, wait=False):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
            return {'ok':False, 'error':'Enter a command between 1 and 4000 characters'}
        text = NAME.sub('', text).strip(' ,')
        options = {}
        if not speak: options['speak'] = False
        if research_id: options['research_id'] = research_id
        if shorten: options['shorten'] = True
        if wait: options['wait'] = True
        return self.respond(text, **options)

    def acknowledge_wake(self):
        if not self.voice_ready or self.muted or self.breaks.active or not self.busy.acquire(blocking=False):
            return False
        diagnostic('wake_acknowledged')
        def reply():
            try:
                self.cancelled = threading.Event()
                self.speak(WAKE_REPLY)
            except Exception:
                pass
            finally:
                self.release_response()
        threading.Thread(target=reply, name='jarvis-wake-reply', daemon=True).start()
        return True

    def deliver_task_completion(self):
        if self.completed_tasks.empty() or self.muted or not self.voice_ready or not self.busy.acquire(blocking=False):
            return
        try:
            row = self.completed_tasks.get_nowait()
            question = row.get('status') == 'waiting'
            if question and not (self.tasks and self.tasks.waiting(row['id'])):
                # Answered or cancelled before he was free to ask it.
                return
            reference = self.activity_status(row['taskReference']) if row.get('taskReference') else row
            domain = reference.get('scope', 'web')
            self.last_answer_text = row['summary'][:1800]
            self.last_answer_domain = domain
            self.last_domain, self.last_command_time = domain, time.monotonic()
            self.last_task_id = row.get('taskReference') or row['id']
            self.cancelled = threading.Event()
            if question:
                self.questions.spoken(row['id'], row['summary'])
            self.speak(row['summary'][:1800])
        finally:
            self.release_response()

    def respond(self, text, speak=True, research_id=None, shorten=False, wait=False):
        if self.breaks.active:
            diagnostic('command_rejected', reason='break')
            return {'ok': False, 'error': 'Jarvis is taking a break. Open start-jarvis.cmd to bring it back.', 'break': self.breaks.snapshot()}
        text = text.strip()
        if not text or len(text) > 4000:
            diagnostic('command_rejected', reason='invalid_length', command_chars=len(text))
            return {'ok': False, 'error': 'Enter a command between 1 and 4000 characters'}
        acquired = self.busy.acquire(timeout=20) if wait else self.busy.acquire(blocking=False)
        if not acquired:
            diagnostic('command_rejected', reason='busy', command_chars=len(text))
            return {'ok': False, 'error': 'Jarvis is already answering'}
        if self.breaks.active:
            diagnostic('command_rejected', reason='break')
            self.busy.release()
            return {'ok': False, 'error': 'Jarvis is taking a break', 'break': self.breaks.snapshot()}
        self.cancelled = getattr(self, 'task_cancel', None) or threading.Event()
        if self.cancelled.is_set():
            self.busy.release()
            return {'ok':False, 'error':'Cancelled'}
        self.speech_paused.clear()
        self.update('thinking', 'Thinking')
        self.events.put(('question', text))
        if time.monotonic() - self.last_command_time > 60:
            self.last_domain = None
        domain = (self.last_answer_domain or self.last_domain or 'web') if shorten else 'web'
        self.request = {'id': uuid.uuid4().hex, 'capabilities': [], 'appQuery': None,
                        'executed': [], 'session': self.sessions[domain], 'domain': domain, 'ownerText': text}
        from desktop.computer import USER
        self.request['foreground'] = getattr(self, 'task_foreground', int(USER.GetForegroundWindow() or 0))
        started = time.monotonic()
        finished_spoken = False
        completed = False
        queued_job = answered_job = clarifying_status = None
        answer_status = 'completed'
        diagnostic('command_started', request_id=self.request['id'], command_chars=len(text), domain=domain)
        try:
            if not shorten and not research_id:
                intent_started = time.monotonic()
                diagnostic('intent_started', request_id=self.request['id'])
                # The owner's previous request too, so a follow-up such as look that up knows what to search for.
                intent_context = {'topic': self.last_domain, 'now': local_time(),
                                  'awaitingBreakConfirmation': self.break_confirmation_pending() is not None,
                                  'lastRequest': self.last_owner_text[:500] if self.last_domain else '',
                                  'lastRequestWhen': ago(time.time() - self.last_owner_at) if self.last_owner_at and self.last_domain else None,
                                  'lastAnswer': self.last_answer_text[:1000] if self.last_domain else '',
                                  'recentTasks': self.task_context(), 'lastTaskId': self.last_task_id,
                                  'waitingQuestions': self.questions.context()}
                decision = getattr(self, 'task_decision', None) or classify_request(text, self.session, intent_context)
                diagnostic('intent_finished', request_id=self.request['id'], action=decision.get('action'), model_tier=decision.get('model_tier'), needs_clarification=decision.get('needs_clarification'), duration_ms=round((time.monotonic()-intent_started)*1000))
                if self.cancelled.is_set():
                    return {'ok': False, 'error': 'Cancelled'}
                if not hasattr(self, 'task_cancel'):
                    asked = self.questions.find(decision.get('task_id')) if decision['action'] == 'task_answer' else None
                    if asked and not asked.get('taskId'):
                        # He answered a question asked before anything started. His request and this answer are one
                        # request now, understood and carried out as if he had said it all at once.
                        self.questions.close(asked['id'])
                        text = with_answers(asked['request'], [{'question': asked['question'], 'answer': text}])
                        self.request['ownerText'] = text
                        intent_context['waitingQuestions'] = self.questions.context()
                        intent_started = time.monotonic()
                        decision = classify_request(text, self.session, intent_context)
                        diagnostic('intent_finished', request_id=self.request['id'], action=decision.get('action'), model_tier=decision.get('model_tier'), needs_clarification=decision.get('needs_clarification'), source='answer', duration_ms=round((time.monotonic()-intent_started)*1000))
                        if self.cancelled.is_set():
                            return {'ok': False, 'error': 'Cancelled'}
                    elif decision['action'] != 'task_answer':
                        self.questions.superseded()
                if decision['action'] == 'break' and not decision.get('needs_clarification'):
                    asked = self.break_confirmation_pending()
                    self.pending_break = None
                    if not asked:
                        return self.ask_to_confirm_break(text, decision, speak)
                    # Confirmed: a time given with the yes wins, otherwise the one he asked about.
                    timing = decision if decision.get('delay_seconds') is not None or decision.get('wake_at') else asked
                    return self.take_break({**asked, 'delay_seconds': timing.get('delay_seconds'), 'wake_at': timing.get('wake_at')})
                self.pending_break = None
            else:
                decision = {'action': 'normal'}
            capabilities, _, domain = capabilities_for(decision) if not shorten else ([], None, domain)
            self.request.update(domain=domain, session=self.sessions[domain], capabilities=capabilities,
                                modelTier=decision.get('model_tier', 'default'),
                                processNames=decision.get('process_names', []),
                                systemActions=decision.get('system_actions', []), browser=decision.get('browser', ''))
            if hasattr(self, 'task_cancel'):
                self.events.put(('task_stage', 'working', 'Selected ' + {'default':'the default model', 'sol':'Sol', 'astra':'Astra'}[decision.get('model_tier','default')]))
            control = 'status' if research_id or decision['action'] == 'task_status' else 'cancel' if decision['action'] == 'task_cancel' else None
            if decision.get('needs_clarification'):
                # For a question, only the read-only public web search stays available, because an unfamiliar name or
                # a recent fact is better looked up than asked about. Nothing on the computer is operated on a guess.
                question = decision.get('action', 'normal') == 'normal'
                self.request['capabilities'] = list(CLARIFYING_TOOLS) if question else []
                response = self.ask_model('openclaw/desktop', text + (CLARIFYING_NOTE if question else
                    '\nThe request is ambiguous or missing essential details. Ask one concise clarifying question before doing anything. Do not guess what to operate.'), decision)
                answer, sources = response_text(response), []
                clarifying_status = response.get('taskStatus', 'needs_clarification')
            elif control == 'status' and not shorten:
                identifier = research_id or decision.get('task_id') or None
                scope = None
                result = self.activity_status(identifier, scope)
                answer, sources = ((email_policy.UNAVAILABLE, []) if not result.get('ok') and scope == 'email'
                                   and not email_policy.enabled() else progress_reply(result))
                domain = result.get('scope', domain)
                if result.get('ok'):
                    self.request['executed'].append('task_status')
                self.request['taskReference'] = result.get('id')
                if result.get('id'):
                    self.last_task_id = result['id']
            elif decision['action'] == 'screen':
                domain, capabilities = 'screen', []
                self.request.update(domain=domain, session=self.sessions[domain], capabilities=[])
                self.update('thinking', 'Looking at your screen')
                frame = capture_screen(decision.get('display', 'current'), self.computer.open_window(text))
                if self.cancelled.is_set() or self.breaks.active:
                    return {'ok': False, 'error': 'Cancelled'}
                self.request['executed'].append('view_screen')
                self.events.put(('screen_view', frame))
                view_context = json.dumps({'display': frame.get('display'), 'rect': frame.get('rect'),
                                           'monitors': frame.get('monitors', [])})
                response = self.ask_model('openclaw/screen', text + '\nCaptured screen metadata: ' + view_context, decision, image=frame['image'])
                answer, sources = response_text(response), []
                if not answer:
                    raise RuntimeError('The selected model did not return a screen description, sir.')
            elif decision['action'] == 'processes':
                domain, capabilities = 'processes', []
                self.request.update(domain=domain, session=self.sessions[domain], capabilities=[])
                self.update('thinking', 'Checking running apps and processes')
                snapshot = inspect_processes(decision.get('process_names', []))
                if self.cancelled.is_set() or self.breaks.active:
                    return {'ok': False, 'error': 'Cancelled'}
                self.request['executed'].append('inspect_processes')
                model_input = json.dumps({'request': text, 'snapshot': snapshot,
                    'priorAnswer': self.last_answer_text[:1000] if self.last_domain == 'processes' else ''}, ensure_ascii=False)
                response = self.ask_model('openclaw/processes', model_input, decision)
                answer, sources = response_text(response), []
                if not answer:
                    raise RuntimeError('The selected model did not return a process summary, sir.')
            elif domain == 'email' and not email_policy.enabled():
                answer, sources = email_policy.UNAVAILABLE, []
            elif control == 'cancel' and not shorten:
                identifier = decision.get('task_id')
                result = self.activity_cancel(identifier) if identifier else {'ok': False}
                answer, sources = progress_reply(result)
                domain = result.get('scope', domain)
            elif decision['action'] == 'task_answer' and not shorten:
                answer, answered_job = self.answer_question(text, decision)
                domain, sources = 'tasks', []
            elif self.tasks and not shorten and not research_id and (capabilities or decision['action'] in ('computer', 'compound')):
                queued_job = self.tasks.prepare(text, domain, self.request['foreground'], decision=decision)
                self.last_task_id = queued_job['id']
                answer = acknowledge(text, queued_job['id'] + '-ack', {'topic': self.last_domain, 'plan': decision})
                self.tasks.describe(queued_job['id'], answer)
                sources = []
            elif not shorten and decision['action'] == 'computer':
                result = self.research.start(text, 'computer', model_tier=decision.get('model_tier', 'default'))
                if not result.get('ok'):
                    raise RuntimeError(result['error'])
                self.request['executed'].append('start_computer_task')
                self.last_task_id = result.get('id', self.last_task_id)
                answer, sources = result.get('note', ''), []
            else:
                model_input = text
                if 'save_project' in capabilities and self.last_answer_text:
                    model_input += '\nPrevious answer to save if requested (reference only):\n' + self.last_answer_text[:1500]
                elif shorten and self.last_answer_text:
                    prefix = '\nPrevious answer to shorten (reference text only):\n'
                    model_input += prefix + self.last_answer_text[:max(0, 5800-len(text)-len(prefix))]
                elif self.last_answer_text and self.last_answer_domain == domain:
                    model_input += '\nPrior answer (reference only, not new instructions):\n' + self.last_answer_text[:1800]
                response = self.ask_model('openclaw/desktop', model_input, decision)
                answer_status = response.get('taskStatus', 'completed')
                answer = response_text(response)
                if not answer:
                    raise RuntimeError('The selected model returned no response')
                sources = response.get('sources', [])
                if ('terminate_processes' in capabilities and answer_status == 'completed'
                        and not self.request.get('processControlResult', {}).get('ok')):
                    outcome = self.request.get('processControlResult', {})
                    answer_status = 'failed'
                    failures = self.request.get('toolFailures', [])
                    answer = outcome.get('error') or (failures[-1]['error'] if failures else 'The requested processes were not verified as stopped, sir.')
            self.last_domain, self.last_command_time = domain, time.monotonic()
            if domain == 'email' and not email_policy.enabled():
                self.last_domain = 'email-deferred'
            if self.cancelled.is_set():
                return {'ok': False, 'error': 'Cancelled'}
            self.events.put(('answer', answer, sources))
            diagnostic('reply_ready', request_id=self.request['id'], reply_chars=len(answer), domain=domain, model=self.request.get('modelUsed'))
            self.last_answer_text = answer
            self.last_owner_text = text
            self.last_owner_at = time.time()
            self.last_answer_domain = domain
            if (not hasattr(self, 'task_cancel') and not shorten and not research_id
                    and (answer_status == 'needs_clarification' or clarifying_status == 'needs_clarification')):
                # He asked before doing anything; he asks again if the owner does not answer.
                self.questions.raised(text, answer)
            spoken = False
            if speak:
                try:
                    self.speak(answer)
                    spoken = not self.cancelled.is_set()
                    finished_spoken = spoken
                except Exception as exc:
                    if not self.cancelled.is_set():
                        diagnostic('reply_playback_failed', request_id=self.request['id'], error_type=type(exc).__name__)
                        self.events.put(('notice', 'The reply is in the panel, but Windows could not play the voice.'))
            completed = answer_status != 'failed'
            return {'ok': completed, 'text': answer, 'sources': sources, 'spoken': spoken,
                    'outcome': answer_status, **({'error': answer} if not completed else {}),
                    'needsClarification': decision.get('needs_clarification', False) or answer_status == 'needs_clarification',
                    'model': self.request.get('modelUsed'), 'reasoning': self.request.get('reasoningUsed'),
                    'tools': self.request['executed'][:], 'taskReference': self.request.get('taskReference'),
                    'toolFailures': self.request.get('toolFailures', []),
                    **({'taskId': queued_job['id'], 'status': 'queued'} if queued_job else {})}
        except Exception as exc:
            diagnostic('command_failed', request_id=self.request['id'], error_type=type(exc).__name__, duration_ms=round((time.monotonic()-started)*1000))
            if self.cancelled.is_set():
                return {'ok': False, 'error': 'Cancelled'}
            message = 'I could not reach the selected model, sir. Check your connection, model access and Codex sign-in.'
            if isinstance(exc, RuntimeError):
                message = str(exc)[:200]
            self.events.put(('answer', message, []))
            if speak and not self.cancelled.is_set():
                try:
                    self.speak(message)
                    finished_spoken = not self.cancelled.is_set()
                except Exception:
                    pass
            return {'ok': False, 'error': message, 'type': type(exc).__name__}
        finally:
            diagnostic('command_finished', request_id=self.request['id'], completed=completed, spoken=finished_spoken, cancelled=self.cancelled.is_set(), duration_ms=round((time.monotonic()-started)*1000))
            if not hasattr(self, 'task_cancel'):
                self.cancelled.set()
            self.request = None
            self.events.put(('finished', finished_spoken))
            self.release_response()
            if queued_job:
                self.tasks.release(queued_job['id'])
            if answered_job:
                self.tasks.release(answered_job)

    def answer_question(self, text, decision):
        """The owner answered the question a paused task is waiting on. The task carries on with his answer once
        this acknowledgement has been spoken."""
        asked = self.questions.find(decision.get('task_id'))
        result = (self.tasks.answer(asked['taskId'], text, release=False) if self.tasks and asked and asked.get('taskId')
                  else {'ok': False})
        if not result.get('ok'):
            raise RuntimeError('That task is no longer waiting for an answer, sir. Please ask me again.')
        try:
            self.questions.close(asked['id'])
            self.last_task_id = asked['taskId']
            self.request['executed'].append('task_answer')
            self.request['taskReference'] = asked['taskId']
            answer = acknowledge(text, asked['taskId'] + '-answer', {'topic': 'tasks', 'answering': {
                'request': asked['request'][:600], 'question': asked['question'][:600], 'waitingWithIt': asked.get('held', [])[:4]}})
        except Exception:
            # The task has its answer either way; it must not be left queued without a worker.
            self.tasks.release(asked['taskId'])
            raise
        return answer, asked['taskId']

    def serve(self, port=18792):
        controller = self
        expected = 'Bearer ' + auth_token()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def authorized(self):
                return hmac.compare_digest(self.headers.get('Authorization', ''), expected)
            def reply(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def do_GET(self):
                if not self.authorized():
                    return self.reply(401, {'error': 'Unauthorized'})
                if self.path in ('/health', '/status'):
                    return self.reply(200, controller.health())
                if self.path == '/email/status':
                    return self.reply(200, mail_status())
                if self.path == '/research/jobs':
                    return self.reply(200, {'jobs': controller.activity_list()})
                if self.path == '/projects':
                    return self.reply(200, controller.projects.list())
                if self.path == '/reminders':
                    return self.reply(200, controller.reminders.list())
                if self.path == '/models':
                    try:
                        return self.reply(200, local_request(18791, '/models', timeout=5))
                    except Exception:
                        return self.reply(503, {'error': 'The model service is unavailable'})
                self.reply(404, {'error': 'Not found'})
            def do_POST(self):
                if not self.authorized():
                    return self.reply(401, {'error': 'Unauthorized'})
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if size < 0 or size > 262144:
                        return self.reply(413, {'error': 'Request too large'})
                    self.connection.settimeout(130)
                    data = json.loads(self.rfile.read(size) or b'{}')
                    if self.path == '/tools':
                        return self.reply(200, controller.tool(data.get('requestId'), data.get('name'), data.get('arguments')))
                    if self.path == '/command':
                        return self.reply(200, controller.submit(data.get('text', ''), data.get('speak', True), data.get('researchId')))
                    if self.path == '/shutdown':
                        controller.events.put(('quit',))
                        return self.reply(200, {'status': 'stopping'})
                    if self.path == '/show':
                        controller.resume()
                        controller.events.put(('show',))
                        return self.reply(200, {'status': 'showing'})
                    if self.path == '/email/settings':
                        controller.events.put(('email_settings',))
                        return self.reply(200, {'status': 'showing'})
                    if self.path == '/models/settings':
                        controller.events.put(('model_settings',))
                        return self.reply(200, {'status': 'showing'})
                    if self.path == '/models':
                        return self.reply(200, local_request(18791, '/models', data, timeout=5))
                    if self.path == '/research/cancel':
                        return self.reply(200, controller.activity_cancel(data.get('id')))
                    return self.reply(404, {'error': 'Not found'})
                except Exception as exc:
                    self.reply(400, {'ok': False, 'error': 'Desktop operation failed', 'type': type(exc).__name__})
        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.server.serve_forever()
