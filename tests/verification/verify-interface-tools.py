import ctypes
import json

from ui_harness import Interface
from desktop.computer import USER, Rect


def verify():
    with Interface() as ui:
        controller = ui.orb.controller
        context = {'capabilities': ['get_jarvis_interface', 'move_jarvis_orb']}
        def call(name, **arguments):
            return controller.execute_tool(context, name, arguments)
        initial = call('get_jarvis_interface')
        assert initial['ok'], initial
        placements = []
        for monitor in initial['monitors']:
            result = call('move_jarvis_orb', display=str(monitor['number']))
            assert result.get('ok') and result.get('verified'), result
            assert result['orb']['monitor'] == monitor['id'], result
            assert ui.orb.settings['monitor'] == monitor['id']
            window = ui.windows()['orb']
            rect = Rect()
            assert USER.GetWindowRect(window['handle'], ctypes.byref(rect))
            center = ((rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2)
            left, top, right, bottom = monitor['rect']
            assert left <= center[0] < right and top <= center[1] < bottom, (monitor, center)
            placements.append({'monitor': monitor['number'], 'native_rect': [rect.left, rect.top, rect.right, rect.bottom], 'verified': True})
        before = ui.windows()['orb']['bounds']
        missing = call('move_jarvis_orb', display='999')
        assert not missing['ok'], missing
        assert ui.windows()['orb']['bounds'] == before
        ui.orb.events.put(('toggle_jarvis',))
        ui.wait(lambda: ui.orb.listener.hidden)
        hidden = call('move_jarvis_orb', display=initial['monitors'][0]['id'])
        assert hidden['ok'] and not hidden['orb']['visible'], hidden
        assert ui.orb.listener.hidden, 'Moving the orb changed listening state'
        print(json.dumps({'native_placements': placements, 'missing_monitor_rejected': True, 'hidden_state_preserved': True}), flush=True)


if __name__ == '__main__':
    verify()
