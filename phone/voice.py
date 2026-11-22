import functools
import io
import json
import wave

from desktop.common import ROOT

SETTINGS = json.loads((ROOT / 'phone/settings.json').read_text(encoding='utf-8'))


@functools.lru_cache(maxsize=1)
def speech_voice():
    from piper import PiperVoice
    return PiperVoice.load(str(ROOT / '.runtime/speech' / SETTINGS['voice']['model']))


def synthesize_wav(text: str) -> bytes:
    from piper import SynthesisConfig
    voice = SETTINGS['voice']
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        speech_voice().synthesize_wav(text, wav, syn_config=SynthesisConfig(
            length_scale=voice['lengthScale'] / voice.get('speed', 1.0), noise_scale=voice['noiseScale'],
            noise_w_scale=voice['noiseWScale']))
    return buffer.getvalue()
