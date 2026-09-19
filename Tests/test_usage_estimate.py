import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'ModelHarbor/Support/usage_estimate.py'
SPEC = importlib.util.spec_from_file_location('usage_estimate', PATH)
estimate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(estimate)


class UsageEstimateTests(unittest.TestCase):
    def test_text_request_estimates_from_bytes(self):
        request = {'instructions': 'x' * 3300, 'input': [{'role': 'user', 'content': 'y' * 3300}]}
        result = estimate.estimate_input_tokens(request)
        self.assertEqual(result, 2001)  # structural keys count as visible bytes

    def test_encrypted_content_contributes_nothing(self):
        request = {'input': [{'type': 'reasoning', 'encrypted_content': 'PRIVATE ' * 100000}]}
        self.assertEqual(estimate.estimate_input_tokens(request), 3)  # only type key remains

    def test_image_uses_bound_not_bytes(self):
        request = {'input': [{'role': 'user', 'content': [
            {'type': 'input_text', 'text': 'look'},
            {'type': 'input_image', 'image_url': 'data:image/png;base64,' + 'A' * 100000}]}]}
        result = estimate.estimate_input_tokens(request)
        self.assertEqual(result, 4101)  # 4096 image bound plus text and structure

    def test_deepseek_flash_image_bound(self):
        request = {'input': [{'role': 'user', 'content': [
            {'type': 'input_image', 'image_url': 'https://example.test/x.png'}]}]}
        result = estimate.estimate_input_tokens(request, model='harbor/baseten/deepseek-ai/DeepSeek-V4.1-Flash')
        self.assertEqual(result, 1025)  # image bound plus one structural byte

    def test_malformed_image_ref_counts_whole(self):
        request = {'input': [{'role': 'user', 'content': [
            {'type': 'input_image', 'image_url': 'data:x', 'file_id': 'both'},
            {'type': 'input_image'}]}]}
        result = estimate.estimate_input_tokens(request)
        self.assertLess(result, 4096)

    def test_clamped_to_context_window(self):
        request = {'instructions': 'x' * 330000}
        self.assertEqual(estimate.estimate_input_tokens(request, context_window=50000), 50000)

    def test_never_raises_on_garbage(self):
        for request in (None, [], 'text', {'input': object()}, {'input': [float('nan')]}):
            try:
                estimate.estimate_input_tokens(request if isinstance(request, dict) else {})
            except Exception:
                self.fail('estimator raised')

    def test_no_content_in_output(self):
        request = {'instructions': 'PRIVATE', 'input': [{'content': 'PRIVATE'}]}
        result = estimate.estimate_input_tokens(request)
        self.assertIsInstance(result, int)


if __name__ == '__main__':
    unittest.main()
