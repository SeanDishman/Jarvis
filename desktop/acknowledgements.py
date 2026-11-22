import json
import time

from desktop.common import local_request
from desktop.diagnostics import event as diagnostic
from desktop.research import response_text

FALLBACK_ACKNOWLEDGEMENT = 'Let me check that, sir.'
WAKE_REPLY = 'Yes, sir?'


def acknowledge(text, session, context=None):
    started = time.monotonic()
    try:
        response = local_request(18791, '/v1/responses', {
            'model': 'openclaw/acknowledgement', 'user': session,
            'input': json.dumps({'utterance': text, 'context': context or {}}, ensure_ascii=False),
        }, timeout=5)
        answer = response_text(response).strip()
        if not answer or len(answer) > 180 or len(answer.split()) > 28 or '\n' in answer or '\r' in answer:
            raise ValueError('The acknowledgement was not a short sentence')
        diagnostic('acknowledgement_generated', duration_ms=round((time.monotonic()-started)*1000))
        return answer
    except Exception as exc:
        diagnostic('acknowledgement_fallback', error_type=type(exc).__name__, duration_ms=round((time.monotonic()-started)*1000))
        return FALLBACK_ACKNOWLEDGEMENT
