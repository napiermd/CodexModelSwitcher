import copy
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

path = pathlib.Path(__file__).parents[1] / 'ModelHarbor/Support/grok_adapter.py'
spec = importlib.util.spec_from_file_location('image_adapter', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.image = {'type': 'input_image', 'image_url': 'data:image/png;base64,exact-bytes', 'detail': 'high'}
        self.tool = {'type': 'function_call_output', 'call_id': 'screen123', 'output': [
            {'type': 'input_text', 'text': 'Accessibility tree'}, self.image]}

    def test_tool_image_preserves_bytes_text_and_call_association(self):
        original = copy.deepcopy(self.tool)
        result = module.baseten_tool_images([self.tool])
        self.assertEqual(self.tool, original)
        self.assertEqual(result[0]['call_id'], 'screen123')
        self.assertEqual(result[0]['output'][0]['text'], 'Accessibility tree')
        self.assertEqual(result[1]['role'], 'user')
        self.assertIn('screen123', result[1]['content'][0]['text'])
        self.assertIn('tool result data', result[1]['content'][0]['text'])
        self.assertEqual(result[1]['content'][1], self.image)

    def test_parallel_outputs_stay_together_before_image_message(self):
        second = {'type': 'function_call_output', 'call_id': 'text123', 'output': 'other result'}
        continuation = {'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Continue'}]}
        result = module.baseten_tool_images([self.tool, second, continuation])
        self.assertEqual(result[1], second)
        self.assertEqual(result[2]['content'][1], self.image)
        self.assertEqual(result[3], continuation)

    def test_custom_tools_and_multiple_images_are_preserved(self):
        self.tool['type'] = 'custom_tool_call_output'
        self.tool['output'].append(dict(self.image, image_url='https://example.test/image.png'))
        result = module.baseten_tool_images([self.tool])
        self.assertEqual(len([p for p in result[1]['content'] if p['type'] == 'input_image']), 2)
        self.assertEqual(result[0]['type'], 'custom_tool_call_output')

    def test_user_images_and_text_only_results_are_unchanged(self):
        items = [{'role': 'user', 'content': [self.image]}, {'type': 'function_call_output', 'call_id': 'c1', 'output': 'text'}]
        self.assertEqual(module.baseten_tool_images(items), items)
        self.assertEqual(module.baseten_tool_images('hello'), 'hello')

    def test_translation_is_idempotent_and_runs_only_on_baseten(self):
        result = module.baseten_tool_images([self.tool])
        self.assertEqual(module.baseten_tool_images(result), result)
        for provider in ['baseten', 'grok-oauth']:
            with patch.object(module, 'route_for_turn', return_value={'provider': provider, 'model': 'test'}), patch.object(module, 'baseten_headers', return_value={}), patch.object(module, 'oauth_headers', return_value={}):
                translated = module.routed_request({'model': 'test', 'input': [self.tool]}, {})[0].request['input']
                self.assertEqual(len(translated), 2 if provider == 'baseten' else 1)
