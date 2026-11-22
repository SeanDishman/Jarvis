from __future__ import annotations

import audioop
from collections import deque
import json
import math
import queue
import re
import threading
import time

from desktop.common import ROOT
from desktop.windows import normalized
from desktop.interruptions import NAME, sounds_like_echo
from desktop import offline_speech
from desktop.speech import transcribe_pcm
from desktop.diagnostics import event as diagnostic
from desktop.computer import stop_requested
from desktop.turn_timing import IdleSpeechPause, MAX_AUDIO_SECONDS, configure_endpoint, contains_speech

NAMES = r'(?:jarvis|javis|jar viss|jar ves)'
# Whisper's spellings of a spoken Jarvis, trusted only once the decoders have already heard the name.
HEARD_NAMES = r'(?:jarvis|javis|jar viss|jar ves|jervis|jarvas|jarvus|jarves|charvis|chavis|chavez|travis|garvis|harvis|jarbis)'
# Words people say before a name without changing who they are talking to: yo, um, Jarvis.
FILLERS = (r"(?:hey there|good (?:morning|afternoon|evening)|what['’]?s up|hey|hi|hello|yo|ok|okay|so|um+|uh+|uhm|erm?|ah+|"
           r"oh|well|and|alright|all right|right|listen|please|excuse me|sorry)")
FILLER_LEAD = r'(?:' + FILLERS + r'[,.!?\s]+){0,3}'
GREETING = FILLER_LEAD + r"(?:[a-z'’]+[,\s]+)?"
WAKE = re.compile('^' + GREETING + NAMES + r'\b[\s,.:!?-]*', re.I)
HEARD_NAME = re.compile('^' + GREETING + HEARD_NAMES + r'\b[\s,.:!?-]*', re.I)
INTERRUPTION_WAKE_GRAMMAR = json.dumps(['jarvis', 'hey jarvis', '[unk]'])
WAKE_PHRASES = ('jarvis', 'hey jarvis', 'okay jarvis')
INTERRUPTION_PREROLL_SECONDS = 2
CONTINUATION_CONFIRM_SECONDS = .6
# A lone name this soon after a phrase answers that phrase: what's the weather like ... Jarvis?
PREVIOUS_PHRASE_SECONDS = 15
TRAILING_NAME_GAP_SECONDS = 2.0

NAME_CALL = r'(?:(?:hey|okay|ok)[,\s]+)?(?:jarvis|javis)\b'
REPEATED_NAME = re.compile('^' + NAME_CALL + r'[\s,.:!?-]*', re.I)
REPEATED_CALL = re.compile(r'\b(?:jarvis|javis)\b(?:[\s,.:!?-]+' + NAME_CALL + r')+[\s,.:!?-]*', re.I)


def closing_name(text, names):
    """What was asked when the name closes it: what's the weather, Jarvis?

    Only Whisper's punctuation tells that apart from we were talking about Jarvis: it sets a name someone is
    speaking to apart with a comma. The local decoder writes no punctuation, so there a closing name is only a
    candidate for Whisper to confirm."""
    if not re.search(r'[,.!?]', text):
        return None
    match = re.search(r',\s*' + names + r'\b\s*(?P<stop>[.!?]+|$)', text, re.I)
    if not match:
        match = re.search(r'\s+' + names + r'\b\s*(?P<stop>\?+)', text, re.I)
    if not match or not re.search(r'\w', text[:match.start()]):
        return None
    return (text[:match.start()].rstrip(' ,') + match.group('stop') + text[match.end():]).strip()


