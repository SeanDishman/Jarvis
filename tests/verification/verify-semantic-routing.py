import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from desktop.intent import classify_request

spec = importlib.util.spec_from_file_location('verification_helper', Path(__file__).with_name('verify-app-interaction.py'))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def main():
    cases = [
        ('Minimize Outlook and Spotify', 'computer', None),
        ('Close Notepad, Spotify, and Prism Launcher', 'computer', None),
        ('Open YouTube in Firefox', 'computer', None),
        ('Open YouTube in Brave', 'computer', None),
        ('Move Prism Launcher to the second monitor', 'normal', 'arrange_windows'),
        ('Put Task Manager on the other display', 'normal', 'arrange_windows'),
        ('Raise the audio a bit', 'normal', 'windows_control'),
        ('The speakers are blasting; bring them down a little', 'normal', 'windows_control'),
        ('Jot down a reminder to stretch in twenty minutes', 'normal', 'create_reminder'),
        ('What does minimizing Outlook mean? Do not do it.', 'normal', None),
        ('Do not search the web. Explain what research means.', 'normal', None),
    ]
    failed = []
    with helper.model_service() as request, patch('desktop.intent.local_request', side_effect=request):
        for index, (text, action, tool) in enumerate(cases):
            selected = classify_request(text, 'semantic-verification-' + str(index))
            ok = selected['action'] == action and not selected['needs_clarification']
            if tool:
                ok = ok and tool in selected['capabilities']
            elif action == 'normal':
                ok = ok and not selected['capabilities']
            if tool == 'windows_control':
                expected = 'volume_up' if index == 6 else 'volume_down'
                ok = ok and expected in selected['system_actions'] and 'clipboard_read' not in selected['system_actions']
            if index in (2,3):
                ok = ok and selected['browser'].casefold() == ('firefox' if index == 2 else 'brave')
            print(json.dumps({'pass':bool(ok),'request':text,'decision':selected}), flush=True)
            if not ok:
                failed.append(text)
    if failed:
        raise AssertionError('Unexpected routing: ' + '; '.join(failed))
    print('PASS: all semantic routing cases; no desktop actions executed.', flush=True)


if __name__ == '__main__':
    main()
