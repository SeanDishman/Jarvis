import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop import computer

TARGET = {'hwnd': 41, 'pid': 7, 'process': 'app.exe', 'title': 'App', 'rect': [100, 200, 900, 800]}
CHILD = 77


def frame(source='window'):
    # An 800 x 600 window shown at half size, so screenshot point (10, 20) is screen point (120, 240).
    return {'rect': TARGET['rect'], 'width': 400, 'height': 300, 'captured': time.monotonic(), 'source': source}


class FakeWindows:
    """Records the window messages Jarvis's own pointer sends; the real pointer must never move."""

    def __init__(self, covered=False, class_name='Notepad'):
        self.sent, self.posted, self.covered, self.class_name = [], [], covered, class_name

    def GetClassNameW(self, _hwnd, buffer, _size):
        buffer.value = self.class_name
        return len(self.class_name)

    def ScreenToClient(self, hwnd, point):
        origin = (100, 200) if hwnd == TARGET['hwnd'] else (150, 230)
        point._obj.x -= origin[0]
        point._obj.y -= origin[1]
        return True

    def ChildWindowFromPointEx(self, parent, _point, _flags):
        return CHILD if parent == TARGET['hwnd'] else parent

    def SendMessageTimeoutW(self, hwnd, message, wparam, lparam, _flags, _timeout, _result):
        self.sent.append((hwnd, message, wparam, lparam))
        return 1

    def PostMessageW(self, hwnd, message, wparam, lparam):
        self.posted.append((hwnd, message, wparam, lparam))
        return 1

    def WindowFromPoint(self, _point):
        return 999 if self.covered else TARGET['hwnd']

    def GetAncestor(self, hwnd, _flag):
        return hwnd

    def IsIconic(self, _hwnd):
        return False

    def IsWindow(self, hwnd):
        return hwnd == CHILD

    def MapVirtualKeyW(self, code, _kind):
        return 28 if code == 13 else 0

    def GetCursorPos(self, point):
        point._obj.x, point._obj.y = 500, 500
        return True

    def GetSystemMetrics(self, _index):
        return 0