def split_wake(text, heard=False, allow_repeated=False):
    """Whether the speech calls Jarvis by name, and what it asks. The name may open the speech after a few filler
    words, open a later sentence, sit between commas, or close what was asked. A mention such as we talked about
    Jarvis at lunch does not count."""
    text = text.strip()
    names = HEARD_NAMES if heard else NAMES
    match = (HEARD_NAME if heard else WAKE).match(text)
    if not match and allow_repeated:
        match = REPEATED_CALL.search(text)
    if not match:
        # The report is done. Jarvis, what time is it?
        match = re.search(r'[.!?]\s+' + FILLER_LEAD + names + r'\b[\s,.:!?-]*', text, re.I)
    if match:
        return True, text[match.end():].strip()
    # Can you, Jarvis, open Spotify?
    middle = re.search(r',\s*' + names + r'\b\s*,\s*', text, re.I)
    if middle and re.search(r'\w', text[:middle.start()]):
        return True, (text[:middle.start()] + ' ' + text[middle.end():]).strip()
    command = closing_name(text, names)
    return (True, command) if command else (False, '')


def without_repeated_names(command):
    # Someone who is not answered at once says the name again: Jarvis. Jarvis, open Spotify.
    while (match := REPEATED_NAME.match(command)):
        command = command[match.end():]
    return command.strip()


def wake_in_grammar(app_text):
    # The general model often turns a spoken Jarvis into "job as" or "jar". The launch grammar knows the
    # name, so it still hears it, wherever it falls in what was said.
    return 'jarvis' in str(app_text or '').split()


def wake_candidate_source(text, app_text):
    if REPEATED_CALL.search(text) or REPEATED_CALL.search(app_text):
        return 'repeated_call'
    if wake_in_grammar(app_text):
        return 'wake_grammar'
    # The name somewhere later, as in what's the weather like jarvis, or a name the decoder spelled its own way.
    if re.search(r'\b' + HEARD_NAMES + r'\b', str(text or ''), re.I):
        return 'name_heard'
    return None


def discard_idle_phrase(recognizer, app_recognizer, partial, utterance, discarded=None):
    if split_wake(partial)[0]:
        return False
    grammar_partial = json.loads(app_recognizer.PartialResult()).get('partial', '')
    if wake_candidate_source(partial, grammar_partial):
        return False

    # A quiet or short name may only resolve when the decoders finalize the phrase.
    text = json.loads(recognizer.FinalResult()).get('text', '')
    app_text = json.loads(app_recognizer.FinalResult()).get('text', '')
    if split_wake(text)[0] or wake_candidate_source(text, app_text):
        # Restore the addressed audio so the normal command endpoint still governs pauses.
        pcm = bytes(utterance)
        recognizer.AcceptWaveform(pcm)
        app_recognizer.AcceptWaveform(pcm)
        return False
    diagnostic('speech_ignored', reason='idle_phrase_without_wake', word_count=len(text.split()))
    if discarded is not None:
        discarded.append(text)
    return True


def speech_onset_seconds(pcm):
    """How long the audio runs before speech starts, by WebRTC voice activity in 20 ms frames."""
    import webrtcvad
    vad, frame, voiced = webrtcvad.Vad(2), 640, 0
    for offset in range(0, len(pcm) - frame + 1, frame):
        voiced = voiced + 1 if vad.is_speech(pcm[offset:offset + frame], 16000) else 0
        if voiced >= 3:
            return (offset - 2 * frame) / 32000
    return len(pcm) / 32000


def directed_interruption(text, spoken):
    return bool(interruption_request(text, spoken))


def interruption_request(text, spoken):
    text = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', text)
    for match in NAME.finditer(text):
        if text[match.end():].strip(' ,.:!?-') and not sounds_like_echo(text[match.start():], spoken):
            return text[match.start():].strip()
    return ''


def interruption_grammar(spoken, model):
    phrases = json.loads(INTERRUPTION_WAKE_GRAMMAR)
    for sentence in re.split(r'[.!?\n]+', spoken):
        phrase = ' '.join(word for word in normalized(sentence).split() if model.vosk_model_find_word(word) >= 0)
        if phrase:
            phrases.append(phrase)
    return json.dumps(phrases)


