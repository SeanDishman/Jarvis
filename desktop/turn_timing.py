import math
from pathlib import Path
import re

MAX_AUDIO_SECONDS = 60
DEFAULT_PAUSE_SECONDS = 2.0


class IdleSpeechPause:
    """Find a short gap after speech while waiting for the start of a wake phrase."""

    FRAME_BYTES = 640  # 20 ms of mono 16 kHz, signed 16-bit PCM
    QUIET_FRAMES = 16  # WebRTC also allows for the trailing sounds of a word.

    def __init__(self):
        import webrtcvad
        self.vad = webrtcvad.Vad(2)
        self.reset()

    def reset(self):
        self.remainder = b''
        self.quiet_frames = None

    def reached(self, pcm):
        data = self.remainder + pcm
        end = len(data) - len(data) % self.FRAME_BYTES
        for offset in range(0, end, self.FRAME_BYTES):
            if self.vad.is_speech(data[offset:offset + self.FRAME_BYTES], 16000):
                self.quiet_frames = 0
            elif self.quiet_frames is not None:
                self.quiet_frames += 1
        self.remainder = data[end:]
        if self.quiet_frames is None or self.quiet_frames < self.QUIET_FRAMES:
            return False
        self.quiet_frames = None
        return True


def pause_seconds(settings):
    try:
        value = float(settings.get('speechPauseSeconds', DEFAULT_PAUSE_SECONDS))
    except (TypeError, ValueError):
        return DEFAULT_PAUSE_SECONDS
    return value if math.isfinite(value) and 1.0 <= value <= 5.0 else DEFAULT_PAUSE_SECONDS


def configure_endpoint(model_path, settings):
    delay = pause_seconds(settings)
    path = Path(model_path) / 'conf/model.conf'
    if not path.exists():
        return delay
    original = path.read_text(encoding='utf-8')
    updated = original
    options = {f'endpoint.rule{rule}.min-trailing-silence': delay + extra
               for rule, extra in ((2, 0), (3, .5), (4, 1))}
    options['endpoint.rule5.min-utterance-length'] = MAX_AUDIO_SECONDS
    for name, value in options.items():
        line = f'--{name}={value:g}'
        pattern = r'^--' + re.escape(name) + r'=[^\r\n]*'
        if re.search(pattern, updated, re.M):
            updated = re.sub(pattern, line, updated, flags=re.M)
        else:
            updated = updated.rstrip() + '\n' + line + '\n'
    if updated != original:
        backup = path.with_suffix('.conf.original')
        if not backup.exists():
            backup.write_text(original, encoding='utf-8')
        temporary = path.with_suffix('.conf.tmp')
        temporary.write_text(updated, encoding='utf-8')
        temporary.replace(path)
    return delay


def contains_speech(pcm):
    import webrtcvad
    vad = webrtcvad.Vad(2)
    voiced = 0
    frame_bytes = 640
    for offset in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        voiced = voiced + 1 if vad.is_speech(pcm[offset:offset + frame_bytes], 16000) else 0
        if voiced >= 2:
            return True
    return False
