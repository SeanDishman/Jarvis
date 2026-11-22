import argparse
import audioop
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.listener import Listener
from phone.voice import synthesize_wav


def recording(text):
    folder = ROOT / '.cache/interruption-verification'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha256(text.encode()).hexdigest()[:16] + '.wav')
    if not path.exists():
        path.write_bytes(synthesize_wav(text))
    data = path.read_bytes()
    with wave.open(io.BytesIO(data), 'rb') as wav:
        pcm, _ = audioop.ratecv(wav.readframes(wav.getnframes()), 2, 1, wav.getframerate(), 16000, None)
    return data, pcm


class Playback:
    def __init__(self):
        self.writes = 0
        self.aborted = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def write(self, _block):
        self.writes += 1
        self.aborted.wait(.025)

    def abort(self):
        self.aborted.set()


def verify(utterance, expected, audible=False, followup=False):
    import sounddevice as sd
    explanation = 'The forecast calls for rain throughout the afternoon. The wind will become stronger later in the evening, with clearer weather expected tomorrow morning.'
    response_wav, response_pcm = recording(explanation)
    _, command_pcm = recording(utterance)
    prefix = response_pcm[:32000]
    incoming = prefix + command_pcm + b'\0\0' * 48000
    next_pcm = recording('Jarvis, what is two plus two?')[1] if followup else b''
    stopped_reply = threading.Event()
    playback = Playback()
    metrics, errors = {}, []
    with tempfile.TemporaryDirectory(prefix='jarvis-interruption-') as directory:
        with patch('desktop.controller.BreakState', return_value=BreakState(Path(directory) / 'break.json')):
            controller = Controller(queue.Queue())
        listener = Listener(controller, {'speechPauseSeconds': 2.0})
        recognize = listener.recognize
        def track_recognition(*args, **kwargs):
            text = recognize(*args, **kwargs)
            metrics.setdefault('recognitions', []).append(text)
            return text
        listener.recognize = track_recognition
        controller.listener = listener
        controller.windows_desktop = 'Default'
        controller.speech_cache[explanation] = response_wav
        controller.busy.acquire()
        original_query = sd.query_devices

        def device_info(device=None, kind=None):
            return {'name': 'Prerecorded interruption', 'default_samplerate': 16000} if kind == 'input' else original_query(device, kind)

        def speak():
            try:
                controller.speak(explanation)
            except Exception as exc:
                errors.append(type(exc).__name__ + ': ' + str(exc))
            finally:
                controller.release_response()

        def feed():
            started = time.monotonic()
            for offset in range(0, len(incoming), 2560):
                if listener.stopped.is_set():
                    break
                listener.callback(incoming[offset:offset + 2560], 1280, None, None)
                if controller.speech_paused.is_set() and 'paused_after_wake_ms' not in metrics:
                    metrics['paused_after_wake_ms'] = round((time.monotonic() - started - len(prefix) / 32000) * 1000)
                listener.stopped.wait(.08)
            if followup and stopped_reply.wait(15):
                continued = b'\0\0' * 48000 + next_pcm + b'\0\0' * 80000
                for offset in range(0, len(continued), 2560):
                    if listener.stopped.is_set():
                        break
                    listener.callback(continued[offset:offset + 2560], 1280, None, None)
                    listener.stopped.wait(.08)

        speaker = threading.Thread(target=speak, daemon=True)
        microphone = threading.Thread(target=listener.run, daemon=True)
        feeder = threading.Thread(target=feed, daemon=True)
        with patch('sounddevice.RawInputStream', return_value=nullcontext()), \
                patch('sounddevice.query_devices', side_effect=device_info), \
                (nullcontext() if audible else patch('sounddevice.RawOutputStream', return_value=playback)):
            try:
                speaker.start()
                microphone.start()
                deadline = time.monotonic() + 15
                while not (controller.microphone_ready and controller.spoken_chunk) and time.monotonic() < deadline:
                    time.sleep(.02)
                assert controller.microphone_ready and controller.spoken_chunk, 'Audio workers did not start'
                feeder.start()
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        event = controller.events.get(timeout=.1)
                    except queue.Empty:
                        continue
                    if event[0] == 'interruption':
                        metrics['transcript'] = event[1]
                        controller.interrupt(event[1], event[2])
                        break
                assert 'transcript' in metrics, ('The spoken interruption was lost', metrics)
                assert 'paused_after_wake_ms' in metrics, 'Playback did not yield to the wake word'
                assert controller.cancelled.is_set() == (expected == 'stop'), (expected, metrics)
                assert not controller.speech_paused.is_set(), 'Playback stayed paused after the decision'
                if expected == 'stop':
                    speaker.join(2)
                    assert not speaker.is_alive(), 'Stopped speech kept playing'
                    assert not controller.busy.locked(), 'Stopped speech kept the command lock'
                    assert not listener.muted and not listener.hidden, 'Stop disabled wake listening'
                    if followup:
                        stopped_reply.set()
                        states = []
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            try:
                                event = controller.events.get(timeout=.1)
                            except queue.Empty:
                                continue
                            if event[0] == 'state':
                                states.append(event[1])
                            if event[0] == 'command':
                                metrics['followup'] = event[1]
                                result = controller.respond(event[1], speak=False)
                                assert result.get('ok'), result
                                assert '4' in result['text'] or 'four' in result['text'].lower(), result
                                metrics['followup_answer'] = result['text']
                                break
                        assert 'followup_answer' in metrics, ('The next wake request was lost', metrics)
                        states.extend(row[1] for row in list(controller.events.queue) if row[0] == 'state')
                        assert 'listening' in states and 'thinking' in states, states
                        assert controller.state == 'ready', controller.state
                        metrics['followup_states'] = states
                else:
                    writes = playback.writes
                    time.sleep(.2)
                    assert audible or playback.writes > writes, 'Ignored interruption did not resume audio'
                assert not errors, errors
                metrics.update(input=utterance, action=expected, audible=audible)
                return metrics
            finally:
                listener.stopped.set()
                controller.cancel()
                for worker in (feeder, microphone, speaker):
                    if worker.ident:
                        worker.join(3)
                for service in (controller.research, controller.tasks, controller.browser):
                    service.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--audible', action='store_true')
    parser.add_argument('--followup', action='store_true')
    args = parser.parse_args()
    cases = [('Jarvis, shut the fuck up.', 'stop')] if args.followup else [('Jarvis, shut the fuck up.', 'stop'),
                                ('Jarvis, stop talking.', 'stop'),
                                ('Jarvis, do not stop. Please keep going.', 'ignore')]
    for utterance, expected in cases:
        print(json.dumps(verify(utterance, expected, args.audible, args.followup)), flush=True)
