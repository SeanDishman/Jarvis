import json
from desktop.common import ROOT

UNAVAILABLE = "I don't have access to your email, sir."


def enabled():
    try:
        settings = json.loads((ROOT / 'desktop/settings.json').read_text(encoding='utf-8'))
        return settings.get('emailEnabled') is True
    except (OSError, ValueError):
        return False


def unavailable():
    return {'ok': False, 'disabled': True, 'error': UNAVAILABLE, 'messages': [], 'errors': [UNAVAILABLE]}
