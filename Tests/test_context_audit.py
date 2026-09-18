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


def report(rows, limit=10, **evidence):
    return load().audit(io.BytesIO(('\n'.join(json.dumps(r) for r in rows) + '\n').encode()),
                        limit=limit, **evidence)


def catalog(model='private/model', default=272000, maximum=872000, version='client-v1',
            fetched_at='2026-09-18T20:00:00Z'):
    return {'client_version': version, 'fetched_at': fetched_at, 'models': [{
        'slug': model, 'context_window': default, 'max_context_window': maximum}]}


def digest(document):
    raw = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def published(model='private/model', default=272000, maximum=872000, version='client-v1',
              source_digest=None):
    source_digest = source_digest or digest(catalog(model, default, maximum, version))
    return {'harbor_sources': [{'path': '/PRIVATE/source/catalog.json', 'client_version': version,
             'fetched_at': '2026-09-18T20:00:00Z', 'sha256': source_digest}],
            'models': [{'slug': model, 'context_window': default,
             'max_context_window': maximum, 'harbor_native_client_version': version,
             'harbor_native_fetched_at': '2026-09-18T20:00:00Z'}]}


class ContextAuditTests(unittest.TestCase):
    def test_loop_and_corrected_window_are_measured_without_cumulative_or_cached_double_count(self):
        result = report([compact(), estimate(16000, 121600), usage('first', 140000, 1),
                         usage('compactor', 25000, 2), compact('compactor', 3),
                         estimate(20000, 258400, 3), usage('second', 143000, 4),
                         usage('third', 244000, 5), usage('compactor2', 120000, 6), compact('compactor2', 7)])
        first, second, active = result['cycles']
        self.assertEqual(first['first_provider_input_tokens'], 140000)
        self.assertEqual(first['unattributed_input_minus_history_estimate'], 124000)
        self.assertEqual(first['first_provider_cached_input_tokens'], 139990)
        self.assertEqual(first['first_provider_uncached_input_tokens'], 10)
        self.assertEqual(first['uncached_input_minus_history_estimate'], -15990)
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
        observed = result['context_evidence']['task_observed_effective_window']
        self.assertEqual(observed['status'], 'model-changed')
        self.assertEqual(observed['model_epoch'], 2)
        self.assertEqual(observed['previous_model_epoch'], 1)
        self.assertIsNone(observed['effective_window'])
        self.assertEqual(observed['changed_at'], '2026-09-18T21:00:00+00:00')

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

    def test_optional_evidence_is_unknown_and_unobserved_task_is_not_inferred(self):
        result = report([row('turn_context', {'model': 'private/model'}), compact()])
        evidence = result['context_evidence']
        self.assertEqual(evidence['task_observed_effective_window']['status'], 'unknown')
        self.assertIsNone(evidence['task_observed_effective_window']['effective_window'])
        self.assertEqual(evidence['published_catalog']['status'], 'unknown')
        self.assertEqual(evidence['native_capture']['status'], 'unknown')
        self.assertEqual(evidence['current_client']['status'], 'unknown')
        self.assertEqual(evidence['desktop_adoption']['status'], 'unknown')

    def test_native_capture_is_stale_and_published_source_version_mismatch_is_distinct(self):
        result = report([row('turn_context', {'model': 'private/model'}), estimate(20, 258400)],
                        published_catalog=published(), native_catalog=catalog(),
                        current_client_version='client-v2')
        evidence = result['context_evidence']
        self.assertEqual(evidence['task_observed_effective_window']['status'], 'observed')
        self.assertEqual(evidence['native_capture']['status'], 'stale')
        self.assertEqual(evidence['published_catalog']['status'], 'source-version-mismatch')
        self.assertEqual(evidence['current_client']['status'], 'observed')
        self.assertNotEqual(evidence['native_capture']['captured_client_version_sha256'],
                            evidence['current_client']['version_sha256'])

        changed_source = report([row('turn_context', {'model': 'private/model'})],
                                published_catalog=published(source_digest='b' * 64),
                                native_catalog=catalog(), current_client_version='client-v1')
        changed_evidence = changed_source['context_evidence']
        self.assertEqual(changed_evidence['native_capture']['status'], 'observed')
        self.assertEqual(changed_evidence['published_catalog']['status'],
                         'source-version-mismatch')

    def test_unrelated_published_provider_versions_do_not_create_a_mismatch(self):
        native = catalog(model='private-model')
        source = published(model='harbor/azure/private-model', source_digest=digest(native))
        source['harbor_sources'][0]['provider'] = 'azure'
        source['harbor_sources'].append({'provider': 'openrouter', 'client_version': 'other-v9',
                                          'sha256': 'c' * 64})
        result = report([row('turn_context', {'model': 'harbor/azure/private-model'})],
                        published_catalog=source, native_catalog=native,
                        current_client_version='client-v1')
        self.assertEqual(result['context_evidence']['published_catalog']['status'], 'observed')

    def test_exact_ratio_explanation_is_conditional_and_not_a_conversion_rule(self):
        exact = report([row('turn_context', {'model': 'private/model'}), estimate(20, 258400)],
                       published_catalog=published())
        comparison = exact['context_evidence']['task_to_published_default']
        self.assertEqual(comparison['status'], 'observed')
        self.assertEqual(comparison['ratio'], .95)
        self.assertIn('258400 is 95% of 272000', comparison['explanation'])
        self.assertIn('not establish a universal conversion', comparison['explanation'])

        alternate = report([row('turn_context', {'model': 'private/model'}), estimate(20, 150000)],
                           published_catalog=published(default=200000, maximum=500000))
        alternate_comparison = alternate['context_evidence']['task_to_published_default']
        self.assertEqual(alternate_comparison['ratio'], .75)
        self.assertNotIn('95%', alternate_comparison['explanation'])
        self.assertIn('not establish a universal conversion', alternate_comparison['explanation'])

    def test_explicit_missing_invalid_and_model_absent_catalogs_have_distinct_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'PRIVATE-missing.json'
            result = report([row('turn_context', {'model': 'private/model'})],
                            published_catalog=missing, native_catalog=b'not-json')
        self.assertEqual(result['context_evidence']['published_catalog']['status'], 'unavailable')
        self.assertEqual(result['context_evidence']['native_capture']['status'], 'unavailable')
        self.assertNotIn('PRIVATE', json.dumps(result))

        absent = report([row('turn_context', {'model': 'private/model'})],
                        published_catalog=published(model='different/model'),
                        native_catalog=catalog(model='different/model'))
        self.assertEqual(absent['context_evidence']['published_catalog']['status'], 'model-not-found')
        self.assertEqual(absent['context_evidence']['native_capture']['status'], 'model-not-found')

    def test_default_maximum_and_native_capture_are_separate_from_effective_window(self):
        result = report([row('turn_context', {'model': 'private/model'}), estimate(20, 258400)],
                        published_catalog=published(), native_catalog=catalog(),
                        current_client_version='client-v1')
        evidence = result['context_evidence']
        self.assertEqual(evidence['task_observed_effective_window']['effective_window'], 258400)
        self.assertEqual(evidence['published_catalog']['default_window'], 272000)
        self.assertEqual(evidence['published_catalog']['maximum_window'], 872000)
        self.assertEqual(evidence['native_capture']['default_window'], 272000)
        self.assertEqual(evidence['native_capture']['maximum_window'], 872000)
        self.assertEqual(evidence['published_catalog']['status'], 'observed')
        self.assertEqual(evidence['native_capture']['status'], 'observed')
        self.assertNotIn('selected', json.dumps(evidence).lower())

        routed = report([row('turn_context', {'model': 'harbor/azure/private-model'})],
                        native_catalog=catalog(model='private-model'))
        self.assertEqual(routed['context_evidence']['native_capture']['status'], 'observed')

    def test_catalog_model_versions_and_paths_are_confidential(self):
        private_model = 'PRIVATE MODEL ID'
        private_version = 'PRIVATE CLIENT VERSION'
        source = published(model=private_model, version=private_version)
        source['harbor_sources'][0]['path'] = '/PRIVATE/catalog/path.json'
        result = report([row('turn_context', {'model': private_model}), estimate(20, 258400)],
                        published_catalog=source,
                        native_catalog=catalog(model=private_model, version=private_version),
                        current_client_version=private_version)
        rendered = json.dumps(result)
        self.assertNotIn('PRIVATE', rendered)
        self.assertEqual(len(result['context_evidence']['published_catalog']['catalog_sha256']), 64)
        self.assertEqual(len(result['context_evidence']['native_capture']['catalog_sha256']), 64)
        self.assertEqual(len(result['context_evidence']['current_client']['version_sha256']), 64)

    def test_cli_catalog_paths_are_explicit_optional_and_never_emitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rollout = root / 'PRIVATE-rollout.jsonl'
            published_path = root / 'PRIVATE-published.json'
            native_path = root / 'PRIVATE-native.json'
            version_path = root / 'PRIVATE-version.txt'
            rollout.write_text('\n'.join(json.dumps(item) for item in [
                row('turn_context', {'model': 'private/model'}), estimate(20, 258400)]) + '\n')
            published_path.write_text(json.dumps(published()))
            native_path.write_text(json.dumps(catalog(), sort_keys=True, ensure_ascii=False,
                                              separators=(',', ':')))
            version_path.write_text('client-v1\n')
            run = subprocess.run([sys.executable, str(SCRIPT), str(rollout),
                '--published-catalog', str(published_path), '--native-catalog', str(native_path),
                '--current-client-version-file', str(version_path)], capture_output=True, check=True)
        rendered = run.stdout.decode()
        self.assertNotIn('PRIVATE', rendered)
        evidence = json.loads(rendered)['context_evidence']
        self.assertEqual(evidence['published_catalog']['status'], 'observed')
        self.assertEqual(evidence['native_capture']['status'], 'observed')

    def test_existing_audit_call_and_rollout_fields_remain_compatible(self):
        module = load()
        result = module.audit(io.BytesIO((json.dumps(compact()) + '\n').encode()), 10)
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['compactions'], 1)
        self.assertEqual(result['latest_observed_context'], {'model_epoch': 0, 'effective_window': None})
        self.assertIn('cycles', result)
