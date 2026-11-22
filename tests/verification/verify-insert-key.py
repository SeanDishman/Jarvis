import ctypes
from pathlib import Path
import sys
import time
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests/verification'))
from ui_harness import Interface
from desktop.hotkey import KeyboardEvent, VK_INSERT, WM_KEYDOWN, WM_KEYUP


def send(orb, message):
    event = KeyboardEvent(vkCode=VK_INSERT)
    assert orb.hotkey._handle(0, message, ctypes.addressof(event)) == 1


def main():
    with Interface() as ui:
        orb, breaks = ui.orb, ui.breaks
        orb.hotkey._user = Mock()
        playback = Mock()
        orb.controller.playback = playback
        ui.ui('open', {'window': 'panel', 'focus': False})
        ui.wait(lambda: ui.state('panel') == 'normal', 10, 'The command panel did not open')
        orb.listener.listening_until = time.monotonic() + 30
        orb.listener.reset.clear()

        send(orb, WM_KEYDOWN)
        ui.wait(lambda: ui.state('orb') == 'withdrawn', 5, 'Insert did not hide the orb')
        ui.wait(lambda: ui.state('panel') == 'withdrawn', 5, 'Insert did not hide the command panel')
        assert not breaks.active, 'Insert must not pause Jarvis'
        assert not breaks.path.exists(), 'Visibility toggle wrote a persisted break'
        playback.abort.assert_not_called()
        assert not orb.controller.cancelled.is_set(), 'Insert cancelled an answer'
        assert orb.listener.listening_until == 0, 'Hidden Jarvis kept his listening window'
        assert orb.listener.hidden and not orb.listener.is_listening(), 'Hidden Jarvis is still listening'
        assert orb.controller.state == 'hidden', 'Hidden state was not reported'
        orb.listener.callback(b'\0\0' * 160, 160, None, None)
        assert orb.listener.audio.empty(), 'Hidden Jarvis still accepted microphone audio'
        assert not orb.listener.accepts_speech('jarvis what time is it'), 'Hidden Jarvis still answered his wake word'
        ui.emit('finished', True)
        ui.emit('research', {'id': 'hidden-job', 'status': 'running', 'scope': 'web', 'question': 'Hidden work'})
        ui.pause(.6)
        assert not orb.listener.is_listening(), 'A finished answer reopened listening while hidden'
        assert ui.state('console') is None, 'Automatic progress popup ignored hidden UI'

        for _ in range(10):
            send(orb, WM_KEYDOWN)
        ui.pause(.5)
        assert ui.state('orb') == 'withdrawn', 'Holding Insert toggled repeatedly'
        send(orb, WM_KEYUP)
        orb.listener.listening_until = 0
        send(orb, WM_KEYDOWN)
        send(orb, WM_KEYUP)
        ui.wait(lambda: ui.state('orb') == 'normal' and ui.state('panel') == 'normal', 5, 'Insert did not show Jarvis again')
        assert not breaks.active, 'Visibility toggle changed break state'
        playback.abort.assert_not_called()
        assert not orb.listener.hidden, 'Showing Jarvis left his microphone off'
        assert not orb.listener.conversation_active, 'Showing Jarvis enabled wake-free conversation'
        ui.wait(lambda: orb.controller.state == 'ready', 5, 'Showing Jarvis did not return him to ready')
        assert not orb.listener.accepts_speech("what's up dude"), 'Ambient speech bypassed the wake word'
        assert orb.listener.accepts_speech('Jarvis what time is it'), 'Wake-prefixed question was rejected'
        ui.emit('finished', True)
        ui.pause(.3)
        assert not orb.listener.accepts_speech('what time is it'), 'A finished reply enabled wake-free follow-ups'

        ui.page('panel').evaluate("window.jarvis.ui('window', {action: 'minimize'})")
        ui.wait(lambda: ui.state('panel') == 'iconic', 5, 'The panel did not minimize')
        for _ in range(2):
            send(orb, WM_KEYDOWN)
            send(orb, WM_KEYUP)
            ui.pause(.8)
        ui.wait(lambda: ui.state('orb') == 'normal', 5, 'Jarvis did not come back')
        assert ui.state('panel') == 'iconic', 'Insert restored a minimized panel'
        assert not orb.listener.conversation_active, 'Showing Jarvis again enabled wake-free conversation'

        orb.controller.take_break({'delay_seconds': 300, 'wake_at': None})
        ui.wait(lambda: ui.state('orb') == 'withdrawn', 5, 'The break did not hide Jarvis')
        send(orb, WM_KEYDOWN)
        send(orb, WM_KEYUP)
        ui.wait(lambda: not breaks.active and ui.state('orb') == 'normal', 5, 'Insert did not cancel a timed break')
        assert breaks.snapshot()['wakeAt'] is None, 'Insert left a wake time behind'
        print('PASS: Insert hides Jarvis and stops listening; showing him or finishing a reply still requires Jarvis before each command. Minimized windows, key repeat and timed breaks work.')


if __name__ == '__main__':
    main()
