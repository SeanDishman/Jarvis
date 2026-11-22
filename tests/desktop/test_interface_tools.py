import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from desktop.breaks import BreakState
from desktop.orb import Orb
from desktop.ui_bridge import UIBridge


SCREENS = [
    {'device': r'\\.\DISPLAY1', 'primary': True, 'rect': [0, 0, 1920, 1080], 'work': [0, 0, 1920, 1032]},
    {'device': r'\\.\DISPLAY2', 'primary': False, 'rect': [-1920, 52, 0, 1132], 'work': [-1920, 52, 0, 1084]},
]


class InterfaceToolTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)
        with patch('desktop.controller.BreakState', return_value=BreakState(self.path / 'break.json')):
            self.orb = Orb(interface=False)
        self.orb.settings_file = self.path / 'settings.json'
        self.orb.settings['monitor'] = SCREENS[0]['device']
        self.orb.ui = MagicMock()
        for service in (self.orb.controller.research, self.orb.controller.tasks, self.orb.controller.browser):
            self.addCleanup(service.close)
        self.saved = patch.object(self.orb, 'save_settings').start()
        self.addCleanup(patch.stopall)
        patch('desktop.orb.physical_monitors', return_value=SCREENS).start()
        patch('desktop.orb.diagnostic').start()
        self.current = SCREENS[0]['device']
        self.orb.ui.request.side_effect = self.interface

    def interface(self, method, params, timeout=5):
        if method == 'move_orb':
            self.current = params['screens']['selected']
        return {'orb': {'monitor': self.current, 'bounds': {'x': -137, 'y': 900, 'width': 132, 'height': 132},
                        'visible': True}, 'verified': method == 'move_orb'}

    def invoke(self, display=None, cancelled=None, deadline=None):
        result = queue.Queue()
        self.orb.on_interface_tool('get_jarvis_interface' if display is None else 'move_jarvis_orb',
                                  {} if display is None else {'display': display}, result,
                                  cancelled or threading.Event(), deadline or time.monotonic() + 8)
        return result.get_nowait()

    def test_inspection_reports_real_orb_monitor_and_available_numbers(self):
        result = self.invoke()
        self.assertTrue(result['ok'])
        self.assertEqual(result['orb']['monitor'], SCREENS[0]['device'])
        self.assertEqual([row['number'] for row in result['monitors']], [1, 2])
        self.saved.assert_not_called()

    def test_second_monitor_is_verified_before_persisting(self):
        result = self.invoke('2')
        self.assertTrue(result['ok'])
        self.assertTrue(result['verified'])
        self.assertEqual(result['orb']['monitor'], SCREENS[1]['device'])
        self.assertEqual(self.orb.settings['monitor'], SCREENS[1]['device'])
        self.saved.assert_called_once()

    def test_other_is_relative_to_actual_orb_after_dragging(self):
        self.current = SCREENS[1]['device']
        result = self.invoke('other')
        self.assertTrue(result['ok'])
        self.assertEqual(result['orb']['monitor'], SCREENS[0]['device'])

    def test_missing_or_ambiguous_monitor_never_moves_to_another(self):
        third = {'device': r'\\.\DISPLAY3', 'rect': [1920, 0, 3840, 1080]}
        for display in ('99', 'all', ''):
            with self.subTest(display=display):
                self.assertFalse(self.invoke(display)['ok'])
        with patch('desktop.orb.physical_monitors', return_value=SCREENS + [third]):
            self.assertFalse(self.invoke('other')['ok'])
        self.assertTrue(all(call.args[0] == 'inspect_interface' for call in self.orb.ui.request.call_args_list))
        self.saved.assert_not_called()

    def test_failed_confirmation_does_not_save_the_requested_monitor(self):
        self.orb.ui.request.return_value = {'orb': {'monitor': SCREENS[0]['device']}, 'verified': False}
        self.orb.ui.request.side_effect = None
        result = self.invoke('2')
        self.assertFalse(result['ok'])
        self.assertEqual(self.orb.settings['monitor'], SCREENS[0]['device'])
        self.saved.assert_not_called()

    def test_cancelled_and_expired_requests_cannot_move_the_orb_later(self):
        cancelled = threading.Event()
        cancelled.set()
        self.assertFalse(self.invoke('2', cancelled=cancelled)['ok'])
        self.assertFalse(self.invoke('2', deadline=time.monotonic() - 1)['ok'])
        self.orb.ui.request.assert_not_called()

    def test_tool_dispatch_waits_for_native_confirmation(self):
        controller = self.orb.controller
        result = []
        context = {'capabilities': ['get_jarvis_interface', 'move_jarvis_orb']}
        worker = threading.Thread(target=lambda: result.append(controller.execute_tool(context, 'move_jarvis_orb', {'display': '2'})))
        worker.start()
        try:
            event = controller.events.get(timeout=1)
            self.assertEqual(event[0], 'interface_tool')
            self.assertEqual(result, [])
            self.orb.handle(event)
            worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertTrue(result[0]['verified'])
        finally:
            controller.cancelled.set()
            worker.join(1)

    def test_tool_requires_a_grant_for_the_current_request(self):
        with self.assertRaises(ValueError):
            self.orb.controller.execute_tool({'capabilities': []}, 'move_jarvis_orb', {'display': '2'})
        self.orb.ui.request.assert_not_called()


class InterfaceRequestTests(unittest.TestCase):
    def bridge(self):
        bridge = UIBridge(lambda *_args: None, lambda: None)
        bridge.ready = True
        bridge.connected.set()
        return bridge

    def test_timeout_cleans_pending_requests(self):
        bridge = self.bridge()
        with patch.object(bridge, '_queue', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'confirm'):
                bridge.request('move_orb', {}, timeout=.01)
        self.assertEqual(bridge.pending, {})

    def test_disconnect_releases_a_waiting_request(self):
        bridge = self.bridge()
        connection = bridge.connection = MagicMock()
        sent = threading.Event()
        errors = []
        def request():
            try:
                bridge.request('inspect_interface', timeout=2)
            except RuntimeError as error:
                errors.append(str(error))
        with patch.object(bridge, '_queue', side_effect=lambda _message: sent.set() or True):
            worker = threading.Thread(target=request)
            worker.start()
            self.assertTrue(sent.wait(1))
            bridge._disconnect(connection)
            worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, ['The Jarvis interface disconnected'])
        self.assertEqual(bridge.pending, {})


if __name__ == '__main__':
    unittest.main()
