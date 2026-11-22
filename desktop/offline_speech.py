import ctypes
import os
from pathlib import Path
import sys
import threading
import time

from desktop.common import ROOT
from desktop.diagnostics import event as diagnostic
from desktop.turn_timing import MAX_AUDIO_SECONDS

NAME = 'whisper-small.en'
LABEL = 'Whisper small'
MODEL_DIR = ROOT / '.runtime/models/whisper-small.en'
PROMPT = 'Jarvis. GPT Luna, Sol, Astra. Proton VPN. Gmail. Outlook. OpenClaw. Notepad. Obsidian.'
# CTranslate2 loads cuBLAS by name on its first GPU call. desktop/gpu-requirements.txt installs NVIDIA's
# wheels, so load those DLLs by absolute path first instead of editing PATH for every app Jarvis launches.
CUDA_LIBRARIES = ('nvidia/cublas/bin/cublasLt64_12.dll', 'nvidia/cublas/bin/cublas64_12.dll')
_model = None
_device = None
_gpu_failed = False
_lock = threading.Lock()


def ready():
    return all((MODEL_DIR/name).is_file() for name in ('model.bin','config.json','tokenizer.json'))


def device():
    return _device


def load_cuda_libraries():
    for library in CUDA_LIBRARIES:
        path = next((Path(entry)/library for entry in sys.path if entry and (Path(entry)/library).is_file()), None)
        if path is None:
            return False
        ctypes.WinDLL(str(path))
    return True


def load_model():
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_speech_timestamps
    global _gpu_failed
    get_speech_timestamps(np.zeros(16000, dtype=np.float32))
    if not _gpu_failed:
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() and load_cuda_libraries():
                # float32 is the fastest GPU type on GTX 16-series cards, which have no tensor cores for float16.
                model = WhisperModel(str(MODEL_DIR), device='cuda', compute_type='float32', local_files_only=True)
                segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32), language='en', beam_size=1, vad_filter=False)
                list(segments)
                return model, 'cuda'
        except Exception as error:
            _gpu_failed = True
            diagnostic('offline_gpu_unavailable', source='local', model=NAME, error_type=type(error).__name__)
    return WhisperModel(str(MODEL_DIR), device='cpu', compute_type='int8', cpu_threads=max(1,min(4,(os.cpu_count() or 2)-1)),
                        num_workers=1, local_files_only=True), 'cpu'


def loaded_model():
    global _model, _device
    if _model is None:
        started = time.monotonic()
        diagnostic('offline_model_loading', source='local', model=NAME)
        try:
            _model, _device = load_model()
        except Exception as error:
            diagnostic('offline_model_failed', source='local', model=NAME, error_type=type(error).__name__)
            raise RuntimeError(f'Local {LABEL} could not start. Run scripts/windows/setup-local-features.cmd.') from None
        diagnostic('offline_model_loaded', source='local', model=NAME, device=_device, duration_ms=round((time.monotonic()-started)*1000))
    return _model


def preload():
    def load():
        try:
            with _lock:
                loaded_model()
        except RuntimeError:
            pass
    if ready() and _model is None:
        threading.Thread(target=load, name='whisper-preload', daemon=True).start()


def transcribe(pcm):
    global _model, _device, _gpu_failed
    if not ready():
        raise RuntimeError(f'Local {LABEL} is not installed. Run scripts/windows/setup-local-features.cmd.')
    if not pcm or len(pcm)%2 or len(pcm) > 16000*2*MAX_AUDIO_SECONDS:
        raise ValueError('Use a spoken command shorter than one minute')
    import numpy as np
    audio = np.frombuffer(pcm,dtype=np.int16).astype(np.float32)/32768.0
    with _lock:
        model = loaded_model()
        try:
            segments,_ = model.transcribe(audio,language='en',beam_size=1,vad_filter=True,
                condition_on_previous_text=False,initial_prompt=PROMPT,temperature=0)
            text = ' '.join(segment.text.strip() for segment in segments).strip()[:4000]
        except Exception as error:
            diagnostic('offline_transcription_failed', source='local', model=NAME, device=_device, error_type=type(error).__name__)
            _gpu_failed = _gpu_failed or _device == 'cuda'
            _model = _device = None
            raise RuntimeError(f'Local {LABEL} could not transcribe that command. Please try again.') from None
        diagnostic('offline_transcription_finished',source='local',model=NAME,device=_device,heard_chars=len(text))
        return text
