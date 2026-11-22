import ctypes
import queue
import unittest
from unittest.mock import Mock

from desktop.hotkey import (InsertHotkey, KeyboardEvent, VK_INSERT, WM_KEYDOWN,
                            WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)


class InsertHotkeyTests(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.hotkey = InsertHotkey(self.events)
        self.hotkey._user = Mock()
        self.hotkey._user.CallNextHookEx.return_value = 91

    def send(self, message, key=VK_INSERT, code=0):
        event = KeyboardEvent(vkCode=key)
        return self.hotkey._handle(code, message, ctypes.addressof(event))

    def test_insert_consumed_and_toggles_once_per_press(self):
        self.assertEqual(self.send(WM_KEYDOWN), 1)
        for _ in range(20):
            self.assertEqual(self.send(WM_KEYDOWN), 1)
        self.assertEqual(self.send(WM_KEYUP), 1)
        self.assertEqual(self.events.get_nowait(), ('toggle_jarvis',))
        self.assertTrue(self.events.empty())
        self.assertEqual(self.send(WM_KEYDOWN), 1)
        self.assertEqual(self.events.get_nowait(), ('toggle_jarvis',))
        self.hotkey._user.CallNextHookEx.assert_not_called()

    def test_alt_insert_and_release_are_consumed_too(self):
        self.assertEqual(self.send(WM_SYSKEYDOWN), 1)
        self.assertEqual(self.send(WM_SYSKEYDOWN), 1)
        self.assertEqual(self.send(WM_SYSKEYUP), 1)
        self.assertEqual(self.events.get_nowait(), ('toggle_jarvis',))
        self.assertTrue(self.events.empty())
        self.hotkey._user.CallNextHookEx.assert_not_called()

    def test_unrelated_keys_and_numpad_zero_pass_through(self):
        for key in (0x41, 0x10, 0x11, 0x12, 0x60):
            self.assertEqual(self.send(WM_KEYDOWN, key), 91)
            self.assertEqual(self.send(WM_KEYUP, key), 91)
        self.assertTrue(self.events.empty())

    def test_negative_hook_code_passes_without_reading_pointer(self):
        self.assertEqual(self.hotkey._handle(-1, WM_KEYDOWN, 0), 91)
        self.hotkey._user.CallNextHookEx.assert_called_once_with(None, -1, WM_KEYDOWN, 0)
        self.assertTrue(self.events.empty())

    def test_quit_releases_insert_behavior(self):
        self.hotkey.stop()
        self.assertEqual(self.send(WM_KEYDOWN), 91)
        self.assertEqual(self.send(WM_KEYUP), 91)
        self.assertTrue(self.events.empty())

    def test_status_does_not_claim_access_to_users_desktop(self):
        self.hotkey.active = True
        self.hotkey.desktop = 'CodexSandboxDesktop-test'
        self.assertFalse(self.hotkey.status()['availableOnUserDesktop'])
        self.hotkey.desktop = 'Default'
        self.assertTrue(self.hotkey.status()['availableOnUserDesktop'])


if __name__ == '__main__':
    unittest.main()