def app_grammar(apps, model):
    names = {normalized(app['Name']) for app in apps}
    names.update(('chrome', 'edge', 'notepad', 'outlook', 'outlook classic', 'spotify', 'terminal'))
    names = {name for name in names if name and all(model.vosk_model_find_word(word) >= 0 for word in name.split())}
    phrases = ['[unk]', *WAKE_PHRASES]
    for name in sorted(names):
        for verb in ('open', 'start', 'launch'):
            for prefix in ('', 'jarvis ', 'hey jarvis ', 'please '):
                phrases.append(prefix + verb + ' ' + name)
    return json.dumps(phrases)


def prefer_app_transcript(general, app_text, listening=False):
    from desktop.controller import LAUNCH
    woke, general_command = split_wake(general)
    if not woke and not listening:
        return general
    general_command = general_command if woke else general
    _, app_command = split_wake(app_text)
    app_command = app_command or app_text
    if LAUNCH.fullmatch(general_command) and LAUNCH.fullmatch(app_command) and '[unk]' not in app_text:
        return ('jarvis ' if woke else '') + app_command
    return general


class Listener:
    def __init__(self, controller, settings):
        self.controller = controller
        self.settings = settings
        self.controller.whisper_model = settings.get('whisperModel', 'whisper-large-v3')
        self.audio = queue.Queue(maxsize=math.ceil(MAX_AUDIO_SECONDS / .08))
        self.stopped = threading.Event()
        self.listening_until = 0
        self.conversation_active = False
        self.hidden = False
        self.reset = threading.Event()
        self.muted = bool(settings.get('muted', False))
        self.transcribing = False
        self.queue_drops = 0
        self.input_overflows = 0
        self.previous_phrase = None
        self.controller.muted = self.muted

    def is_listening(self):
        return (not self.muted and not self.hidden and not self.controller.breaks.active
                and time.monotonic() < self.listening_until)

    def activate(self, seconds=None, source='manual', continuous=False, reset_audio=True):
        blocked = 'break' if self.controller.breaks.active else 'hidden' if self.hidden else None
        if not blocked and not self.controller.busy.locked():
            self.conversation_active = False
            self.muted = False
            self.controller.muted = False
            self.listening_until = 0
            if reset_audio:
                self.reset.set()
            if self.controller.microphone_ready and not self.reset.is_set():
                self.controller.update('ready', 'Say Jarvis before each command')
            else:
                self.controller.update('starting', 'Starting microphone')
            diagnostic('wake_listening_ready' if self.controller.state == 'ready' else 'wake_listening_requested', source=source)
        else:
            diagnostic('listening_not_opened', source=source, reason=blocked or 'busy')

    def activate_from_wake(self, text, heard_chars=None):
        if (self.hidden or self.muted or self.stopped.is_set() or self.controller.breaks.active
                or self.controller.busy.locked() or not split_wake(text)[0]):
            return False
        if not self.is_listening():
            self.listening_until = time.monotonic() + self.settings.get('commandTimeoutSeconds', 12)
            self.controller.update('listening', 'Listening')
            diagnostic('wake_detected', source='voice', heard_chars=len(text) if heard_chars is None else heard_chars)
        return True

    def trailing_question(self, previous):
        """The question asked just before a lone name: what's the weather like ... Jarvis?"""
        if not previous or previous[1] > TRAILING_NAME_GAP_SECONDS:
            return ''
        try:
            question = self.recognize(previous[0]).strip()
        except (RuntimeError, ValueError):
            return ''
        question = without_repeated_names(question)
        if question:
            diagnostic('speech_accepted', wake=True, source='trailing_name', heard_chars=len(question))
        return question

    def mute(self, value):
        self.muted = bool(value)
        if self.muted:
            self.conversation_active = False
        self.controller.muted = self.muted
        self.listening_until = 0
        self.reset.set()
        self.controller.speech_paused.clear()
        self.controller.update('muted' if self.muted else 'ready', 'Microphone muted' if self.muted else 'Say Jarvis')
        diagnostic('microphone_mute_changed', muted=self.muted)

    def hide(self, hidden):
        if bool(hidden) == self.hidden:
            return
        self.hidden = bool(hidden)
        self.reset.set()
        if self.hidden:
            self.conversation_active = False
            self.listening_until = 0
            self.controller.resume_interruption(self.controller.interruption_pause)
            self.controller.idle()

    def callback(self, data, _frames, _time, _status):
        if _status:
            self.input_overflows += 1
        if self.controller.breaks.active or self.muted or self.hidden:
            return
        try:
            self.audio.put_nowait(bytes(data))
        except queue.Full:
            self.queue_drops += 1
            self.reset.set()

    def recognize(self, pcm, update_state=True, *, local_only=False):
        started = time.monotonic()
        mode = self.controller.preferences.read().get('offlineSpeech','auto') if getattr(self.controller,'preferences',None) else 'online'
        if local_only:
            mode = 'offline'
        local = mode == 'offline' or (mode == 'auto' and offline_speech.ready())
        diagnostic('transcription_started', model=offline_speech.NAME if local else self.settings.get('whisperModel', 'whisper-large-v3'),
                   audio_ms=round(len(pcm) / 32))
        self.transcribing = True
        if update_state:
            self.controller.update('thinking', f'Transcribing locally with {offline_speech.LABEL}' if local else 'Transcribing your command')
        try:
            text = transcribe_pcm(pcm, self.settings.get('whisperModel', 'whisper-large-v3'), mode=mode)
            self.controller.speech_error = None
            diagnostic('transcription_finished', heard_chars=len(text), duration_ms=round((time.monotonic()-started)*1000))
            return '' if self.stopped.is_set() or self.muted or self.hidden or self.controller.breaks.active else text
        except (RuntimeError, ValueError) as error:
            diagnostic('transcription_failed', error_type=type(error).__name__, duration_ms=round((time.monotonic()-started)*1000))
            self.controller.speech_error = str(error)
            self.reset.set()
            raise
        finally:
            self.transcribing = False

    def continuation_waiting(self, pending, rate, rate_state, recognizer):
        for _ in range(self.audio.maxsize):
            try:
                pending.append(self.audio.get_nowait())
            except queue.Empty:
                break
        if not pending:
            return False
        pcm, probe_rate_state = audioop.ratecv(b''.join(pending), 2, 1, rate, 16000, rate_state)
        if not contains_speech(pcm):
            pending.clear()
            return False
        recognizer.Reset()
        deadline = time.monotonic() + CONTINUATION_CONFIRM_SECONDS
        heard = ''
        try:
            while True:
                if self.reset.is_set() or self.stopped.is_set() or self.muted or self.hidden or self.controller.breaks.active:
                    return False
                if pcm:
                    ended = recognizer.AcceptWaveform(pcm)
                    result = json.loads(recognizer.Result() if ended else recognizer.PartialResult())
                    heard = result.get('text' if ended else 'partial', '').replace('[unk]', '').strip()
                if heard:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    heard = json.loads(recognizer.FinalResult()).get('text', '').replace('[unk]', '').strip()
                    break
                try:
                    raw = self.audio.get(timeout=min(.08, remaining))
                except queue.Empty:
                    pcm = b''
                    continue
                pending.append(raw)
                pcm, probe_rate_state = audioop.ratecv(raw, 2, 1, rate, 16000, probe_rate_state)
        finally:
            recognizer.Reset()
        if heard:
            diagnostic('speech_continued_during_transcription', audio_ms=round(sum(map(len, pending)) * 1000 / (rate * 2)), word_count=len(heard.split()))
            return True
        diagnostic('continuation_noise_ignored', audio_ms=round(sum(map(len, pending)) * 1000 / (rate * 2)))
        pending.clear()
        return False

    def local_transcription(self):
        preferences = getattr(self.controller, 'preferences', None)
        mode = preferences.read().get('offlineSpeech', 'auto') if preferences else 'online'
        return mode == 'offline' or (mode == 'auto' and offline_speech.ready())

    def accepts_speech(self, text, app_text=''):
        blocked = ('hidden' if self.hidden else 'muted' if self.muted else
                   'break' if self.controller.breaks.active else 'stopped' if self.stopped.is_set() else None)
        if blocked:
            diagnostic('speech_ignored', reason=blocked)
            return False
        text = prefer_app_transcript(text, app_text)
        wake, command = split_wake(text)
        candidate = None if wake else wake_candidate_source(text, app_text)
        if candidate and self.local_transcription():
            # An uncertain first word or a repeated call buried in other speech needs local confirmation.
            diagnostic('wake_candidate', source=candidate, heard_chars=len(text), word_count=len(text.split()))
            return 'confirm_repeated' if candidate == 'repeated_call' else 'confirm'
        if not text:
            diagnostic('speech_ignored', reason='no_words')
            return False
        if len(text.strip()) == 1:
            diagnostic('speech_ignored', reason='fragment', heard_chars=1)
            return False
        if not wake:
            diagnostic('speech_ignored', reason='wake_word_required', heard_chars=len(text), word_count=len(text.split()))
            return False
        if not self.activate_from_wake(text):
            return False
        self.listening_until = 0
        if not command:
            self.controller.idle()
            self.controller.acknowledge_wake()
            return False
        diagnostic('speech_accepted', wake=True, source='wake_word', heard_chars=len(text))
        return True

    def handle_interruption(self, text, pcm, context=None):
        context = context or self.controller.interruption_context()
        spoken = context['assistantSpeech']
        queued = False
        try:
            if self.hidden or self.muted or self.stopped.is_set() or self.controller.breaks.active:
                diagnostic('interruption_ignored', reason='disabled')
                return
            if not context.get('wakeDetected') and not directed_interruption(text, spoken):
                diagnostic('interruption_ignored', reason='wake_word_required_or_echo')
                return
            if self.controller.interrupt_lock.locked():
                diagnostic('interruption_ignored', reason='decision_in_progress')
                return
            if not self.controller.pause_interruption(context, transcribing=True):
                return
            heard = self.recognize(pcm, update_state=False)
            request = interruption_request(heard, spoken)
            if request:
                diagnostic('interruption_received', heard_chars=len(request))
                self.controller.events.put(('interruption', request, context))
                queued = True
                return
            diagnostic('interruption_ignored', reason='unconfirmed_wake_or_echo', heard_chars=len(heard))
        except (RuntimeError, ValueError) as error:
            self.controller.events.put(('speech_error', str(error)))
        finally:
            if not queued:
                self.controller.resume_interruption(context)

    def run(self):
        if self.controller.windows_desktop.lower() != 'default':
            diagnostic('microphone_unavailable', reason='restricted_desktop', windows_desktop=self.controller.windows_desktop)
            self.controller.update('error', 'Run setup-jarvis.cmd from your Windows desktop to enable the microphone and orb.')
            return
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
            SetLogLevel(-1)
            model_path = ROOT / '.runtime/speech/vosk-model-small-en-us-0.15'
            delay = configure_endpoint(model_path, self.settings)
            model = Model(str(model_path))
            diagnostic('listener_configured', pause_seconds=delay, max_audio_seconds=MAX_AUDIO_SECONDS)
        except Exception as exc:
            diagnostic('listener_start_failed', error_type=type(exc).__name__)
            self.controller.update('error', 'Speech recognition could not start. Check diagnostic logs.')
            return
        while not self.stopped.is_set():
            if self.controller.breaks.active or self.hidden:
                if self.controller.breaks.active:
                    self.conversation_active = False
                self.controller.microphone_ready = False
                self.reset.set()
                self.stopped.wait(.2)
                continue
            barge_context = None
            try:
                device = self.settings.get('microphoneDevice')
                info = sd.query_devices(device, 'input')
                rate = int(info['default_samplerate'])
                recognizer = KaldiRecognizer(model, 16000)
                launch_grammar = app_grammar(self.controller.apps.apps, model)
                app_recognizer = KaldiRecognizer(model, 16000, launch_grammar)
                idle_pause = IdleSpeechPause()
                rate_state = None
                was_busy = False
                capture_answer = self.controller.cancelled
                grammar_speech = ''
                utterance = bytearray()
                pending = deque()
                held_text = held_app_text = ''
                turn_accepted = confirm_wake = False
                input_peak = 0
                next_audio_log = time.monotonic() + 10
                if self.reset.is_set():
                    self.reset.clear()
                    while True:
                        try:
                            self.audio.get_nowait()
                        except queue.Empty:
                            break
                with sd.RawInputStream(device=device, samplerate=rate, blocksize=int(rate * .08),
                                       channels=1, dtype='int16', callback=self.callback):
                    self.controller.microphone_ready = True
                    self.controller.microphone_name = info['name']
                    diagnostic('microphone_opened', device=info['name'], sample_rate=rate)
                    announce_ready = True
                    while not self.stopped.is_set():
                        if self.controller.breaks.active:
                            self.conversation_active = False
                            diagnostic('microphone_closed', reason='break')
                            break
                        if self.hidden:
                            diagnostic('microphone_closed', reason='hidden')
                            break
                        busy = self.controller.busy.locked()
                        answer = self.controller.cancelled
                        spoken = self.controller.spoken_chunk
                        echo_reference = self.controller.spoken_text or spoken
                        answer_changed = answer is not capture_answer
                        if busy != was_busy or answer_changed or (busy and barge_context is None and spoken != grammar_speech):
                            app_recognizer.Reset()
                            app_recognizer.SetGrammar(interruption_grammar(spoken, model) if busy else launch_grammar)
                            grammar_speech = spoken
                        if answer_changed or (busy != was_busy and barge_context is None):
                            self.controller.resume_interruption(barge_context)
                            recognizer.Reset()
                            idle_pause.reset()
                            utterance.clear()
                            held_text = held_app_text = ''
                            turn_accepted = False
                            barge_context = None
                            rate_state = None
                            self.previous_phrase = None
                            diagnostic('speech_capture_reset', reason='new_answer' if answer_changed else 'response_started' if busy else 'response_finished')
                        capture_answer = answer
                        was_busy = busy
                        if self.reset.is_set() or self.muted:
                            self.controller.resume_interruption(barge_context)
                            self.reset.clear()
                            self.previous_phrase = None
                            recognizer.Reset()
                            app_recognizer.Reset()
                            idle_pause.reset()
                            rate_state = None
                            utterance.clear()
                            pending.clear()
                            held_text = held_app_text = ''
                            turn_accepted = False
                            barge_context = None
                            while True:
                                try:
                                    self.audio.get_nowait()
                                except queue.Empty:
                                    break
                            if self.muted:
                                self.stopped.wait(.1)
                                continue
                            announce_ready = True
                        if announce_ready:
                            announce_ready = False
                            if not busy:
                                self.controller.idle()
                        if self.listening_until and time.monotonic() > self.listening_until and not busy:
                            self.listening_until = 0
                            if not self.conversation_active:
                                diagnostic('listening_expired', reason='timeout')
                                self.controller.update('ready', 'Say Jarvis')
                        try:
                            raw = pending.popleft() if pending else self.audio.get(timeout=.2)
                        except queue.Empty:
                            continue
                        pcm, rate_state = audioop.ratecv(raw, 2, 1, rate, 16000, rate_state)
                        input_peak = max(input_peak, audioop.max(pcm, 2))
                        if time.monotonic() >= next_audio_log:
                            diagnostic('microphone_input', input_peak=input_peak, queue_drops=self.queue_drops, input_overflows=self.input_overflows)
                            input_peak = 0
                            next_audio_log = time.monotonic() + 10
                        utterance.extend(pcm)
                        if busy and barge_context is None:
                            del utterance[:-16000 * 2 * INTERRUPTION_PREROLL_SECONDS]
                        if len(utterance) > 16000 * 2 * MAX_AUDIO_SECONDS:
                            self.controller.resume_interruption(barge_context)
                            diagnostic('speech_ignored', reason='utterance_too_long')
                            utterance.clear()
                            held_text = held_app_text = ''
                            turn_accepted = False
                            recognizer.Reset()
                            app_recognizer.Reset()
                            idle_pause.reset()
                            barge_context = None
                            self.listening_until = 0
                            self.controller.events.put(('notice', 'Please keep each spoken command under one minute.'))
                            continue
                        ended = recognizer.AcceptWaveform(pcm)
                        local_result = json.loads(recognizer.Result() if ended else recognizer.PartialResult())
                        local_text = local_result.get('text' if ended else 'partial', '').strip()
                        wake_ended = app_recognizer.AcceptWaveform(pcm)
                        discarded = []
                        if (not busy and barge_context is None and not turn_accepted and not ended and idle_pause.reached(pcm)
                                and discard_idle_phrase(recognizer, app_recognizer, local_text, utterance, discarded)):
                            # Kept a moment in case the name follows it: what's the weather like ... Jarvis?
                            if len(discarded[0].split()) >= 2:
                                self.previous_phrase = (bytes(utterance[-16000 * 2 * PREVIOUS_PHRASE_SECONDS:]), time.monotonic())
                            utterance.clear()
                            if self.listening_until:
                                self.listening_until = 0
                                self.controller.idle()
                            continue
                        if busy and barge_context is None and not self.controller.interrupt_lock.locked():
                            wake_result = app_recognizer.Result() if wake_ended else app_recognizer.PartialResult()
                            candidate = json.loads(wake_result).get('text' if wake_ended else 'partial', '')
                            if (NAME.search(candidate) and not sounds_like_echo(candidate, echo_reference)
                                    and not sounds_like_echo(local_text, echo_reference)):
                                barge_context = self.controller.interruption_context()
                                barge_context['wakeDetected'] = True
                                self.controller.pause_interruption(barge_context)
                        if ended:
                            idle_pause.reset()
                            text = ' '.join(filter(None, [held_text, local_text]))
                            app_text = ' '.join(filter(None, [held_app_text, json.loads(app_recognizer.FinalResult()).get('text', '').strip()]))
                            command_audio = bytes(utterance)
                            previous, self.previous_phrase = self.previous_phrase, None
                            if previous:
                                # How long after the phrase before it ended this one began to speak.
                                previous = None if held_text else (previous[0], time.monotonic() - len(command_audio) / 32000
                                                                   + speech_onset_seconds(command_audio) - previous[1])
                            if busy or barge_context is not None:
                                utterance.clear()
                                held_text = held_app_text = ''
                                turn_accepted = False
                                self.handle_interruption(text, command_audio, context=barge_context)
                                recognizer.Reset()
                                app_recognizer.Reset()
                                barge_context = None
                                self.listening_until = 0
                                continue
                            woke, local_command = split_wake(text)
                            if woke and stop_requested(local_command) and not sounds_like_echo(text, self.controller.spoken_chunk) and self.controller.stop_computer_tasks():
                                utterance.clear()
                                held_text = held_app_text = ''
                                turn_accepted = False
                                self.controller.cancel()
                                self.controller.events.put(('notice', 'PC control stopped.'))
                                barge_context = None
                                self.listening_until = 0
                                continue
                            if not turn_accepted:
                                question = (self.trailing_question(previous)
                                            if previous and split_wake(text) == (True, '') and self.activate_from_wake(text) else '')
                                if question:
                                    utterance.clear()
                                    recognizer.Reset()
                                    app_recognizer.Reset()
                                    rate_state = None
                                    self.listening_until = 0
                                    diagnostic('voice_command_queued', command_chars=len(question))
                                    self.controller.events.put(('command', question))
                                    continue
                                accepted = self.accepts_speech(text, app_text)
                                if not accepted:
                                    utterance.clear()
                                    self.listening_until = 0
                                    self.controller.idle()
                                    continue
                                confirm_wake = accepted in ('confirm', 'confirm_repeated')
                                repeated_call = accepted == 'confirm_repeated'
                            turn_accepted = True
                            try:
                                transcript = self.recognize(command_audio, update_state=not confirm_wake, local_only=confirm_wake)
                                woke, spoken_command = split_wake(transcript, heard=not confirm_wake, allow_repeated=repeated_call)
                                command = without_repeated_names(spoken_command) if woke else ''
                            except (RuntimeError, ValueError) as error:
                                self.controller.events.put(('speech_error', str(error)))
                                self.controller.idle()
                                continue
                            if self.reset.is_set() or self.muted or self.hidden or self.controller.breaks.active:
                                continue
                            if confirm_wake and not woke:
                                # A name Whisper wrote but that was not said to Jarvis, as in we talked about Jarvis.
                                mentioned = re.search(r'\b' + NAMES + r'\b', transcript, re.I)
                                diagnostic('speech_ignored', reason='name_not_addressed' if mentioned else 'wake_unconfirmed',
                                           heard_chars=len(transcript))
                                utterance.clear()
                                held_text = held_app_text = ''
                                turn_accepted = False
                                self.listening_until = 0
                                self.controller.idle()
                                continue
                            if confirm_wake and not held_text:
                                diagnostic('speech_accepted', wake=True, source='repeated_call' if repeated_call else 'wake_grammar',
                                           heard_chars=len(transcript))
                            continuing = self.continuation_waiting(pending, rate, rate_state, recognizer)
                            if self.reset.is_set() or self.stopped.is_set() or self.muted or self.hidden or self.controller.breaks.active:
                                continue
                            if continuing:
                                held_text, held_app_text = text, app_text
                                self.controller.update('listening', 'Listening')
                                continue
                            utterance.clear()
                            held_text = held_app_text = ''
                            turn_accepted = False
                            recognizer.Reset()
                            app_recognizer.Reset()
                            rate_state = None
                            if not command and woke:
                                command = self.trailing_question(previous)
                            if not command:
                                self.controller.idle()
                                if woke:
                                    self.controller.acknowledge_wake()
                                continue
                            control = command.lower().rstrip('.!?').strip()
                            if control in ('cancel', 'never mind', 'nevermind', 'go to sleep'):
                                self.listening_until = 0
                                self.conversation_active = False
                                diagnostic('conversation_ended', reason='voice_cancel')
                                self.controller.update('ready', 'Say Jarvis')
                                continue
                            if control in ('mute', 'stop listening', 'mute microphone'):
                                self.mute(True)
                                self.controller.events.put(('muted', True))
                                continue
                            self.listening_until = 0
                            diagnostic('voice_command_queued', command_chars=len(command))
                            self.controller.events.put(('command', command))
                        else:
                            partial = local_text
                            if (busy and not self.controller.interrupt_lock.locked()
                                    and NAME.search(partial) and not sounds_like_echo(partial, echo_reference)):
                                if barge_context is None:
                                    barge_context = self.controller.interruption_context()
                                    barge_context['wakeDetected'] = True
                                    self.controller.pause_interruption(barge_context)
                                continue
                            if not self.activate_from_wake(partial) and not busy and not self.is_listening():
                                # Show that he is listening as soon as either decoder hears the name, wherever it falls.
                                grammar_partial = json.loads(app_recognizer.PartialResult()).get('partial', '')
                                if wake_in_grammar(grammar_partial) or re.search(r'\b' + NAMES + r'\b', partial, re.I):
                                    self.activate_from_wake('jarvis', heard_chars=len(partial))
            except Exception as exc:
                diagnostic('microphone_failed', error_type=type(exc).__name__, error_code=getattr(exc, 'winerror', None))
                if not self.controller.interrupt_lock.locked():
                    self.controller.speech_paused.clear()
                self.controller.microphone_ready = False
                self.controller.update('error', 'Microphone unavailable. Right-click for microphone settings.')
                self.controller.events.put(('microphone_error', type(exc).__name__))
                self.stopped.wait(5)
            finally:
                self.controller.resume_interruption(barge_context)
