from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop import window_moves
from desktop.window_moves import (HWND_BOTTOM, HWND_NOTOPMOST, HWND_TOPMOST, SC_MAXIMIZE, SWP_NOACTIVATE, WM_SYSCOMMAND,
                                  place_window, resting_place)

HWND, OWNER = 41, 77
MAIN = {'device': 'DISPLAY1', 'rect': [0, 0, 1920, 1080], 'work': [0, 0, 1920, 1032], 'primary': True}
SIDE = {'device': 'DISPLAY2', 'rect': [-1920, 52, 0, 1132], 'work': [-1920, 52, 0, 1084]}
SCREENS = [MAIN, SIDE]


class FakeDesktop:
    """One window as Windows keeps it. Only calls that leave keyboard focus alone exist here, so anything that
    would activate the window, such as SetForegroundWindow, ShowWindow or SendInput, fails the test outright."""

    def __init__(self, rect=(100, 100, 900, 700), state='normal', topmost=False, smallest=0, maximizes=True):
        self.rect, self.normal, self.state = list(rect), list(rect), state
        self.restores_maximized = False
        self.topmost, self.smallest, self.maximizes = topmost, smallest, maximizes
        self.calls = []

    def window(self):
        rect = [-32000, -32000, -31840, -31972] if self.state == 'minimized' else self.rect
        return {'hwnd': HWND, 'pid': 7, 'process': 'app.exe', 'title': 'App', 'rect': rect[:]}

    def IsIconic(self, _hwnd):
        return self.state == 'minimized'

    def IsZoomed(self, _hwnd):
        return self.state == 'maximized'

    def GetWindowPlacement(self, _hwnd, pointer):
        data = pointer._obj
        data.flags = 2 if self.restores_maximized else 0
        data.normal.left, data.normal.top, data.normal.right, data.normal.bottom = self.normal
        return 1

    def SetWindowPlacement(self, _hwnd, pointer):
        data = pointer._obj
        self.calls.append(('placement', data.flags, data.show))
        if data.show == 4:
            self.state, self.rect = 'normal', self.normal[:]
            if self.activates_on_restore:
                self.foreground = HWND
        return 1

    def SetWindowPos(self, _hwnd, after, x, y, width, height, flags):
        after = getattr(after, 'value', after)
        after = None if after is None else after - (1 << 64) if after > 1 << 63 else after
        self.calls.append(('position', after, flags))
        if not flags & window_moves.SWP_NOMOVE:
            width = max(width, self.smallest)
            self.rect = self.normal = [x, y, x + width, y + height]
        return 1

    def ShowWindowAsync(self, _hwnd, command):
        self.calls.append(('show', command))
        if command == 4 and self.state == 'maximized':
            self.state, self.rect = 'normal', self.normal[:]
        return 1

    def PostMessageW(self, _hwnd, message, wparam, _lparam):
        self.calls.append(('post', message, wparam))
        if (message, wparam) == (WM_SYSCOMMAND, SC_MAXIMIZE) and self.maximizes:
            screen = max(SCREENS, key=lambda row: window_moves.shared_area(self.rect, [row]))
            left, top, right, bottom = screen['work']
            self.state, self.rect = 'maximized', [left - 8, top - 8, right + 8, bottom + 8]
        return 1

    def GetWindowLongPtrW(self, _hwnd, _index):
        return window_moves.WS_EX_TOPMOST if self.topmost else 0

    def GetDpiForWindow(self, _hwnd):
        return 96

    # Keyboard focus starts with the owner's window. activates_on_restore makes the app grab it as it comes back.
    foreground, input_tick, activates_on_restore = OWNER, 1000, False

    def GetForegroundWindow(self):
        return self.foreground

    def GetLastInputInfo(self, pointer):
        pointer._obj.time = self.input_tick
        return 1

    def IsWindow(self, hwnd):
        return hwnd in (HWND, OWNER)

    def WindowFromPoint(self, _point):
        return HWND

    def GetAncestor(self, hwnd, _flag):
        return hwnd

    def MonitorFromRect(self, _rect, _flags):
        return 0


def display(window, screens):
    return max(screens, key=lambda row: window_moves.shared_area(window['rect'], [row]))


