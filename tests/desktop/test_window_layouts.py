import threading
import unittest
from unittest.mock import patch

from desktop.window_layouts import WindowLayouts, target_rect
from desktop.controller import capabilities_for
from tests.desktop.intent_fixtures import decision


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.screens = [{'device':'left', 'internal':False, 'primary':False, 'rect':[-1600,0,0,1000], 'work':[-1600,0,0,960]},
                        {'device':'laptop', 'internal':True, 'primary':True, 'rect':[0,0,1920,1080], 'work':[0,0,1920,1040]}]
        self.windows = [{'hwnd':11, 'pid':101, 'process':'chrome.exe', 'title':'Browser', 'rect':[20,20,820,620]},
                        {'hwnd':12, 'pid':102, 'process':'notepad.exe', 'title':'Notes', 'rect':[30,30,830,630]}]

    def test_geometry_handles_negative_monitors_and_excludes_taskbar(self):
        self.assertEqual(target_rect('left', self.screens[0], [], {}), [-1600,0,-800,960])
        self.assertEqual(target_rect('bottom_right', self.screens[1], [], {}), [960,520,1920,1040])
        self.assertEqual(target_rect('keep', self.screens[0], [20,20,820,620], self.screens[1]), [-1580,20,-780,620])
        self.assertEqual(capabilities_for(decision('list_layout_windows','arrange_windows'))[2], 'layout')
        self.assertEqual(capabilities_for(decision('arrange_windows'))[2], 'layout')

    def test_inventory_includes_apps_without_product_aliases(self):
        windows = self.windows + [
            {'hwnd':13,'pid':103,'process':'prismlauncher.exe','title':'Prism Launcher','rect':[0,0,800,600]},
            {'hwnd':14,'pid':104,'process':'taskmgr.exe','title':'Task Manager','rect':[0,0,800,600]}]
        screens = [{**self.screens[0], 'device':r'\\.\DISPLAY7'}, {**self.screens[1], 'device':r'\\.\DISPLAY2'}]
        with patch('desktop.window_layouts.require_desktop'), patch('desktop.window_layouts.USER'),              patch('desktop.window_layouts.monitors', return_value=screens),              patch('desktop.window_layouts.enumerate_windows', return_value=windows):
            result = WindowLayouts().listing({'ownerText':'Move Task Manager and Prism Launcher'})
        self.assertEqual([row['title'] for row in result['windows']], [row['title'] for row in windows])
        self.assertEqual([row['number'] for row in result['monitors']], [7,2])

    def test_stale_second_window_prevents_all_moves(self):
        context = {'capabilities':['arrange_windows'], 'ownerText':'Put Chrome on the left and notes on the right'}
        layout = WindowLayouts()
        with patch('desktop.window_layouts.require_desktop'), patch('desktop.window_layouts.USER') as user, \
             patch('desktop.window_layouts.monitors', return_value=self.screens), \
             patch('desktop.window_layouts.enumerate_windows', return_value=self.windows):
            listing = layout.listing(context)
            placements = [{'id':row['id'], 'position':side, 'monitor':'current'} for row,side in zip(listing['windows'], ['left','right'])]
            with patch('desktop.window_layouts.window_info', side_effect=[self.windows[0], {**self.windows[1], 'pid':999}]):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    layout.arrange(context, placements)
            user.SetWindowPos.assert_not_called()

    def test_success_requires_position_readback_and_respects_cancellation(self):
        context = {'capabilities':['arrange_windows'], 'ownerText':'Put Chrome on the left'}
        layout = WindowLayouts()
        with patch('desktop.window_layouts.require_desktop'), patch('desktop.window_layouts.USER') as user, \
             patch('desktop.window_layouts.monitors', return_value=self.screens), \
             patch('desktop.window_layouts.enumerate_windows', return_value=self.windows):
            listing = layout.listing(context)
            placement = [{'id':listing['windows'][0]['id'], 'position':'left', 'monitor':'laptop'}]
            moved = {**self.windows[0], 'rect':[0,0,960,1040]}
            with patch('desktop.window_layouts.window_info', side_effect=[self.windows[0],self.windows[0],moved]):
                self.assertTrue(layout.arrange(context, placement)['windows'][0]['verified'])
            cancelled = threading.Event(); cancelled.set()
            user.SetWindowPos.reset_mock()
            with patch('desktop.window_layouts.window_info', return_value=self.windows[0]):
                self.assertFalse(layout.arrange(context, placement, cancelled)['ok'])
            user.SetWindowPos.assert_not_called()
