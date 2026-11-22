import base64
import io
import json
import os
import pathlib
import wave

from desktop.common import protect
from desktop.diagnostics import event as diagnostic
from desktop.turn_timing import MAX_AUDIO_SECONDS
import requests

ENDPOINT = 'https://api.groq.com/openai/v1/audio/transcriptions'
MODELS = ('whisper-large-v3', 'whisper-large-v3-turbo')
ENTROPY = b'VibeCode.GroqSpeech.v1'


def groq_key():
    filename = pathlib.Path(os.environ['APPDATA']) / 'VibeCode/groq-speech.json'
    try:
        sealed = json.loads(filename.read_text(encoding='utf-8-sig'))['Secret']
        key = protect(base64.b64decode(sealed, validate=True), decrypt=True, entropy=ENTROPY).decode('utf-8').strip()
        if not key:
            raise ValueError('Empty key')
        return key
    except Exception:
        raise RuntimeError('Jarvis could not unlock your VibeCode Groq speech key. Check Speech to text in VibeCode Settings and run Jarvis under the same Windows account.') from None


def speech_status(model='whisper-large-v3'):
    from desktop.offline_speech import LABEL, NAME, device, ready
    from desktop.preferences import Preferences
    offline = {'offlineReady':ready(),'offlineModel':LABEL,'offlineDevice':device(),'mode':Preferences().read().get('offlineSpeech','auto')}
    if offline['mode'] == 'offline' or (offline['mode'] == 'auto' and offline['offlineReady']):
        return {'provider':'Local Whisper','model':NAME,'keySource':None,'configured':offline['offlineReady'],
                'error':None if offline['offlineReady'] else f'Run scripts/windows/setup-local-features.cmd to install {LABEL}.',**offline}
    try:
        groq_key()
        return {'provider': 'Groq', 'model': model, 'keySource': 'VibeCode', 'configured': True, **offline}
    except RuntimeError as error:
        return {'provider': 'Groq', 'model': model, 'keySource': 'VibeCode', 'configured': False, 'error': str(error), **offline}


def pcm_wav(pcm, rate=16000):
    if not pcm or len(pcm) % 2 or len(pcm) > 16000 * 2 * MAX_AUDIO_SECONDS or rate != 16000:
        raise ValueError('Use a spoken command shorter than one minute')
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm)
    return output.getvalue()


def transcribe_pcm(pcm, model='whisper-large-v3', mode='online'):
    if mode not in ('auto','online','offline'):
        raise ValueError('Choose an online, automatic or offline speech mode')
    from desktop.offline_speech import ready, transcribe
    if mode == 'offline':
        return transcribe(pcm)
    if mode == 'auto' and ready():
        try:
            return transcribe(pcm)
        except RuntimeError:
            diagnostic('transcription_fallback',source='groq',model=model)
    return transcribe_online(pcm,model)


def transcribe_online(pcm, model='whisper-large-v3'):
    if model not in MODELS:
        raise ValueError('Choose a supported Whisper model')
    audio = pcm_wav(pcm)
    try:
        key = groq_key()
    except RuntimeError:
        diagnostic('transcription_service_failed', reason='key_unavailable')
        raise
    try:
        response = requests.post(ENDPOINT, headers={'Authorization': 'Bearer ' + key},
            files={'file': ('command.wav', audio, 'audio/wav')},
            data={'model': model, 'language': 'en', 'temperature': '0', 'response_format': 'json',
                  'prompt': 'Jarvis. GPT Luna, Sol, Astra. Proton VPN. Gmail. Outlook. OpenClaw.'},
            timeout=(5, 30), allow_redirects=False)
    except requests.RequestException as exc:
        diagnostic('transcription_service_failed', reason='network', error_type=type(exc).__name__)
        raise RuntimeError('Groq could not transcribe that command. Check your connection and try again.') from None
    diagnostic('transcription_service_response', error_code=response.status_code, model=model)
    if response.status_code == 401:
        raise RuntimeError('Groq rejected the speech key. Check it in VibeCode Speech to text settings.')
    if response.status_code == 403:
        raise RuntimeError('Groq is denying this network connection or account access. Check VPN or network restrictions and your Groq account.')
    if response.status_code == 429:
        raise RuntimeError('Groq speech has reached an account rate limit. Please try again shortly.')
    if response.status_code != 200:
        raise RuntimeError(f'Groq speech is unavailable right now. Status {response.status_code}.')
    try:
        text = response.json().get('text', '')
        if not isinstance(text, str):
            raise ValueError()
        return text.strip()[:4000]
    except ValueError:
        diagnostic('transcription_service_failed', reason='invalid_response')
        raise RuntimeError('Groq returned an unreadable transcription. Please try again.') from None
