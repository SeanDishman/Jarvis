from tests.desktop.intent_fixtures import decision
import queue
import unittest
from unittest.mock import patch

from desktop.controller import Controller


class AutoReasoningTests(unittest.TestCase):
    def test_ai_selected_tier_is_used_for_the_answer(self):
        for tier in ('sol', 'astra'):
            with self.subTest(tier=tier):
                controller = Controller(queue.Queue())
                response = {'model':'openai/gpt-6-' + tier, 'reasoning':'high', 'output':[{'type':'message', 'content':[{'type':'output_text', 'text':'Here is the explanation, sir.'}]}]}
                with patch('desktop.controller.classify_request', return_value={'action':'normal', 'model_tier':tier}), \
                     patch('desktop.controller.local_request', return_value=response) as model:
                    result = controller.respond('Help me understand the tradeoffs', speak=False)
                self.assertEqual(model.call_args.args[2]['modelTier'], tier)
                self.assertEqual(result['model'], 'openai/gpt-6-' + tier)
                self.assertEqual(controller.last_model, result['model'])

    def test_ambiguous_action_goes_to_stronger_model_with_no_tools(self):
        controller = Controller(queue.Queue())
        decision = {'action':'normal', 'model_tier':'astra', 'needs_clarification':True}
        response = {'output':[{'type':'message', 'content':[{'type':'output_text', 'text':'Which item do you mean, sir?'}]}]}
        with patch('desktop.controller.classify_request', return_value=decision), \
             patch.object(controller.research, 'start') as start, \
             patch('desktop.controller.local_request', return_value=response) as model:
            result = controller.respond('Click that and turn it off', speak=False)
        self.assertTrue(result['ok'])
        start.assert_not_called()
        payload = model.call_args.args[2]
        # Nothing that operates the computer; only the read-only web search, so an unknown name can be looked up.
        self.assertEqual(payload['capabilities'], ['start_web_search', 'web_search_status'])
        self.assertIn('never say you have no web search', payload['input'])
        self.assertEqual(payload['modelTier'], 'astra')

    def test_background_task_receives_the_selected_tier(self):
        controller = Controller(queue.Queue())
        controller.tasks = None
        def model(*_args):
            result = controller.tool(controller.request['id'], 'start_web_search', {'question':'Competing explanations'})
            self.assertTrue(result['ok'], result)
            return {'output':[{'type':'message','content':[{'type':'output_text','text':'Research started.'}]}]}
        with patch('desktop.controller.classify_request', return_value=decision('start_web_search', model_tier='astra')),              patch.object(controller, 'ask_model', side_effect=model),              patch.object(controller.research, 'start', return_value={'ok':True}) as start:
            self.assertTrue(controller.respond('Research the competing explanations for this experiment', speak=False)['ok'])
        self.assertEqual(start.call_args.kwargs['model_tier'], 'astra')


if __name__ == '__main__':
    unittest.main()
