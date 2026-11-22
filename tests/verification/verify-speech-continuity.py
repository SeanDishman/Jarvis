import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import queue
import runpy
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from desktop.breaks import BreakState
from desktop.controller import Controller
from desktop.listener import Listener


def verify(audible=False):
    import sounddevice as sd
    fixture = runpy.run_path(str(Path(__file__).with_name('verify-voice-interruption.py')))
    texts = [
        'The forecast calls for rain throughout the afternoon. The wind will become stronger later in the evening, with clearer weather expected tomorrow morning.',
        'I can help you manage your desktop, search for information, or answer your questions. Tell me what you would like to do next.',
        'Your computer is running normally. The browser is open and your documents are available in the selected folder.',
    ]
    recordings = [fixture['recording'](text) for text in texts]
    with tempfile.TemporaryDirectory(prefix='jarvis-continuity-') as directory:
        with patch('desktop.controller.BreakState', return_value=BreakState(Path(directory) / 'break.json')):
            controller = Controller(queue.Queue())
        diagnostics, gaps, streams = [], [], []
        actual_output = sd.RawOutputStream

        class Output:
            def __init__(self, **options):
                self.rate = options['samplerate']
                self.output = actual_output(**options) if audible else None
                self.previous = None
                streams.append(self)

            def __enter__(self):
                if self.output:
                    self.output.__enter__()
                return self

            def __exit__(self, *args):
                if self.output:
                    self.output.__exit__(*args)

            def write(self, block):
                now = time.monotonic()
                if self.previous is not None:
                    gaps.append((now - self.previous) * 1000)
                if self.output:
                    self.output.write(block)
                else:
                    time.sleep(len(block) / (2 * self.rate))
                self.previous = time.monotonic()

            def abort(self):
                if self.output:
                    self.output.abort()

        try:
            with patch('sounddevice.RawOutputStream', side_effect=Output), \
                    patch('desktop.controller.diagnostic', side_effect=lambda event, **fields: diagnostics.append((event, fields))):
                controller.speak(' '.join(texts))
            waits = [fields['duration_ms'] for event, fields in diagnostics if event == 'playback_buffer_ready' and fields['source'] == 'continuation']
            assert len(waits) >= 1, 'The reply did not span multiple audio chunks'
            assert len(streams) == 1, 'Audio device was reopened between chunks'
            assert max(waits) < 100, ('Synthesis interrupted playback', waits)
            assert max(gaps) < 100, ('Playback writes stalled', max(gaps))
            metrics = {'audible': audible, 'chunks': len(waits) + 1, 'output_streams': len(streams),
                       'continuation_wait_ms': waits, 'max_write_gap_ms': round(max(gaps), 2)}
            print(json.dumps({'continuous_playback': metrics}), flush=True)
            controller.busy.acquire()
            controller.windows_desktop = 'Default'
            echo_results = []
            for text, (_, pcm) in zip(texts, recordings):
                controller.spoken_chunk = text
                controller.spoken_text = ' '.join(texts)
                listener = Listener(controller, {})
                controller.listener = listener
                with patch('sounddevice.RawInputStream', return_value=nullcontext()), \
                        patch('sounddevice.query_devices', return_value={'name': 'Playback echo fixture', 'default_samplerate': 16000}), \
                        patch.object(listener, 'recognize', return_value=text) as transcribe, \
                        patch.object(controller, 'pause_interruption', wraps=controller.pause_interruption) as pause:
                    worker = threading.Thread(target=listener.run, daemon=True)
                    worker.start()
                    try:
                        deadline = time.monotonic() + 10
                        while not controller.microphone_ready and time.monotonic() < deadline:
                            time.sleep(.02)
                        assert controller.microphone_ready, 'Listener did not start'
                        audio = pcm + b'\0\0' * 64000
                        for offset in range(0, len(audio), 2560):
                            listener.callback(audio[offset:offset + 2560], 1280, None, None)
                        deadline = time.monotonic() + 10
                        while not listener.audio.empty() and time.monotonic() < deadline:
                            time.sleep(.02)
                        assert listener.audio.empty(), 'Echo recording was not consumed'
                    finally:
                        listener.stopped.set()
                        worker.join(3)
                        controller.microphone_ready = False
                    assert not worker.is_alive(), 'Echo listener did not stop'
                    assert not pause.called, 'Playback echo paused speech'
                    assert not transcribe.called, 'Playback echo reached interruption transcription'
                echo_results.append({'reply_chars': len(text), 'false_pauses': pause.call_count})
            print(json.dumps({'playback_echo': echo_results}), flush=True)
            return metrics
        finally:
            if controller.busy.locked():
                controller.busy.release()
            controller.cancel()
            for service in (controller.research, controller.tasks, controller.browser):
                service.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--audible', action='store_true')
    verify(parser.parse_args().audible)
