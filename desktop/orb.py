from __future__ import annotations

import base64
import ctypes
import json
import os
import pathlib
import queue
import sys
import threading
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / '.runtime/python'))

from desktop.common import local_request, secret_path
from desktop.controller import Controller
from desktop.listener import Listener
from desktop.hotkey import InsertHotkey
from desktop import mail
from desktop import email_policy
from desktop.windows import monitors, preferred_monitor, set_startup, startup_enabled, desktop_name
from desktop.web_tools import validate_url
from desktop.diagnostics import event as diagnostic, LOG_PATH
from desktop.orb_visual import VoiceLevels, icon_png
from desktop.task_progress import current_tasks
from desktop.ui_bridge import UIBridge, plain
from desktop.displays import display_inventory, select_display
from desktop.welcome import WELCOME_HOLD_MS, WELCOME_SCALE

STATE_NAMES = {'ready': 'Ready', 'listening': 'Listening', 'thinking': 'Thinking', 'speaking': 'Speaking',
               'muted': 'Muted', 'starting': 'Starting', 'error': 'Attention', 'hidden': 'Hidden', 'away': 'On break'}
GREETING = 'At your service, sir. Say Jarvis, then ask a question.'
HISTORY = 40
ICON = ROOT / '.cache/jarvis-ui/jarvis-orb.png'
LOOP_CALLS = {'snapshot', 'activate', 'toggle_jarvis', 'submit', 'set_muted', 'select_screen', 'screens',
              'set_setting', 'voice_meter', 'quit'}
METER_LEASE = 4.0
METER_INTERVAL = 1 / 20
STARTUP_BLOCKED = 'Windows blocked changing startup. Run scripts/windows/enable-startup.cmd from this folder in your normal Windows session.'


def physical_monitors():
    from desktop.computer import USER
    previous = USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        return monitors()
    finally:
        if previous:
            USER.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def window_icon():
    try:
        if not ICON.is_file():
            ICON.parent.mkdir(parents=True, exist_ok=True)
            temporary = ICON.with_suffix('.tmp')
            temporary.write_bytes(base64.b64decode(icon_png(256)))
            temporary.replace(ICON)
        return str(ICON)
    except Exception:
        return ''


def safe_sources(sources, limit=6):
    rows = []
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        try:
            url = validate_url(str(source.get('url', '')))
        except ValueError:
            continue
        rows.append({'title': str(source.get('title') or 'Source')[:160], 'url': url})
        if len(rows) == limit:
            break
    return rows


