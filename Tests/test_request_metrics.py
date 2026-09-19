import importlib.util
import json
from pathlib import Path
import threading
import unittest

PATH = Path(__file__).parents[1] / 'ModelHarbor/Support/request_metrics.py'
SPEC = importlib.util.spec_from_file_location('request_metrics', PATH)
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


class RequestMetricsTests(unittest.TestCase):
    def fixture(self):
        return {'model': 'route/model', 'instructions': 'PRIVATE instructions',
                'reasoning': {'effort': 'high'},
                'tools': [{'type': 'function', 'name': 'PRIVATE tool', 'parameters': {'type': 'object'}}],
                'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'PRIVATE clinical'},
                           {'type': 'input_image', 'image_url': 'data:image/png;base64,PRIVATE'}]},
                          {'type': 'reasoning', 'encrypted_content': 'PRIVATE cipher'},
                          {'type': 'function_call_output', 'call_id': 'PRIVATE id', 'output': 'PRIVATE output'}]}

    def test_measurement_is_content_free_and_does_not_change_wire_bytes(self):
        source = self.fixture()
        before = json.dumps(source, separators=(',', ':')).encode()
        result = metrics.measure(source, source, {'provider': 'azure', 'model': 'route/model'}, before)
        after = json.dumps(source, separators=(',', ':')).encode()
        self.assertEqual(before, after)
        self.assertEqual(result['wire_bytes'], len(before))
        self.assertEqual(result['tool_count'], 1)
        self.assertEqual(result['images'], 1)
        self.assertEqual(result['encrypted_items'], 1)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('estimated_tokens', result)

    def test_unknown_item_type_cannot_become_a_metric_key(self):
        source = {'input': [{'type': 'PRIVATE TYPE', 'content': 'PRIVATE'}]}
        result = metrics.measure(source, source, {}, b'{}')
        self.assertEqual(result['input_shape'], {'unknown': {'items': 1, 'serialized_bytes': 43}})
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_usage_keeps_cached_input_as_a_subset_and_records_failures(self):
        recorder = metrics.Recorder(clock=lambda: 10)
        source = self.fixture()
        wire = json.dumps(source, separators=(',', ':')).encode()
        handle = recorder.begin(source, source, {'provider': 'azure', 'model': 'fixture'}, wire)
        recorder.finish(handle, 'completed', {'input_tokens': 100, 'cached_input_tokens': 80,
                                              'output_tokens': 5, 'total_tokens': 105,
                                              'PRIVATE': 999})
        result = recorder.snapshot()[0]
        self.assertEqual(result['usage'], {'input_tokens': 100, 'cached_input_tokens': 80,
                                           'output_tokens': 5, 'total_tokens': 105})

    def test_usage_normalizes_nested_responses_cached_tokens(self):
        recorder = metrics.Recorder()
        handle = recorder.begin({'input': []}, {'input': []}, {}, b'{}')
        recorder.finish(handle, 'completed', {'input_tokens': 100,
            'input_tokens_details': {'cached_tokens': 80}, 'output_tokens': 5, 'total_tokens': 105})
        self.assertEqual(recorder.snapshot()[0]['usage'],
                         {'input_tokens': 100, 'cached_input_tokens': 80,
                          'output_tokens': 5, 'total_tokens': 105})

    def test_bounded_concurrent_records_stay_isolated(self):
        recorder = metrics.Recorder(limit=4)
        def record(index):
            source = {'input': [{'role': 'user', 'content': 'x' * index}]}
            wire = json.dumps(source, separators=(',', ':')).encode()
            handle = recorder.begin(source, source, {'provider': 'fixture', 'model': str(index)}, wire)
            recorder.finish(handle, 'completed', {'input_tokens': index, 'output_tokens': 1})
        threads = [threading.Thread(target=record, args=(index,)) for index in range(1, 9)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        result = recorder.snapshot()
        self.assertEqual(len(result), 4)
        self.assertEqual(len({item['sequence'] for item in result}), 4)
        for item in result:
            self.assertEqual(item['usage']['input_tokens'], int(item['model']))

    def test_compaction_classification_requires_an_explicit_item(self):
        ordinary = {'instructions': 'compact this privately', 'input': []}
        compact = {'input': [{'type': 'compaction', 'encrypted_content': 'PRIVATE'}]}
        self.assertEqual(metrics.measure(ordinary, ordinary, {}, b'{}')['request_kind'], 'normal_or_unknown')
        self.assertEqual(metrics.measure(compact, compact, {}, b'{}')['request_kind'], 'contains_compaction_item')

    def test_every_record_and_snapshot_are_independent_copies(self):
        recorder = metrics.Recorder(limit=2)
        source = {'input': [{'role': 'user', 'content': 'PRIVATE'}]}
        wire = json.dumps(source, separators=(',', ':')).encode()
        handle = recorder.begin(source, source, {'provider': 'fixture', 'model': 'model'}, wire)
        source['input'][0]['content'] = 'CHANGED'
        recorder.finish(handle, 'completed', {'input_tokens': 1, 'output_tokens': 1})
        first = recorder.snapshot()
        first[0]['input_shape']['message:user']['items'] = 99
        second = recorder.snapshot()
        self.assertEqual(second[0]['input_shape']['message:user']['items'], 1)
        self.assertNotIn('PRIVATE', json.dumps(second))
        self.assertNotIn('CHANGED', json.dumps(second))

class RollupTests(unittest.TestCase):
    def test_rollup_aggregates_without_content(self):
        recorder = metrics.Recorder()
        source = {'input': [{'role': 'user', 'content': 'PRIVATE text'}],
                  'tools': [{'type': 'function', 'name': 'PRIVATE tool'}]}
        handle = recorder.begin(source, source, {'provider': 'azure', 'model': 'm'},
                                json.dumps(source).encode())
        recorder.finish(handle, 'completed', {'input_tokens': 10})
        buckets = metrics.rollup(recorder.snapshot())
        self.assertEqual(len(buckets), 1)
        bucket = buckets[0]
        self.assertEqual(bucket['requests'], 1)
        self.assertEqual(bucket['states'], {'completed': 1})
        self.assertGreater(bucket['input_serialized_bytes'], 0)
        self.assertNotIn('PRIVATE', json.dumps(buckets))

    def test_flush_writes_rollups_once(self):
        recorder = metrics.Recorder()
        handle = recorder.begin({'input': []}, {'input': []}, {'provider': 'p', 'model': 'm'}, b'{}')
        recorder.finish(handle, 'completed', {'input_tokens': 1})
        written = []
        self.assertTrue(recorder.flush_rollups(written.append))
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]['status'], 'metrics_rollup')
        self.assertTrue(recorder.flush_rollups(written.append))
        self.assertEqual(len(written), 1)

    def test_flush_write_failure_is_swallowed(self):
        recorder = metrics.Recorder()
        handle = recorder.begin({'input': []}, {'input': []}, {}, b'{}')
        recorder.finish(handle, 'completed', {'input_tokens': 1})
        def boom(event):
            raise OSError('disk full')
        self.assertFalse(recorder.flush_rollups(boom))

    def test_started_records_do_not_rollup(self):
        recorder = metrics.Recorder()
        recorder.begin({'input': []}, {'input': []}, {}, b'{}')
        self.assertEqual(metrics.rollup(recorder.snapshot()), [])