class AgentPointerTests(unittest.TestCase):
    def setUp(self):
        self.windows = FakeWindows()
        for patcher in (patch.object(computer, 'USER', self.windows), patch.object(computer, 'require_desktop'),
                        patch.object(computer, 'window_info', return_value=dict(TARGET)),
                        patch.object(computer, 'controllable', return_value=True),
                        patch.object(computer, 'send_inputs', side_effect=AssertionError('The real pointer must not move'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_background_click_goes_to_the_control_under_the_point_without_the_real_pointer(self):
        child, spot = computer.background_pointer(TARGET, frame(), {'action': 'click', 'x': 10, 'y': 20})
        self.assertEqual((child, spot), (CHILD, (120, 240)))
        at = computer.pack_point(120 - 150, 240 - 230)
        self.assertEqual(self.windows.sent, [(CHILD, computer.WM_MOUSEMOVE, 0, at), (CHILD, computer.WM_LBUTTONDOWN, 1, at),
                                             (CHILD, computer.WM_LBUTTONUP, 0, at)])

    def test_double_right_click_and_scroll_send_their_own_messages(self):
        computer.background_pointer(TARGET, frame(), {'action': 'double_click', 'x': 10, 'y': 20})
        self.assertEqual([message for _, message, _, _ in self.windows.sent][-2:], [computer.WM_LBUTTONDBLCLK, computer.WM_LBUTTONUP])
        self.windows.sent.clear()
        computer.background_pointer(TARGET, frame(), {'action': 'right_click', 'x': 10, 'y': 20})
        self.assertEqual([message for _, message, _, _ in self.windows.sent][1:], [computer.WM_RBUTTONDOWN, computer.WM_RBUTTONUP])
        self.windows.sent.clear()
        computer.background_pointer(TARGET, frame(), {'action': 'scroll', 'x': 10, 'y': 20, 'scroll': -2})
        wheel = self.windows.sent[-1]
        self.assertEqual(wheel[1], computer.WM_MOUSEWHEEL)
        self.assertEqual(wheel[2] >> 16, (-240) & 0xffff)
        self.assertEqual(wheel[3], computer.pack_point(120, 240), 'The wheel carries screen coordinates')

    def test_a_covered_spot_in_a_screen_capture_is_refused(self):
        self.windows.covered = True
        with self.assertRaisesRegex(RuntimeError, 'covers that spot'):
            computer.background_pointer(TARGET, frame(source='screen'), {'action': 'click', 'x': 10, 'y': 20})
        self.assertEqual(self.windows.sent, [])
        # The app's own pixels show what is under the point, even while it is covered.
        computer.background_pointer(TARGET, frame(source='window'), {'action': 'click', 'x': 10, 'y': 20})
        self.assertTrue(self.windows.sent)

    def test_a_paused_browser_page_is_only_clicked_through_live_control_positions(self):
        # Chromium stops painting a fully covered window, so its screenshot may show an older page.
        self.windows.covered = True
        with self.assertRaisesRegex(RuntimeError, 'paused painting'):
            computer.background_pointer(TARGET, dict(frame(), paused=True), {'action': 'click', 'x': 10, 'y': 20})
        self.assertEqual(self.windows.sent, [])
        self.windows.covered = False
        computer.background_pointer(TARGET, dict(frame(), paused=True), {'action': 'click', 'x': 10, 'y': 20})
        self.assertTrue(self.windows.sent)

    def test_chromium_apps_are_not_scrolled_where_another_window_covers_them(self):
        self.windows.class_name = 'Chrome_WidgetWin_1'
        self.windows.covered = True
        with self.assertRaisesRegex(RuntimeError, 'scroll_into_view'):
            computer.background_pointer(TARGET, frame(), {'action': 'scroll', 'x': 10, 'y': 20, 'scroll': -2})
        self.assertEqual(self.windows.sent, [])
        self.windows.covered = False
        computer.background_pointer(TARGET, frame(), {'action': 'scroll', 'x': 10, 'y': 20, 'scroll': -2})
        self.assertEqual(self.windows.sent[-1][1], computer.WM_MOUSEWHEEL)
        # Other apps take the wheel message even while covered.
        self.windows.sent.clear()
        self.windows.class_name, self.windows.covered = 'Notepad', True
        computer.background_pointer(TARGET, frame(), {'action': 'scroll', 'x': 10, 'y': 20, 'scroll': 1})
        self.assertEqual(self.windows.sent[-1][1], computer.WM_MOUSEWHEEL)

    def test_full_cover_and_chromium_windows_are_recognised(self):
        self.assertFalse(computer.fully_covered(TARGET))
        self.windows.covered = True
        self.assertTrue(computer.fully_covered(TARGET))
        self.assertFalse(computer.chromium_window(TARGET['hwnd']))
        self.windows.class_name = 'Chrome_WidgetWin_1'
        self.assertTrue(computer.chromium_window(TARGET['hwnd']))

    def test_stale_or_missing_screenshots_are_refused(self):
        old = dict(frame(), captured=time.monotonic() - 120)
        for shot in (old, {'rect': TARGET['rect']}):
            with self.subTest(shot=shot), self.assertRaises(RuntimeError):
                computer.background_pointer(TARGET, shot, {'action': 'click', 'x': 10, 'y': 20})
        with self.assertRaisesRegex(RuntimeError, 'outside the screenshot'):
            computer.background_pointer(TARGET, frame(), {'action': 'click', 'x': 400, 'y': 20})

    def test_typing_and_single_keys_go_to_the_field_that_was_clicked(self):
        computer.background_keys(CHILD, {'action': 'type', 'text': 'hi\n'})
        self.assertEqual(self.windows.posted, [(CHILD, computer.WM_CHAR, ord('h'), 1), (CHILD, computer.WM_CHAR, ord('i'), 1),
                                               (CHILD, computer.WM_CHAR, 13, 1)])
        self.windows.posted.clear()
        computer.background_keys(CHILD, {'action': 'key', 'key': 'enter'})
        self.assertEqual([(message, code) for _, message, code, _ in self.windows.posted],
                         [(computer.WM_KEYDOWN, 13), (computer.WM_KEYUP, 13)])
        self.assertEqual(self.windows.posted[0][3], 1 | (28 << 16))
        for action in ({'action': 'key', 'key': 'ctrl+a'}, {'action': 'key', 'key': 'select_all'}):
            with self.subTest(action=action), self.assertRaisesRegex(RuntimeError, 'shortcuts need focus_window'):
                computer.background_keys(CHILD, action)
        with self.assertRaisesRegex(RuntimeError, 'Click the field'):
            computer.background_keys(None, {'action': 'type', 'text': 'hi'})

    def test_the_pointer_goes_to_a_listed_control_and_hides_when_asked(self):
        inspection = {'controls': [{'id': 'control:1:0', 'rect': [10, 20, 50, 60]}, {'id': 'control:1:1', 'rect': []}]}
        self.assertEqual(computer.control_spot(inspection, 'control:1:0'), (30, 40))
        self.assertIsNone(computer.control_spot(inspection, 'control:1:1'))
        self.assertIsNone(computer.control_spot(None, 'control:1:0'))
        control = computer.ComputerControl(SimpleNamespace(apps=[]))
        shown = []
        control.pointer = lambda **row: shown.append(row)
        with patch.object(computer.time, 'sleep') as pause:
            control.point((30, 40), 'click_control')
            control.point(None, 'click')
        control.hide_pointer()
        self.assertEqual(shown, [{'x': 30, 'y': 40, 'action': 'click_control'}, {'hide': True}])
        pause.assert_called_once_with(computer.POINTER_GLIDE_SECONDS)


if __name__ == '__main__':
    unittest.main()