class Orb:
    def __init__(self, interface=True, ui_arguments=(), ui_environment=None):
        self.settings_file = ROOT / 'desktop/settings.json'
        self.settings = json.loads(self.settings_file.read_text(encoding='utf-8'))
        self.settings_lock = threading.Lock()
        self.events = queue.Queue()
        self.controller = Controller(self.events)
        self.controller.app_settings = self.settings
        self.hotkey = InsertHotkey(self.events)
        self.controller.hotkey = self.hotkey
        self.listener = Listener(self.controller, self.settings)
        self.controller.listener = self.listener
        self.voice = VoiceLevels()
        self.voice.tap(self.listener)
        self.break_hidden = False
        self.ui_hidden = False
        self.windows_hidden = False
        self.screens = physical_monitors()
        self.search_jobs = {}
        self.announced_jobs = set()
        self.conversation = []
        self.progress = []
        self.browser_note = 'Ask Jarvis to visit a website.'
        self.closing = False
        self.next_task_refresh = self.next_level = 0.0
        self.meter_until = self.next_meter = 0.0
        self.next_sweep = time.monotonic() + 30
        self.controller.interface = {'kind': 'electron', 'running': False}
        self.ui = None
        if interface:
            environment = {'JARVIS_UI_ICON': window_icon(), **(ui_environment or {})}
            self.ui = UIBridge(self.ui_call, self.ui_ready, ui_arguments, environment, on_exit=self.ui_exited)
        self.sync_break_visibility()

    def send(self, name, data=None):
        if self.ui:
            self.ui.send(name, data)

    def ui_ready(self):
        from desktop.computer import OWN_PROCESSES
        if self.ui and self.ui.process:
            OWN_PROCESSES.add(self.ui.process.pid)
        self.controller.interface['running'] = True
        diagnostic('ui_ready')

    def ui_exited(self, code):
        self.controller.interface['running'] = False

    def ui_call(self, identifier, method, params):
        handler = getattr(self, 'call_' + method, None)
        if handler is None:
            if self.ui:
                self.ui.reply(identifier, False, error='Unknown action')
            return
        if method in LOOP_CALLS:
            self.events.put(('ui_call', identifier, handler, params))
        else:
            threading.Thread(target=self.finish_call, args=(identifier, handler, params), daemon=True).start()

    def finish_call(self, identifier, handler, params):
        try:
            result = handler(**params)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
            if self.ui:
                self.ui.reply(identifier, False, error=str(exc)[:300] or type(exc).__name__)
            return
        except Exception as exc:
            diagnostic('ui_call_failed', method=handler.__name__, error_type=type(exc).__name__)
            if self.ui:
                self.ui.reply(identifier, False, error='Jarvis could not complete that action.')
            return
        if self.ui:
            self.ui.reply(identifier, True, result)

    def sync_break_visibility(self):
        active = self.controller.breaks.active
        changed = active != self.break_hidden
        if changed:
            self.break_hidden = active
            self.listener.listening_until = 0
            self.listener.reset.set()
            if active:
                self.listener.conversation_active = False
                self.ui_hidden = False
        hidden = active or self.ui_hidden
        self.controller.ui_visible = not hidden
        if hidden == self.windows_hidden:
            return
        self.windows_hidden = hidden
        self.listener.hide(hidden)
        reason = 'break' if active or changed else 'toggle'
        diagnostic('visibility_changed', ui_visible=not hidden, reason=reason)
        self.send('visibility', {'hidden': hidden, 'reason': reason})
        self.refresh_progress()

    def save_settings(self):
        with self.settings_lock:
            temporary = self.settings_file.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.settings, indent=2) + '\n', encoding='utf-8')
            temporary.replace(self.settings_file)

    def start_services(self):
        diagnostic('desktop_started', version=19, windows_desktop=self.controller.windows_desktop)
        self.hotkey.start()
        threading.Thread(target=self.controller.serve, daemon=True).start()
        threading.Thread(target=self.controller.background_loop, daemon=True).start()
        self.controller.focus_guard.start()
        self.controller.welcome.start()
        try:
            self.controller.warmup()
        except Exception as exc:
            diagnostic('desktop_warmup_failed', error_type=type(exc).__name__)
            self.events.put(('notice', 'Jarvis could not load its voice or app list. Check logs/desktop-stderr.log.'))
            print(json.dumps({'event': 'desktop_start_failed', 'type': type(exc).__name__}), flush=True)
        self.listener_thread = threading.Thread(target=self.listener.run, daemon=True)
        self.listener_thread.start()
        self.preload_speech()

    def preload_speech(self):
        if self.controller.preferences.read().get('offlineSpeech', 'auto') != 'online':
            from desktop.offline_speech import preload
            preload()

    def start_interface(self):
        if not self.ui:
            return
        try:
            self.ui.start()
        except (OSError, RuntimeError) as exc:
            diagnostic('ui_start_failed', error_type=type(exc).__name__)
            print(json.dumps({'event': 'ui_start_failed', 'error': str(exc)}), flush=True)

    def run(self):
        threading.Thread(target=self.start_services, daemon=True).start()
        self.start_interface()
        while not self.closing:
            self.step(.03)

    def step(self, timeout=0.0):
        try:
            event = self.events.get(timeout=timeout) if timeout else self.events.get_nowait()
            while not self.closing:
                self.handle(event)
                event = self.events.get_nowait()
        except queue.Empty:
            pass
        if not self.closing:
            self.tick(time.monotonic())

    def tick(self, now):
        self.sync_break_visibility()
        if now >= self.next_task_refresh:
            self.next_task_refresh = now + .2
            self.refresh_progress()
        if now >= self.next_sweep:
            self.next_sweep = now + 30
            self.refresh_jobs()
        state = self.controller.state
        if not self.windows_hidden and state in ('listening', 'speaking') and now >= self.next_level:
            self.next_level = now + 1 / 30
            self.send('level', round(self.voice.level(state, now), 3))
        if not self.windows_hidden and now < self.meter_until and now >= self.next_meter:
            self.next_meter = now + METER_INTERVAL
            self.send('mic_level', self.microphone_meter(now))

    def microphone_meter(self, now):
        if self.listener.muted:
            return {'level': 0.0, 'status': 'muted'}
        if not self.controller.microphone_ready:
            return {'level': 0.0, 'status': 'unavailable'}
        return {'level': round(self.voice.input_level(now), 3), 'status': 'live'}

    def refresh_progress(self):
        items = []
        if not self.windows_hidden:
            request = None if self.controller.cancelled.is_set() else self.controller.request
            items = current_tasks(self.controller.activity_list(), request,
                                  self.controller.state, self.controller.note)
        if items != self.progress:
            self.progress = items
            self.send('progress', items)

    def handle(self, event):
        kind = event[0]
        if kind == 'ui_call':
            self.finish_call(*event[1:])
            return
        if kind == 'toggle_jarvis':
            self.toggle_jarvis()
            return
        if kind == 'hotkey_status':
            diagnostic('hotkey_status', installed=event[1].get('installed'), on_user_desktop=event[1].get('availableOnUserDesktop'))
            if event[1].get('error'):
                self.notice(event[1]['error'], 'error')
            return
        if kind == 'break_changed':
            self.sync_break_visibility()
            return
        if self.controller.breaks.active and kind not in ('show', 'quit'):
            return
        handler = getattr(self, 'on_' + kind, None)
        if handler:
            handler(*event[1:])

    def activate(self, source='orb'):
        self.send('pulse')
        self.listener.activate(source=source)
        self.send('muted', self.listener.muted)
        if (self.controller.windows_desktop.lower() != 'default'
                or not self.controller.microphone_ready and source in ('orb', 'menu')):
            self.show_panel()

    def toggle_jarvis(self):
        if self.closing:
            return
        try:
            if self.controller.breaks.active:
                self.controller.resume()
                self.ui_hidden = False
            else:
                self.ui_hidden = not self.ui_hidden
            self.sync_break_visibility()
            diagnostic('insert_toggled', ui_visible=not self.ui_hidden)
            if not self.ui_hidden:
                self.activate(source='insert')
        except OSError:
            self.notice('Jarvis could not end the break. Try Insert again.', 'error')

    def show_panel(self, focus=True):
        self.ui_hidden = False
        self.sync_break_visibility()
        self.send('open', {'window': 'panel', 'focus': focus})

    def submit(self, text):
        if not text.strip():
            return
        self.listener.listening_until = 0
        target = self.controller.interrupt if self.controller.busy.locked() else self.controller.submit
        threading.Thread(target=target, args=(text,), daemon=True).start()

    def publish_conversation(self):
        del self.conversation[:-HISTORY]
        self.send('conversation', self.conversation)

    def notice(self, text, tone='info'):
        latest = self.conversation[-1] if self.conversation else None
        if latest and latest['kind'] == 'exchange' and latest['pending']:
            latest.update(answer=text, pending=False, tone=tone)
        else:
            self.conversation.append({'id': uuid.uuid4().hex[:12], 'kind': 'notice', 'text': text,
                                      'tone': tone, 'time': time.time()})
        self.publish_conversation()

    def state_payload(self, state=None, note=None):
        state = state or self.controller.state
        return {'state': state, 'name': STATE_NAMES.get(state, str(state).title()),
                'note': self.controller.note if note is None else note}

    def model_used_text(self):
        model = self.controller.last_model
        return (model.removeprefix('openai/').removeprefix('anthropic/') + ' · ' + str(self.controller.last_reasoning or 'default') + ' reasoning') if model else 'Automatic model choice'

    def research_note(self, rows):
        active = [row for row in rows if row['status'] in ('queued', 'running')]
        if active:
            return f"{len(active)} background task{'s' if len(active) != 1 else ''}. {active[0].get('note', '')}".strip()
        if rows:
            return rows[0].get('note', '') + '. View activity'
        return 'Activity'

    def jobs_payload(self, changed=None):
        rows = self.controller.activity_list()
        self.search_jobs = {row['id']: row for row in reversed(rows)}
        return {'rows': plain(rows), 'busy': any(row['status'] in ('queued', 'running') for row in rows),
                'note': self.research_note(rows), 'changed': changed}

    def refresh_jobs(self, changed=None):
        self.send('jobs', self.jobs_payload(changed))

    def screen_payload(self):
        selected = self.settings.get('monitor')
        return {'screens': self.screens, 'selected': selected,
                'preferred': preferred_monitor(self.screens, selected)['device'] if self.screens else None}

    def ui_settings(self):
        return {'showTaskPopups': self.settings.get('showTaskPopups', False) is True, 'monitor': self.settings.get('monitor')}

    def on_monitor_alert(self, row):
        if not self.ui_hidden:
            self.send('alert', row)

    def orb_request(self, method, params):
        try:
            return self.ui.request(method, params, timeout=3)
        except RuntimeError as exc:
            diagnostic('lock_in_orb_failed', action=method, error_type=type(exc).__name__)
            return None

    def on_lock_in(self, row):
        self.lock_in_visit = row['visit']
        if not self.ui or self.windows_hidden:
            return
        payload = {'site': row['site'], 'name': row['name'], 'text': row['text']}
        if row.get('learning'):
            payload['learning'] = True
        if row.get('cooldown'):
            payload['cooldown'] = True
        if 'attempt' in row:
            payload['attempt'] = row['attempt']
        self.send('lockin', payload)
        self.orb_request('visit_orb', {'rect': row['rect'], 'holdMs': 20000})

    def on_lock_in_finished(self, visit):
        if self.ui and visit == getattr(self, 'lock_in_visit', None):
            self.lock_in_visit = None
            self.orb_request('return_orb', {})

    def on_welcome(self, row):
        # Shares the current-visit marker with lock-in, so whichever visit came last decides when he flies home.
        self.lock_in_visit = ('welcome', row['visit'])
        if not self.ui or self.windows_hidden:
            return
        self.send('lockin', {'site': 'welcome', 'name': 'Jarvis', 'text': row['text'], 'welcome': True, 'title': row['title']})
        self.orb_request('visit_orb', {'rect': row['rect'], 'holdMs': WELCOME_HOLD_MS, 'scale': WELCOME_SCALE})

    def on_welcome_finished(self, visit):
        self.on_lock_in_finished(('welcome', visit))

    def on_agent_pointer(self, row):
        # Jarvis's own pointer during a PC task: a point in physical screen pixels and what he does there, or hide.
        if self.ui and (row.get('hide') or not self.windows_hidden):
            self.send('agent_pointer', row)

    def on_reminder_due(self, row):
        self.send('reminder', row)

    def on_reminders_changed(self):
        self.send('reminders_changed')

    def on_files_found(self, result):
        self.send('files', result)
        # The console only opens by itself when the owner turned task popups on; the results wait there otherwise.
        if self.settings.get('showTaskPopups', False) is True and not self.ui_hidden:
            self.send('open', {'window': 'console', 'view': 'files', 'focus': False})

    def on_browser_status(self, text):
        self.browser_note = text
        self.send('browser', text)

    def on_model_used(self, *_details):
        self.send('model', self.model_used_text())

    def on_screen_view(self, frame):
        self.send('snapshot', {'image': frame.get('image'), 'width': frame.get('width'),
                               'height': frame.get('height'), 'captured': time.time()})

    def on_command(self, text):
        self.submit(text)

    def on_interruption(self, text, context=None):
        threading.Thread(target=self.controller.interrupt, args=(text, context), daemon=True).start()

    def on_question(self, text):
        for entry in self.conversation:
            if entry.get('pending'):
                entry['pending'] = False
        self.conversation.append({'id': uuid.uuid4().hex[:12], 'kind': 'exchange', 'question': text, 'answer': '',
                                  'sources': [], 'pending': True, 'time': time.time()})
        self.publish_conversation()

    def on_answer(self, text, sources):
        entry = next((row for row in reversed(self.conversation) if row['kind'] == 'exchange'), None)
        if entry is None:
            entry = {'id': uuid.uuid4().hex[:12], 'kind': 'exchange', 'question': '', 'time': time.time()}
            self.conversation.append(entry)
        entry.update(answer=text, sources=safe_sources(sources), pending=False)
        self.publish_conversation()

    def on_task_completed(self, row):
        self.conversation.append({'id': uuid.uuid4().hex[:12], 'kind': 'result', 'question': row.get('question', ''),
                                  'answer': row.get('summary', ''), 'status': row.get('status', 'completed'),
                                  'sources': safe_sources(row.get('sources')), 'time': time.time()})
        self.publish_conversation()

    def on_state(self, state, note=''):
        self.send('state', self.state_payload(state, note))

    def on_muted(self, muted):
        self.settings['muted'] = muted
        self.save_settings()
        self.send('muted', muted)

    def on_email_status(self, text):
        self.send('email_status', text)

    def on_email_settings(self):
        self.send('open', {'window': 'settings', 'view': 'email', 'focus': True})

    def on_model_settings(self):
        self.send('open', {'window': 'settings', 'view': 'models', 'focus': True})

    def on_models_changed(self):
        self.send('models_changed')

    def on_preferences_changed(self, values):
        self.send('preferences', values)

    def on_projects_changed(self):
        self.send('projects_changed')

    def on_research(self, row):
        job_id = row['id']
        if job_id not in self.announced_jobs:
            self.announced_jobs.add(job_id)
            if self.settings.get('showTaskPopups', False) is True and not self.ui_hidden:
                self.send('open', {'window': 'console', 'view': 'activity', 'focus': False,
                                   'select': job_id, 'automatic': True})
        self.refresh_jobs(job_id)

    def on_speech_error(self, text):
        self.controller.update('error', 'Speech transcription unavailable')
        self.notice(text, 'error')
        if not self.ui_hidden:
            self.show_panel(focus=False)

    def on_notice(self, text):
        self.notice(text)

    def on_restart_listener(self):
        self.listener.stopped.set()
        def restart():
            thread = getattr(self, 'listener_thread', None)
            if thread:
                thread.join(timeout=2)
            listener = Listener(self.controller, self.settings)
            self.voice.tap(listener)
            listener.hide(self.windows_hidden)
            self.listener = self.controller.listener = listener
            self.listener_thread = threading.Thread(target=listener.run, daemon=True)
            self.listener_thread.start()
            self.preload_speech()
        threading.Thread(target=restart, daemon=True).start()

    def on_show(self):
        self.ui_hidden = False
        self.sync_break_visibility()
        self.send('open', {'window': 'orb'})
        self.activate(source='launch')

    def on_quit(self):
        self.quit()

    def call_snapshot(self):
        return {'state': self.state_payload(), 'conversation': self.conversation, 'greeting': GREETING,
                'model': self.model_used_text(), 'jobs': self.jobs_payload(), 'progress': self.progress,
                'hidden': self.windows_hidden, 'muted': self.listener.muted, 'startup': startup_enabled(),
                'screens': self.screen_payload(), 'settings': self.ui_settings(),
                'emailEnabled': email_policy.enabled(), 'browserNote': self.browser_note,
                'desktop': self.controller.windows_desktop, 'microphoneReady': self.controller.microphone_ready}

    def call_activate(self, source='orb'):
        self.activate(source='menu' if source == 'menu' else 'orb')

    def call_toggle_jarvis(self):
        self.toggle_jarvis()

    def call_submit(self, text):
        text = str(text).strip()
        if not 1 <= len(text) <= 4000:
            raise ValueError('Enter a command between 1 and 4000 characters')
        self.submit(text)

    def call_cancel(self):
        threading.Thread(target=self.controller.cancel, daemon=True).start()

    def call_set_muted(self, muted):
        self.listener.mute(bool(muted))
        self.settings['muted'] = self.listener.muted
        self.save_settings()
        self.send('muted', self.listener.muted)
        return {'muted': self.listener.muted}

    def call_set_startup(self, enabled):
        try:
            set_startup(bool(enabled))
        except OSError:
            raise RuntimeError(STARTUP_BLOCKED) from None
        enabled = startup_enabled()
        self.send('startup', enabled)
        return {'enabled': enabled}

    def call_set_setting(self, key, value):
        if key != 'showTaskPopups':
            raise ValueError('Unknown setting')
        self.settings[key] = bool(value)
        self.save_settings()
        self.send('settings', self.ui_settings())
        return self.ui_settings()

    def call_screens(self):
        self.screens = physical_monitors()
        return self.screen_payload()

    def on_interface_tool(self, name, arguments, result, cancelled, deadline):
        try:
            if cancelled.is_set() or time.monotonic() >= deadline or self.controller.breaks.active:
                raise RuntimeError('The interface request has ended')
            if not self.ui:
                raise RuntimeError('The Jarvis interface is unavailable')
            self.screens = physical_monitors()
            payload = self.screen_payload()
            state = self.ui.request('inspect_interface', {'screens': payload},
                                    timeout=max(0, min(5, deadline - time.monotonic())))
            if name == 'move_jarvis_orb':
                if cancelled.is_set() or time.monotonic() >= deadline:
                    raise RuntimeError('The interface request has ended')
                display = arguments.get('display')
                if not isinstance(display, str) or not display.strip() or display.strip().casefold() == 'all':
                    raise ValueError('Choose one connected monitor for the orb')
                current = next((screen for screen in self.screens if screen['device'] == state['orb']['monitor']), None)
                if current is None:
                    raise RuntimeError('The orb monitor is no longer connected')
                target = select_display(display, self.screens, current['rect'])
                payload.update(selected=target['device'], preferred=target['device'])
                state = self.ui.request('move_orb', {'screens': payload},
                                        timeout=max(0, min(5, deadline - time.monotonic())))
                if state.get('verified') is not True or state['orb']['monitor'] != target['device']:
                    raise RuntimeError('The orb placement could not be verified')
                self.settings['monitor'] = target['device']
                self.save_settings()
            result.put({'ok': True, **state, 'monitors': display_inventory(self.screens),
                        'selected': self.settings.get('monitor')})
        except Exception as exc:
            result.put({'ok': False, 'error': str(exc)[:300] or type(exc).__name__})

    def call_select_screen(self, device=None):
        self.screens = physical_monitors()
        if device is not None and not any(screen['device'] == device for screen in self.screens):
            raise ValueError('That screen is no longer connected')
        self.settings['monitor'] = device
        self.save_settings()
        payload = self.screen_payload()
        self.send('screens', payload)
        return payload

    def call_open(self, target):
        if target == 'logs':
            diagnostic('logs_opened', source='menu')
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            os.startfile(LOG_PATH.parent)
        elif target == 'email_help':
            os.startfile(str(ROOT / 'docs/email-setup.md'))
        elif target == 'microphone_privacy':
            os.startfile('ms-settings:privacy-microphone')
        else:
            raise ValueError('Unknown destination')

    def call_open_url(self, url):
        os.startfile(validate_url(str(url)))

    def call_quit(self):
        self.quit()

    def call_jobs(self):
        return self.jobs_payload()

    def call_job(self, job):
        status = self.controller.activity_status(job)
        if status.get('ok') and status.get('scope') == 'computer':
            frame = self.controller.activity_frame(job)
            status['frameCaptured'] = frame.get('captured') if frame else None
        return status

    def call_job_frame(self, job):
        frame = self.controller.activity_frame(job)
        if not frame:
            return None
        return {key: frame.get(key) for key in ('image', 'width', 'height', 'captured')}

    def call_job_cancel(self, job):
        return self.controller.activity_cancel(job)

    def call_job_read(self, job):
        threading.Thread(target=self.controller.respond, args=('Read search results', True, job), daemon=True).start()

    def call_job_save(self, job, title):
        status = self.controller.activity_status(job)
        if status.get('status') != 'completed':
            raise ValueError('Select a completed task first.')
        saved = self.controller.projects.save(title, job=status)
        self.send('projects_changed')
        return saved

    def call_projects(self):
        return self.controller.projects.list()

    def call_project(self, project):
        return self.controller.projects.read(project)

    def call_project_save(self, project, title, findings, decisions, next_steps, revision):
        saved = self.controller.projects.save(title, findings, decisions, next_steps, project, revision)
        self.send('projects_changed')
        return saved

    def call_project_resume(self, project):
        row = self.controller.projects.read(project)
        self.submit('Resume saved project ' + row['title'] + ' with ID ' + row['id'])

    def call_reminders(self):
        return self.controller.reminders.list()

    def call_reminder_change(self, action, reminder):
        if action not in ('snooze', 'dismiss', 'cancel'):
            raise ValueError('Choose snooze, dismiss or cancel')
        result = self.controller.reminders.change(action, reminder)
        if not result.get('ok'):
            raise ValueError(result.get('error', 'The reminder could not be changed'))
        self.send('reminders_changed')
        return result

    def call_reminder_new(self, text):
        text = ' '.join(str(text).split())
        if not text:
            raise ValueError('Say what to remind you about, and when.')
        self.submit('Remind me ' + text)

    def call_alerts(self):
        return self.controller.personal.monitor.status()

    def call_alerts_dismiss(self):
        return self.controller.personal.monitor.dismiss()

    def call_preferences(self):
        return self.controller.preferences.read()

    def call_preferences_save(self, values):
        if not isinstance(values, dict):
            raise ValueError('Settings must be a set of named values')
        self.controller.preferences.save(values)
        if {'briefingTime', 'weatherUnits', 'fileRoots'} & set(values):
            self.controller.next_briefing_check = 0
        if {'memoryVault', 'fileWriteRoots', 'monitorEnabled', 'monitorBattery', 'monitorFolders'} & set(values):
            self.controller.personal.memory.setup()
        return self.controller.preferences.read()

    def call_briefing(self):
        self.submit('Give me my morning briefing')

    def call_vault_open(self):
        self.controller.personal.memory.setup()
        from desktop.obsidian import open_vault
        open_vault(self.controller.personal.memory.vault)

    def call_voice(self):
        import sounddevice as sd
        from desktop.speech import MODELS, speech_status
        from desktop.offline_speech import ready
        devices = [{'index': None, 'name': 'Windows default microphone'}]
        devices += [{'index': row['index'], 'name': row['name']} for row in sd.query_devices() if row['max_input_channels'] > 0]
        model = self.settings.get('whisperModel', MODELS[0])
        status = speech_status(model)
        if status['configured'] and status['provider'] == 'Local Whisper':
            info = f"Local {status['offlineModel']} handles speech on this PC." + (' Groq is the backup.' if status['mode'] == 'auto' else '')
        elif status['configured']:
            info = 'Groq Whisper uses your VibeCode speech key.'
        else:
            info = status.get('error') or 'Speech recognition is not configured.'
        return {'devices': devices, 'device': self.settings.get('microphoneDevice'), 'models': list(MODELS),
                'model': model, 'status': info, 'configured': bool(status['configured']), 'offlineInstalled': ready(),
                'mode': self.controller.preferences.read().get('offlineSpeech', 'auto'),
                'microphoneReady': self.controller.microphone_ready, 'microphone': self.controller.microphone_name}

    def call_voice_meter(self, on=True):
        self.meter_until = time.monotonic() + METER_LEASE if on else 0.0

    def call_voice_save(self, device, model, mode):
        from desktop.speech import MODELS
        if model not in MODELS:
            raise ValueError('Choose a listed speech model')
        if mode not in ('auto', 'offline', 'online'):
            raise ValueError('Choose automatic, offline or online speech')
        if device is not None and not isinstance(device, int):
            raise ValueError('Choose a listed microphone')
        self.controller.preferences.save({'offlineSpeech': mode})
        self.settings['microphoneDevice'] = device
        self.settings['whisperModel'] = model
        self.save_settings()
        self.events.put(('restart_listener',))
        return self.call_voice()

    def call_models(self):
        try:
            return local_request(18791, '/models', timeout=5)
        except Exception:
            raise RuntimeError('Could not reach the model service. Start Jarvis and try again.') from None

    def call_models_backup_test(self):
        try:
            return local_request(18791, '/backup/test', {}, timeout=30)
        except Exception:
            raise RuntimeError('Could not reach the model service. Start Jarvis and try again.') from None

    def call_models_save(self, conversationModel, searchModel, claudeBackup=None):
        try:
            settings = {'conversationModel': conversationModel, 'searchModel': searchModel}
            if isinstance(claudeBackup, bool):
                settings['claudeBackup'] = claudeBackup
            return local_request(18791, '/models', settings, timeout=5)
        except Exception:
            raise RuntimeError('The model choice was not saved. Check the service and try again.') from None

    def email_accounts_text(self):
        status = self.controller.mail.accounts()
        rows = [entry['provider'].title() + ': ' + (entry.get('address') or 'Connected') if entry['connected']
                else entry['provider'].title() + ': not connected directly' for entry in status['accounts']]
        addresses = [row['address'] for row in status.get('classic', {}).get('accounts', [])]
        rows.append('Outlook classic: ' + (', '.join(addresses) if addresses else 'no connected mailboxes found'))
        return '\n'.join(rows)

    def call_email(self):
        if not email_policy.enabled():
            return {'enabled': False, 'message': email_policy.UNAVAILABLE + ' Email is off for now.'}
        try:
            return {'enabled': True, 'status': self.email_accounts_text()}
        except Exception:
            return {'enabled': True, 'status': 'Could not check accounts. Finish sign-in and refresh.'}

    def call_email_connect(self, provider, clientFile=None, clientId=None):
        if provider not in ('gmail', 'outlook'):
            raise ValueError('Unknown account')
        if not email_policy.enabled():
            raise ValueError(email_policy.UNAVAILABLE)
        if not secret_path(provider + '-client').exists():
            if provider == 'gmail':
                if not clientFile:
                    return {'needs': 'clientFile'}
                mail.configure_gmail(clientFile)
            else:
                if not clientId:
                    return {'needs': 'clientId'}
                mail.configure_outlook(clientId)
        def work():
            try:
                mail.connect(provider)
                self.events.put(('email_status', self.email_accounts_text()))
            except Exception as exc:
                self.events.put(('email_status', str(exc)))
        threading.Thread(target=work, daemon=True).start()
        return {'status': 'Finish signing in in your browser.'}

    def call_browser_handoff(self):
        result = self.controller.browser.execute('handoff')
        message = result.get('message') or result.get('error', 'Browser unavailable')
        self.events.put(('browser_status', message))
        return {'message': message}

    def call_file_summarize(self, file, name):
        self.submit('Summarize the file with reference ' + str(file) + ', named ' + str(name))

    def quit(self):
        if self.closing:
            return
        self.closing = True
        diagnostic('desktop_shutdown', source='quit')
        self.hotkey.stop()
        self.listener.stopped.set()
        self.controller.closed.set()
        self.controller.tasks.close()
        self.controller.browser.close()
        self.controller.research.close()
        threading.Thread(target=self.controller.cancel, daemon=True).start()
        if self.controller.server:
            threading.Thread(target=self.controller.server.shutdown, daemon=True).start()
        if self.ui:
            self.ui.stop()


def main():
    try:
        existing = local_request(18792, '/health', timeout=1)
        if existing.get('service') == 'jarvis-desktop':
            if existing.get('windowsDesktop', '').lower() == 'default' and desktop_name().lower() != 'default':
                return
            if existing.get('windowsDesktop') == desktop_name():
                local_request(18792, '/show', {}, timeout=1)
                return
            local_request(18792, '/shutdown', {}, timeout=2)
            time.sleep(1)
    except Exception:
        pass
    Orb().run()


if __name__ == '__main__':
    main()
