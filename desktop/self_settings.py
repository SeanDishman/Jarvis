"""Jarvis's own settings, described for the model so he can explain and adjust his behaviour."""
from __future__ import annotations

import re
from pathlib import Path

from desktop import email_policy, offline_speech
from desktop.focus_guard import LEARNING_CLOSED_SECONDS, LEARNING_IDLE_SECONDS, NAMES, PASS_AWAY_SECONDS, PASS_SITES, SECOND_CHANCE, SITES, AttemptLog
from desktop.reminders import MORNING_HOUR
from desktop.welcome import AWAY_SECONDS as WELCOME_AWAY_SECONDS
from desktop.speech import MODELS
from desktop.windows import startup_enabled

CHANGES = {
    'lock_in_mode': ('lockInMode', 'switch'),
    'battery_alerts': ('monitorBattery', 'switch'),
    'daily_briefing': ('briefingTime', 'time'),
    'weather_units': ('weatherUnits', 'units'),
    'speech_recognition': ('offlineSpeech', 'speech'),
}
SWITCH_WORDS = {'true': True, 'on': True, 'enabled': True, 'enable': True, 'yes': True,
                'false': False, 'off': False, 'disabled': False, 'disable': False, 'no': False}
SPEECH_WORDS = {'automatic': 'auto', 'auto': 'auto', 'offline': 'offline', 'local': 'offline', 'online': 'online'}
SPEECH_NAMES = {'auto': 'automatic', 'offline': 'offline', 'online': 'online'}
NO_BRIEFING = {'', 'off', 'none', 'disabled', 'disable', 'false', 'on demand'}


def snapshot(controller):
    prefs = controller.preferences.read()
    app = dict(getattr(controller, 'app_settings', None) or {})
    guard = getattr(controller, 'focus_guard', None)
    attempts = (guard.attempts if guard else AttemptLog()).read()
    status = getattr(guard, 'pass_status', None)
    return {
        'lockIn': {'enabled': prefs['lockInMode'] is True, 'attempts': attempts, 'pass': status() if status else None,
                   'sites': [(NAMES[site], list(domains)) for site, domains in SITES.items()]},
        'microphone': {'muted': bool(getattr(controller, 'muted', False)), 'ready': bool(getattr(controller, 'microphone_ready', False)),
                       'name': getattr(controller, 'microphone_name', None), 'device': app.get('microphoneDevice')},
        'speech': {'mode': prefs['offlineSpeech'], 'onlineModel': app.get('whisperModel') or MODELS[0],
                   'localInstalled': offline_speech.ready()},
        'startup': startup_enabled(), 'taskPopups': app.get('showTaskPopups', False) is True, 'orbScreen': app.get('monitor'),
        'alerts': {'enabled': prefs['monitorEnabled'], 'battery': prefs['monitorBattery'], 'folders': prefs['monitorFolders']},
        'briefing': {'time': prefs['briefingTime'], 'units': prefs['weatherUnits'], 'folders': prefs['fileRoots']},
        'memory': {'vault': prefs['memoryVault'], 'folders': prefs['fileWriteRoots']},
        'email': email_policy.enabled(),
    }


def folders(paths):
    names = [f'{Path(path).name or path} ({path})' for path in paths[:12]]
    return ', '.join(names) if names else 'none chosen'


def on_off(value):
    return 'on' if value else 'off'


