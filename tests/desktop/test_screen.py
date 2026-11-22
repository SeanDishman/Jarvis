import queue
import unittest
from unittest.mock import patch

from desktop.controller import Controller
from desktop.screen import capture_screen


class ScreenTests(unittest.TestCase):
    def test_screen_question_takes_one_snapshot_with_no_action_tools(self):
        controller = Controller(queue.Queue())
        frame = {'image':'fixture-png', 'width':100, 'height':100}
        response = {'output':[{'type':'message', 'content':[{'type':'output_text', 'text':'A blue dialog is visible, sir.'}]}]}
        with patch('desktop.controller.classify_request', return_value={'action':'screen','display':'current'}), \
             patch('desktop.controller.capture_screen', return_value=frame) as capture, \
             patch('desktop.controller.local_request', return_value=response) as request, \
             patch.object(controller.research, 'start') as start:
            result = controller.respond('Can you see what I am looking at on my display?', speak=False)
        self.assertTrue(result['ok'])
        self.assertEqual(result['tools'], ['view_screen'])
        capture.assert_called_once_with('current', None)
        start.assert_not_called()
        payload = request.call_args.args[2]
        self.assertEqual(payload['model'], 'openclaw/screen')
        self.assertEqual(payload['image'], 'fixture-png')
        self.assertNotIn('capabilities', payload)
        self.assertEqual(controller.last_answer_domain, 'screen')

    def test_unavailable_desktop_is_reported_without_model_or_fake_snapshot(self):
        controller = Controller(queue.Queue())
        with patch('desktop.controller.classify_request', return_value={'action':'screen'}), \
             patch('desktop.screen.desktop_name', return_value='restricted'), \
             patch('desktop.controller.local_request') as model:
            result = controller.respond('Look at my screen', speak=False)
        self.assertFalse(result['ok'])
        self.assertIn('normal Windows desktop', result['error'])
        model.assert_not_called()

    def test_current_display_follows_active_window_and_includes_taskbar(self):
        screens = [{'rect':[0,0,1920,1080], 'work':[0,0,1920,1040], 'primary':True,'internal':True,'device':'laptop'},
                   {'rect':[-1600,0,0,1200], 'work':[-1600,0,0,1160], 'primary':False,'internal':False,'device':'external'}]
        with patch('desktop.screen.desktop_name', return_value='default'), patch('desktop.screen.monitors', return_value=screens), \
             patch('desktop.screen.window_info', return_value={'rect':[-1400,100,-100,900]}), \
             patch('desktop.screen.USER'), patch('desktop.screen.capture_rect', return_value={'image':'png'}) as capture:
            self.assertEqual(capture_screen()['display'], 'external')
            capture.assert_called_once_with([-1600,0,0,1200])
            capture.reset_mock()
            self.assertEqual(capture_screen('all')['display'], 'All displays')
            capture.assert_called_once_with([-1600,0,1920,1200])


if __name__ == '__main__':
    unittest.main()