class WindowMoveTests(unittest.TestCase):
    def desktop(self, **state):
        fake = FakeDesktop(**state)
        now = [0.0]
        clock = SimpleNamespace(monotonic=lambda: now[0], sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        for patcher in (patch.object(window_moves, 'USER', fake), patch.object(window_moves, 'time', clock),
                        patch.object(window_moves, 'require_desktop'), patch.object(window_moves, 'check_pointer_stop'),
                        patch.object(window_moves, 'window_info', side_effect=lambda _hwnd: fake.window()),
                        patch.object(window_moves, 'window_display', side_effect=display),
                        patch('desktop.computer.bring_to_front', side_effect=lambda hwnd: (fake.calls.append(('focus', hwnd)),
                                                                                         setattr(fake, 'foreground', hwnd))),
                        patch.object(window_moves, 'caption_buttons',
                                     side_effect=lambda window: [window['rect'][2] - 150, window['rect'][1], window['rect'][2] - 6, window['rect'][1] + 30])):
            patcher.start()
            self.addCleanup(patcher.stop)
        return fake

    def place(self, fake, left, top, right, bottom, maximized=False, point=None):
        return place_window(fake.window(), {'left': left, 'top': top, 'right': right, 'bottom': bottom, 'maximized': maximized},
                            SCREENS, point=point)

    def test_a_window_is_dragged_by_its_title_bar_and_lifted_without_taking_focus(self):
        fake = self.desktop()
        pointer = []
        result = self.place(fake, -1800, 200, -600, 1000, point=lambda spot, action, wait=True: pointer.append((spot, action, wait)))
        self.assertEqual((result['verified'], result['state'], result['rect'], result['monitor']),
                         (True, 'moved', [-1800, 200, -600, 1000], 'DISPLAY2'))
        positions = [call for call in fake.calls if call[0] == 'position']
        self.assertTrue(all(flags & SWP_NOACTIVATE for _, _, flags in positions), 'No step may activate the window')
        lifts = [HWND_TOPMOST, HWND_NOTOPMOST]
        self.assertEqual([after for _, after, _ in positions[:2]], lifts, 'Lifted above the others first')
        self.assertEqual(len(positions) - 4, window_moves.DRAG_FRAMES, 'Then dragged there frame by frame')
        self.assertEqual([after for _, after, _ in positions[-2:]], lifts, 'And lifted again once it is in place')
        # It held the bar 325 of 800 pixels along, and keeps that share of the wider window it ends on.
        self.assertEqual(pointer, [((425, 115), 'grab', True), ((-1312, 215), 'drop', False)],
                         'The pointer holds the title bar, then travels with the window')

    def test_a_window_minimized_while_maximized_comes_back_from_the_bottom_of_the_stack(self):
        # Restored any other way, such a window takes keyboard focus.
        fake = self.desktop(state='minimized')
        fake.restores_maximized = True
        result = self.place(fake, 200, 100, 1000, 700)
        self.assertEqual(fake.calls[:3], [('position', HWND_BOTTOM, 0x13), ('placement', 0, 4), ('position', HWND_BOTTOM, 0x13)])
        self.assertNotIn('show', [call[0] for call in fake.calls])
        self.assertEqual((result['verified'], result['rect'], result['maximized']), (True, [200, 100, 1000, 700], False))

    def test_focus_an_app_grabs_while_it_comes_back_goes_straight_back_to_the_owner(self):
        fake = self.desktop(state='minimized')
        fake.restores_maximized, fake.activates_on_restore = True, True
        result = self.place(fake, 200, 100, 1000, 700)
        self.assertEqual((fake.foreground, result['focusReturned']), (OWNER, True))
        focus = fake.calls.index(('focus', OWNER))
        self.assertEqual(fake.calls[-2:], [('position', HWND_TOPMOST, 0x13), ('position', HWND_NOTOPMOST, 0x13)],
                         'The moved window is lifted again after the owner gets focus back')
        self.assertGreater(len(fake.calls) - 2, focus)

    def test_a_window_the_owner_chose_meanwhile_keeps_focus(self):
        fake = self.desktop(state='minimized')
        fake.activates_on_restore = True
        original = fake.SetWindowPlacement
        def owner_clicks(hwnd, pointer):
            fake.input_tick += 1  # the owner clicked the window as it came back
            return original(hwnd, pointer)
        fake.SetWindowPlacement = owner_clicks
        result = self.place(fake, 200, 100, 1000, 700)
        self.assertEqual(fake.foreground, HWND)
        self.assertNotIn('focusReturned', result)
        self.assertNotIn('focus', [call[0] for call in fake.calls])

    def test_a_maximized_place_takes_the_window_to_that_monitor_and_maximizes_it_there(self):
        fake = self.desktop(state='maximized')
        fake.rect = [-8, -8, 1928, 1040]
        result = self.place(fake, -1928, 44, 8, 1092, maximized=True)
        self.assertEqual(fake.calls[0], ('show', 4), 'A maximized window is first returned to its ordinary size')
        self.assertEqual(fake.calls[-3], ('post', WM_SYSCOMMAND, SC_MAXIMIZE))
        self.assertEqual((result['verified'], result['state'], result['monitor'], result['rect']),
                         (True, 'maximized', 'DISPLAY2', [-1928, 44, 8, 1092]))
        self.assertEqual(fake.normal, [-1728, 155, -192, 981], 'It keeps an ordinary size on that monitor to return to')

    def test_a_window_already_maximized_there_is_only_lifted(self):
        fake = self.desktop(state='maximized')
        fake.rect = [-8, -8, 1928, 1040]
        result = self.place(fake, 0, 0, 1920, 1032, maximized=True)
        self.assertEqual([call[1] for call in fake.calls], [HWND_TOPMOST, HWND_NOTOPMOST])
        self.assertTrue(result['verified'])

    def test_an_app_that_will_not_maximize_or_keeps_its_own_size_is_reported_as_it_is(self):
        fake = self.desktop(maximizes=False)
        result = self.place(fake, 0, 0, 1920, 1032, maximized=True)
        self.assertEqual((result['verified'], result['state']), (False, 'not_maximized'))
        self.assertIn('did not maximize', result['error'])
        fake = self.desktop(smallest=900)
        result = self.place(fake, 300, 200, 800, 700)
        self.assertEqual((result['verified'], result['rect']), (True, [300, 200, 1200, 700]))
        self.assertIn('size limits', result['note'])

    def test_an_always_on_top_window_keeps_that_status(self):
        fake = self.desktop(topmost=True)
        self.place(fake, 300, 200, 1100, 800)
        self.assertNotIn(HWND_NOTOPMOST, [call[1] for call in fake.calls if call[0] == 'position'])

    def test_places_off_the_displays_too_small_or_incomplete_are_refused_before_anything_moves(self):
        fake = self.desktop()
        cases = [({'left': 5000, 'top': 0, 'right': 5800, 'bottom': 600, 'maximized': False}, 'off the connected displays'),
                 ({'left': 0, 'top': 0, 'right': 60, 'bottom': 40, 'maximized': False}, 'too small'),
                 ({'left': 0, 'top': 0, 'right': 800, 'bottom': 600}, 'whether to maximize'),
                 ({'left': '0', 'top': 0, 'right': 800, 'bottom': 600, 'maximized': False}, 'screen pixels')]
        for place, message in cases:
            with self.subTest(place=place), self.assertRaisesRegex(RuntimeError, message):
                place_window(fake.window(), place, SCREENS)
        self.assertEqual(fake.calls, [])

    def test_where_a_window_is_or_returns_to(self):
        fake = self.desktop()
        self.assertEqual(resting_place(fake.window(), SCREENS), {'rect': [100, 100, 900, 700], 'maximized': False, 'monitor': 'DISPLAY1'})
        fake.state, fake.restores_maximized, fake.normal = 'minimized', True, [-1500, 300, -700, 900]
        with patch.object(window_moves, 'window_display', return_value=SIDE):
            self.assertEqual(resting_place(fake.window(), SCREENS),
                             {'rect': [-1500, 300, -700, 900], 'maximized': True, 'monitor': 'DISPLAY2'})

    def test_the_pointer_aims_at_the_real_buttons(self):
        fake = self.desktop()
        window = fake.window()
        # The window manager reports the button strip; minimize, maximize and close share it equally.
        self.assertEqual(window_moves.button_spot(window, 'minimize'), (774, 115))
        self.assertEqual(window_moves.button_spot(window, 'close'), (870, 115))
        self.assertEqual(window_moves.title_spot(window), (425, 115))
        with patch.object(window_moves, 'caption_buttons', return_value=[860, 100, 894, 130]):
            self.assertEqual(window_moves.button_spot(window, 'close'), (877, 115), 'A dialog has a lone close button')
            self.assertEqual(window_moves.button_spot(window, 'minimize'), window_moves.title_spot(window))
        fake.state = 'minimized'
        self.assertIsNone(window_moves.title_spot(fake.window()))


if __name__ == '__main__':
    unittest.main()
