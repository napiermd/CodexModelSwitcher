import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'ModelHarbor/Support/tool_result_aging.py'
SPEC = importlib.util.spec_from_file_location('tool_result_aging', PATH)
aging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aging)


def result_item(size, call_id='c1'):
    return {'type': 'function_call_output', 'call_id': call_id, 'output': 'x' * size}


def acted_history(*items):
    """Append enough assistant activity that the listed results precede a
    model action, plus four fresh results for the protected frontier."""
    return list(items) + [{'type': 'message', 'role': 'assistant', 'content': 'used it'}] + [
        result_item(100, f'fresh{i}') for i in range(4)]


class ToolResultAgingTests(unittest.TestCase):
    def test_old_acted_on_large_result_is_eligible(self):
        items = acted_history(result_item(40000))
        report = aging.estimate(items)
        self.assertEqual(report['eligible'], 1)
        self.assertGreater(report['receipt_savings'], 30000)
        self.assertEqual(report['results'][0]['call_id'], 'c1')

    def test_floor_and_frontier_protect(self):
        items = [result_item(31000), {'type': 'message', 'role': 'assistant', 'content': 'ok'}]
        items += [result_item(40000, f'c{i}') for i in range(4)]
        report = aging.estimate(items)
        self.assertEqual(report['eligible'], 0)

    def test_unacted_result_is_protected(self):
        items = [result_item(40000)]
        self.assertEqual(aging.estimate(items)['eligible'], 0)

    def test_non_textual_output_skipped(self):
        items = [{'type': 'function_call_output', 'call_id': 'c1',
                  'output': [{'type': 'input_image', 'image_url': 'x'}]},
                 {'type': 'message', 'role': 'assistant', 'content': 'ok'}]
        self.assertEqual(aging.estimate(items)['eligible'], 0)

    def test_shaping_collapses_repeated_lines(self):
        repeated = '\n'.join(['same line'] * 50 + ['error: keep me'])
        estimate = aging._dense_shaped(repeated)
        self.assertIn('repeated 49 more times', estimate)
        self.assertIn('error: keep me', estimate)

    def test_terminal_rewrites_collapse_but_keep_errors(self):
        value = 'progress 10%\rprogress 20%\rprogress 30%\nerror: failed here\r\n'
        shaped = aging._dense_shaped(value)
        self.assertIn('progress 30%', shaped)
        self.assertIn('error: failed here', shaped)

    def test_input_is_never_mutated(self):
        items = [result_item(40000), {'type': 'message', 'role': 'assistant', 'content': 'ok'}]
        before = copy.deepcopy(items)
        aging.estimate(items)
        self.assertEqual(items, before)

    def test_report_contains_no_content(self):
        items = acted_history({'type': 'function_call_output', 'call_id': 'c1', 'output': 'PRIVATE ' * 10000})
        report = aging.estimate(items)
        import json
        self.assertNotIn('PRIVATE', json.dumps(report))


if __name__ == '__main__':
    unittest.main()
