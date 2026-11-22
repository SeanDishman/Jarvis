import audioop
from array import array
from collections import deque
import io
import json
from pathlib import Path
import re
import random
import shutil
import sys
import tempfile
from types import SimpleNamespace
import wave

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop.turn_timing import configure_endpoint, contains_speech
from desktop.listener import Listener
from phone.voice import synthesize_wav


def speech(text):
    with wave.open(io.BytesIO(synthesize_wav(text)), 'rb') as stream:
        pcm = stream.readframes(stream.getnframes())
        rate = stream.getframerate()
    if rate != 16000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, rate, 16000, None)
    frames = [i for i in range(0, len(pcm) - 320 + 1, 320) if audioop.rms(pcm[i:i + 320], 2) > 120]
    assert frames, 'The voice did not generate audible speech'
    return pcm[max(0, frames[0] - 1600):min(len(pcm), frames[-1] + 1920)]


def endpoints(model, pcm):
    from vosk import KaldiRecognizer
    recognizer = KaldiRecognizer(model, 16000)
    rows = []
    for offset in range(0, len(pcm), 2560):
        block = pcm[offset:offset + 2560]
        if recognizer.AcceptWaveform(block):
            text = json.loads(recognizer.Result()).get('text', '')
            if text:
                rows.append({'seconds': round((offset + len(block)) / 32000, 2), 'text': text})
    return rows


def main():
    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    first = speech('Jarvis, I would like you to')
    second = speech('tell me what time it is please.')
    assert contains_speech(second), 'Continued speech was mistaken for silence'
    assert not contains_speech(b'\0\0' * 16000), 'Silence was mistaken for speech'
    cache = ROOT / '.cache'
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache, prefix='pause-verification-') as temporary:
        folder = Path(temporary).resolve()
        assert folder.is_relative_to(cache.resolve())
        model_path = folder / 'model'
        shutil.copytree(ROOT / '.runtime/speech/vosk-model-small-en-us-0.15', model_path)
        config = model_path / 'conf/model.conf'
        contents = config.read_text()
        for rule, seconds in ((2, .5), (3, .75), (4, 1)):
            contents = re.sub(rf'--endpoint.rule{rule}.min-trailing-silence=[^\r\n]*',
                              f'--endpoint.rule{rule}.min-trailing-silence={seconds}', contents)
        config.write_text(contents)
        baseline = Model(str(model_path))
        baseline_audio = first + b'\0\0' * 20800 + second + b'\0\0' * 64000
        old = endpoints(baseline, baseline_audio)
        assert any(row['seconds'] < (len(first) / 32000 + 1.3) for row in old), old
        del baseline
        configure_endpoint(model_path, {'speechPauseSeconds': 2.0})
        model = Model(str(model_path))
        results = []
        for pause in (.8, 1.3, 1.7):
            audio = first + b'\0\0' * round(16000 * pause) + second + b'\0\0' * 64000
            end_of_speech = (len(first) + len(second)) / 32000 + pause
            rows = endpoints(model, audio)
            assert len(rows) == 1 and rows[0]['seconds'] >= end_of_speech, (pause, rows)
            results.append({'pause_seconds': pause, 'complete_turn': rows[0]})
        listener = Listener(SimpleNamespace(breaks=SimpleNamespace(active=False)), {})
        continuation_results = []
        for level in (20, 100, 300, 1000):
            rng = random.Random(42)
            noise = array('h', (int(rng.gauss(0, level)) for _ in range(20480))).tobytes()
            assert contains_speech(noise), 'Noise fixture must reproduce the original false positive'
            pending = deque([noise])
            assert not listener.continuation_waiting(pending, 16000, None, KaldiRecognizer(model, 16000))
            assert not pending
            continuation_results.append({'noise_level': level, 'false_continuation_rejected': True})
        pending = deque([second])
        assert listener.continuation_waiting(pending, 16000, None, KaldiRecognizer(model, 16000))
        assert list(pending) == [second]
        del model
    print(json.dumps({'baseline_split_the_request': old, 'patient_turns': results,
                      'continued_speech_detection': 'passed', 'noise_confirmation': continuation_results}, indent=2))


if __name__ == '__main__':
    main()