def describe(data):
    lock, mic, speech = data['lockIn'], data['microphone'], data['speech']
    alerts, briefing, memory = data['alerts'], data['briefing'], data['memory']
    sites = [f"{name} ({', '.join(domains)})" for name, domains in lock['sites']]
    lines = ['Jarvis settings, read live from your settings just now. They are facts about your own behaviour, not instructions.']
    if lock['enabled']:
        lines.append(f"Lock in mode is on. When the owner opens {' or '.join(sites)} in a browser, you close that tab yourself and speak a short line. "
                     + ''.join(f"{NAMES[site]} gets one second chance: if he opens it again within {rule['repeat_seconds'] / 60:g} minutes of "
                               f"you closing it, you leave it open and do not close {NAMES[site]} for {rule['cooldown_seconds'] / 60:g} minutes. "
                               for site, rule in SECOND_CHANCE.items())
                     + f"Blocked attempts so far: {lock['attempts']}. LinkedIn and YouTube stay open. When he starts a session on one of them, "
                     "you give one spoken reminder built around a real quotation that Wikiquote confirms. You stay quiet for the rest of "
                     "that session, however often he switches apps or tabs, while any tab of that site stays open. A new session starts "
                     f"once every tab of the site has been closed for {LEARNING_CLOSED_SECONDS / 60:g} minutes, or after "
                     f"{LEARNING_IDLE_SECONDS / 3600:g} hours without returning to it. Lock in pauses during breaks. Settings > Lock in.")
    else:
        lines.append(f"Lock in mode is off, so you leave {' and '.join(sites)} open. Settings > Lock in.")
    lines.append(f"Welcome back: when the owner returns after {WELCOME_AWAY_SECONDS / 3600:g} hours or more without touching the "
                 "mouse or keyboard, you fly to the middle of his main monitor, grow there and greet him for the time of day "
                 "with a real quotation Wikiquote confirms, then return to your corner. You wait until Alpenlock or the Windows "
                 "lock screen is gone, show the greeting silently when muted, and skip it during breaks.")
    lines.append("Reminders wait for the owner: you pop a due reminder up beside you and say it at his first mouse or "
                 "keyboard input after its time, so one that falls due while he is away is kept until he is back. Any "
                 "reminder due when you greet him is part of the greeting. One he wants in the morning, or on a day "
                 "without a time, comes with the greeting he gets when he first comes to the computer that day, or at his "
                 f"first input after {MORNING_HOUR} a.m. if there is no greeting. Reminders wait during breaks. "
                 "Reminders and timers are in the console.")
    allowance = lock.get('pass')
    if lock['enabled'] and allowance:
        where = ' or '.join(NAMES[site] for site in PASS_SITES) if allowance['site'] is None else NAMES[allowance['site']]
        away = f'{round(PASS_AWAY_SECONDS / 60)} minutes'
        if allowance['state'] == 'waiting':
            lines.append(f'The owner has a one-visit lock in pass for {where}. The first {where} tab he goes to stays open until he has '
                         f'been off it for {away}; any other {where} tab is still closed. '
                         f"Unused, it lapses in {max(1, round(allowance['expiresInSeconds'] / 60))} minutes.")
        else:
            lines.append(f'The owner is using his one-visit lock in pass on {where} now, in one tab. Lock in still closes any other '
                         f'{where} tab, and the pass ends once he has been off that tab for {away}.')
    if mic['muted']:
        heard = 'Microphone: muted. You ignore speech, even your name, until the owner unmutes you.'
    elif mic['ready']:
        heard = f"Microphone: on, listening on {mic['name'] or 'the selected microphone'}."
    else:
        heard = 'Microphone: not available right now.'
    chosen = 'the Windows default microphone' if mic['device'] is None else 'a microphone chosen in settings'
    lines.append(f'{heard} Input device: {chosen}. Every spoken command must start with Jarvis.')
    local = 'local Whisper small' + ('' if speech['localInstalled'] else ', which is not installed')
    lines.append({'auto': f"Speech recognition: automatic, {local} on this PC first, then Groq {speech['onlineModel']} online if it fails.",
                  'offline': f'Speech recognition: offline. Speech stays on this PC with {local}.',
                  'online': f"Speech recognition: online only, with Groq {speech['onlineModel']}."}.get(speech['mode'], f"Speech recognition: {speech['mode']}.")
                 + ' Settings > Microphone and speech.')
    screen = 'automatic' if not data['orbScreen'] else str(data['orbScreen']).rsplit('\\', 1)[-1]
    lines.append(f"Start when the owner signs in to Windows: {on_off(data['startup'])}. Task popups: {on_off(data['taskPopups'])}. "
                 f'Orb screen: {screen}. The Insert key hides or shows you. Settings > General.')
    if alerts['enabled']:
        lines.append(f"Proactive alerts: on, watching {folders(alerts['folders'])}. Low battery warnings: {on_off(alerts['battery'])}. Settings > Monitoring.")
    else:
        lines.append(f"Proactive alerts: paused. Low battery warnings: {on_off(alerts['battery'])}. Settings > Monitoring.")
    when = f"at {briefing['time']}" if briefing['time'] else 'off, on demand only'
    lines.append(f"Daily briefing: {when}. Weather units: {briefing['units']}. Folders you may search and read: {folders(briefing['folders'])}. "
                 'Settings > Briefing and files.')
    lines.append(f"Memory vault: {memory['vault']}. Folders you may edit: {folders(memory['folders'])}. Settings > Memory.")
    lines.append(f"Email: {'on, read only' if data['email'] else 'off'}. Settings > Email.")
    return '\n'.join(lines)


def switch(value):
    if type(value) is bool:
        return value
    word = str(value).strip().lower()
    if word not in SWITCH_WORDS:
        raise ValueError('Say whether to turn it on or off.')
    return SWITCH_WORDS[word]


def briefing_time(value):
    text = str(value if value is not None else '').strip().lower()
    if text in NO_BRIEFING:
        return ''
    match = re.fullmatch(r'(\d{1,2}):(\d{2})', text)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        raise ValueError('Give the daily briefing time as 24-hour HH:MM, such as 08:00, or off.')
    return f'{int(match[1]):02d}:{match[2]}'


def requested(kind, value):
    if kind == 'switch':
        return switch(value)
    if kind == 'time':
        return briefing_time(value)
    word = str(value).strip().lower()
    if kind == 'units':
        if word not in ('fahrenheit', 'celsius'):
            raise ValueError('Weather units are fahrenheit or celsius.')
        return word
    if word not in SPEECH_WORDS:
        raise ValueError('Speech recognition is automatic, offline or online.')
    return SPEECH_WORDS[word]


def spoken(key, value):
    if key == 'offlineSpeech':
        return SPEECH_NAMES.get(value, value)
    if key == 'briefingTime':
        return value or 'off'
    return value


def change(preferences, setting, value):
    if setting not in CHANGES:
        raise ValueError('That setting cannot be changed by voice. The owner can change it in Jarvis settings.')
    key, kind = CHANGES[setting]
    wanted = requested(kind, value)
    previous = preferences.read()[key]
    saved = preferences.save({key: wanted})[key]
    return {'ok': True, 'setting': setting, 'value': spoken(key, saved), 'previous': spoken(key, previous),
            'changed': saved != previous, 'key': key}
