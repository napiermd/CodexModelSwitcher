import importlib.util
import json
import pathlib
import unittest

path = pathlib.Path(__file__).parents[1] / 'ModelHarbor/Support/grok_adapter.py'
spec = importlib.util.spec_from_file_location('grok_adapter', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def completed(output):
    return {'type': 'response.completed',
            'response': {'id': 'resp_1', 'status': 'completed', 'output': output}}


class InvalidFunctionCallTests(unittest.TestCase):
    def translation(self):
        return module.Translation({'model': 'grok-4.6', 'input': [], 'tools': []})

    def event(self, translation, payload):
        block = ('data: ' + json.dumps(payload) + '\n\n').encode()
        return translation.event(block)

    def test_invalid_arguments_fail_the_turn(self):
        translation = self.translation()
        result = self.event(translation, completed([
            {'type': 'function_call', 'name': 'read_file', 'call_id': 'c1',
             'arguments': '{"path": "unterminated'}]))
        event = json.loads(result.decode().split('data: ', 1)[1])
        self.assertEqual(event['type'], 'response.failed')
        self.assertEqual(event['response']['error']['code'], 'invalid_function_call_arguments')

    def test_valid_arguments_pass_byte_identical(self):
        translation = self.translation()
        payload = completed([
            {'type': 'function_call', 'name': 'read_file', 'call_id': 'c1',
             'arguments': '{"path": "x"}'}])
        result = self.event(translation, payload)
        self.assertEqual(result, ('data: ' + json.dumps(payload) + '\n\n').encode() + b'\n\n')

    def test_empty_arguments_are_allowed(self):
        translation = self.translation()
        result = self.event(translation, completed([
            {'type': 'function_call', 'name': 'noop', 'call_id': 'c1', 'arguments': ''}]))
        self.assertNotIn('response.failed', result.decode())

    def test_custom_codec_calls_are_exempt(self):
        translation = module.Translation({'model': 'grok-4.6', 'input': [], 'tools': [
            {'type': 'custom', 'name': 'apply_patch',
             'description': 'patch', 'format': {'type': 'grammar', 'syntax': 'lark', 'definition': 'x'}}]})
        self.assertTrue(module.valid_function_arguments(
            {'type': 'function_call', 'name': 'apply_patch', 'arguments': '*** Begin Patch not json'}, translation.names))

    def test_history_side_scan_rejects_poisoned_input(self):
        self.assertFalse(module.valid_function_arguments(
            {'type': 'function_call', 'name': 'read', 'arguments': '{bad'}, {}))
        self.assertTrue(module.valid_function_arguments(
            {'type': 'function_call', 'name': 'read', 'arguments': '{"ok": 1}'}, {}))

    def test_output_item_done_with_invalid_arguments_flags_completion(self):
        translation = self.translation()
        payload = {'type': 'response.output_item.done', 'item': {
            'type': 'function_call', 'name': 'read', 'call_id': 'c1', 'arguments': '{bad'}}
        translation.event(('data: ' + json.dumps(payload) + '\n\n').encode())
        result = self.event(translation, completed([
            {'type': 'function_call', 'name': 'read', 'call_id': 'c1', 'arguments': '{bad'}]))
        self.assertIn('invalid_function_call_arguments', result.decode())


if __name__ == '__main__':
    unittest.main()
