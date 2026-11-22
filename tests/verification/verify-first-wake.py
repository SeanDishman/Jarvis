import audioop
from contextlib import nullcontext
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


def verify(pcm, pending_reset):
    with tempfile.TemporaryDirectory(prefix='jarvis-first-wake-') as directory:
        with patch('desktop.controller.BreakState', return_value=BreakState(Path(directory) / 'break.json')):
            controller = Controller(queue.Queue())
        listener = Listener(controller, {})
        controller.listener = listener
        controller.windows_desktop = 'Default'
        acknowledged = threading.Event()
        worker = None
        ready = []
        update = controller.update

        def publish(state, note=''):
            if state == 'ready' and not ready:
                assert controller.microphone_ready, 'Ready was published before microphone capture'
                assert not listener.reset.is_set(), 'Ready was published before the startup reset'
                ready.append(time.monotonic())
                for offset in range(0, len(pcm), 2560):
                    listener.callback(pcm[offset:offset + 2560], 1280, None, None)
            update(state, note)

        def acknowledge():
            acknowledged.set()
            listener.stopped.set()
            return True

        controller.update = publish
        if pending_reset:
            listener.activate(source='launch')
        try:
            controller.warmup()
            assert controller.state == 'starting', 'Voice warmup falsely announced readiness'
            with patch('sounddevice.RawInputStream', return_value=nullcontext()), \
                    patch('sounddevice.query_devices', return_value={'default_samplerate': 16000, 'name': 'First wake fixture'}), \
                    patch.object(controller, 'acknowledge_wake', side_effect=acknowledge) as reply:
                worker = threading.Thread(target=listener.run, daemon=True)
                worker.start()
                assert acknowledged.wait(10), 'A single first wake word was not acknowledged'
                reply.assert_called_once()
            return {'pending_launch_reset': pending_reset, 'wake_utterances_sent': 1,
                    'acknowledgements': 1, 'ready_after_microphone_open': True,
                    'decode_ms': round((time.monotonic() - ready[0]) * 1000)}
        finally:
            listener.stopped.set()
            if worker:
                worker.join(3)
            for service in (controller.research, controller.tasks, controller.browser):
                service.close()


def speech(text):
    with wave.open(io.BytesIO(synthesize_wav(text)), 'rb') as wav:
        pcm, _ = audioop.ratecv(wav.readframes(wav.getnframes()), 2, 1, wav.getframerate(), 16000, None)
    voiced = [i for i in range(0, len(pcm), 320) if audioop.rms(pcm[i:i + 320], 2) > 100]
    assert voiced, 'The test voice produced no speech'
    return pcm[max(0, voiced[0] - 640):voiced[-1] + 960]


if __name__ == '__main__':
    pcm = speech('Jarvis.') + b'\0\0' * 64000
    for pending_reset in (False, True):
        print(json.dumps(verify(pcm, pending_reset)), flush=True)
    background = speech('We should get the report ready for our meeting tomorrow morning.')
    after_background = background + b'\0\0' * 9600 + audioop.mul(pcm, 2, .25)
    print(json.dumps({'wake_after_background': verify(after_background, False)}), flush=True)
