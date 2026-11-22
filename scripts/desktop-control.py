import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from desktop.common import local_request
from desktop.windows import set_startup, startup_enabled


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action in ('enable-startup', 'disable-startup'):
        set_startup(action == 'enable-startup')
        print(json.dumps({'startupEnabled': startup_enabled()}))
    elif action == 'stop':
        print(json.dumps(local_request(18792, '/shutdown', {})))
    elif action == 'email-settings':
        print(json.dumps(local_request(18792, '/email/settings', {})))
    elif action == 'status':
        print(json.dumps(local_request(18792, '/health'), indent=2))
    elif action == 'ask':
        print(json.dumps(local_request(18792, '/command', {'text': ' '.join(sys.argv[2:]), 'speak': True}, timeout=130), indent=2))
    else:
        raise ValueError('Unknown command')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
