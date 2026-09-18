import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit-context.py'

def load():
    spec = importlib.util.spec_from_file_location('context_audit', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(kind, payload, second=0):
    return {'type': kind, 'timestamp': f'2026-09-18T21:00:{second:02d}Z', 'payload': payload}


def usage(identifier, tokens, second=0):
    return row('token_usage_record', {'response_id': identifier, 'usage': {
        'input_tokens': tokens, 'cached_input_tokens': tokens - 10, 'output_tokens': 20,
        'total_tokens': tokens + 20}, 'thread_token_usage': {'input_tokens': 999999999}}, second)


def estimate(tokens, window, second=0):
    return row('event_msg', {'type': 'token_count', 'info': {'model_context_window': window,
        'last_token_usage': {'input_tokens': 0, 'total_tokens': tokens}}}, second)


def compact(identifier='compact', second=0):
    return row('compacted', {'compaction_response_id': identifier,
        'message': 'PRIVATE SUMMARY', 'replacement_history': [{'content': 'PRIVATE HISTORY'}]}, second)


def report(rows, limit=10):
    return load().audit(io.BytesIO(('\n'.join(json.dumps(r) for r in rows) + '\n').encode()), limit=limit)


class ContextAuditTests(unittest.TestCase):
    def test_loop_and_corrected_window_are_measured_without_cumulative_or_cached_double_count(self):
        result = report([compact(), estimate(16000, 121600), usage('first', 140000, 1),
                         usage('compactor', 25000, 2), compact('compactor', 3),
                         estimate(20000, 258400, 3), usage('second', 143000, 4),
                         usage('third', 244000, 5), usage('compactor2', 120000, 6), compact('compactor2', 7)])
        first, second, active = result['cycles']
        self.assertEqual(first['first_provider_input_tokens'], 140000)
        self.assertEqual(first['unattributed_input_minus_history_estimate'], 124000)
        self.assertTrue(first['first_request_exceeds_effective_window'])
        self.assertEqual(first['provider_requests'], 1)
        self.assertEqual(second['provider_requests'], 2)
        self.assertEqual(second['last_provider_input_tokens'], 244000)
        self.assertEqual(second['elapsed_seconds'], 4)
        self.assertFalse(second['first_request_exceeds_effective_window'])
        self.assertEqual(second['compactor_usage'], 'excluded_by_response_id')
        self.assertIsNone(active['first_provider_input_tokens'])

    def test_repeated_snapshots_do_not_count_as_requests_and_missing_usage_is_unknown(self):
        snapshot = row('event_msg', {'type': 'token_count', 'info': {'model_context_window': 258400,
            'last_token_usage': {'input_tokens': 140000, 'total_tokens': 140010}}})
        cycle = report([compact(), snapshot, snapshot, compact('next')])['cycles'][0]
        self.assertEqual(cycle['provider_requests'], 0)
        self.assertIsNone(cycle['first_provider_input_tokens'])
        self.assertEqual(cycle['compactor_usage'], 'not_observed')

    def test_content_is_not_exposed_and_byte_lengths_are_not_tokens(self):
        rows = [compact(), row('response_item', {'type': 'function_call_output',
            'output': 'PRIVATE TOOL α', 'call_id': 'PRIVATE ID'}),
            row('response_item', {'type': 'custom_tool_call_output', 'output': [{'type': 'image', 'data': 'PRIVATE IMAGE'}]}),
            row('event_msg', {'type': 'agent_message', 'message': 'PRIVATE PROMPT'})]
        result = report(rows)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['cycles'][0]['tool_outputs'], 2)
        self.assertGreater(result['cycles'][0]['tool_output_serialized_bytes'], 25)
        self.assertNotIn('estimated_tokens', json.dumps(result))

    def test_only_recent_cycles_are_retained(self):
        result = report([compact(str(i), i) for i in range(30)], limit=3)
        self.assertEqual(result['compactions'], 30)
        self.assertEqual(len(result['cycles']), 3)
        self.assertEqual(result['cycles'][0]['start_line'], 28)

    def test_malformed_interior_and_unfinished_final_line_are_distinct(self):
        raw = (json.dumps(compact()) + '\nnot-json\n' + json.dumps(estimate(20, 100)) + '\n{"payload":').encode()
        result = load().audit(io.BytesIO(raw))
        self.assertEqual(result['invalid_records'], 1)
        self.assertEqual(result['incomplete_final_line'], True)
        self.assertEqual(result['cycles'][0]['post_compaction_history_estimate'], 20)

    def test_model_switch_clears_window_and_prevents_false_adoption(self):
        rows = [row('turn_context', {'model': 'harbor/azure/one'}), compact(), estimate(20, 100),
                row('turn_context', {'model': 'harbor/azure/two'}), usage('x', 150)]
        result = report(rows)
        self.assertIsNone(result['latest_observed_context']['effective_window'])
        self.assertTrue(result['cycles'][0]['model_changed'])
        self.assertIsNone(result['cycles'][0]['first_request_exceeds_effective_window'])

    def test_cli_reads_without_mutating_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'private-name.jsonl'
            source.write_text(json.dumps(compact()) + '\n')
            before = hashlib.sha256(source.read_bytes()).digest()
            run = subprocess.run([sys.executable, str(SCRIPT), str(source)], capture_output=True, check=True)
            self.assertEqual(before, hashlib.sha256(source.read_bytes()).digest())
            self.assertNotIn(str(source), run.stdout.decode())
            self.assertEqual(json.loads(run.stdout)['compactions'], 1)

    def test_duplicate_usage_and_invalid_shapes_are_not_requests(self):
        result = report([compact(), usage('a', 100), usage('a', 100),
                         row('token_usage_record', {'usage': {'input_tokens': 999}}),
                         row('token_usage_record', {'response_id': 'bad', 'usage': {'input_tokens': True}}),
                         row('event_msg', {'type': 'token_count', 'info': []}),
                         usage('b', 200)])
        self.assertEqual(result['cycles'][0]['provider_requests'], 2)
        self.assertEqual(result['duplicate_usage_records'], 1)
        self.assertEqual(result['unidentified_usage_records'], 1)

    def test_unknown_compactor_is_not_guessed_from_small_usage(self):
        cycle = report([compact(), usage('normal', 200), usage('small', 10),
                        compact('unseen')])['cycles'][0]
        self.assertEqual(cycle['provider_requests'], 2)
        self.assertEqual(cycle['last_provider_input_tokens'], 10)
        self.assertEqual(cycle['compactor_usage'], 'not_observed')

    def test_model_change_cannot_rewrite_previous_request_observation(self):
        cycle = report([row('turn_context', {'model': 'one'}), compact(), estimate(20, 100),
                        usage('first', 80), row('turn_context', {'model': 'two'}),
                        usage('second', 150)])['cycles'][0]
        self.assertEqual(cycle['unattributed_input_minus_history_estimate'], 60)
        self.assertEqual(cycle['first_request_effective_window'], 100)
        self.assertTrue(cycle['model_changed'])

    def test_valid_final_record_without_newline_and_invalid_limit(self):
        module = load()
        result = module.audit(io.BytesIO(json.dumps(compact()).encode()))
        self.assertFalse(result['incomplete_final_line'])
        for limit in (0, -1, 1001, True):
            with self.assertRaises(ValueError):
                module.audit(io.BytesIO(b''), limit)

    def test_cli_errors_do_not_disclose_source_path(self):
        run = subprocess.run([sys.executable, str(SCRIPT), '/PRIVATE-MISSING/path'], capture_output=True)
        self.assertEqual(run.returncode, 2)
        self.assertNotIn(b'PRIVATE', run.stdout + run.stderr)

    def test_nonadjacent_compactor_is_marked_unresolved(self):
        cycle = report([compact(), usage('compactor', 20), usage('normal', 100),
                        compact('compactor')])['cycles'][0]
        self.assertEqual(cycle['compactor_usage'], 'unresolved_nonadjacent_usage')

    def test_untrusted_metadata_and_malformed_records_do_not_leak(self):
        result = report([None, [], row('turn_context', {'model': 'PRIVATE MODEL'}),
                         {'type': 'compacted', 'timestamp': 'PRIVATE TIME', 'payload': {}},
                         usage('PRIVATE RESPONSE ID', 25)])
        self.assertEqual(result['invalid_records'], 2)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertIsNone(result['cycles'][0]['started_at'])
        self.assertEqual(result['cycles'][0]['first_request_model_epoch'], 1)
