import contextlib
import copy
import http.client
import http.server
import importlib.util
import io
import json
import stat
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from Tests.test_bifrost_pilot import FakeDocker, pilot

DIRECTORY = Path(__file__).resolve().parents[1] / 'experiments/bifrost'
with patch.dict(sys.modules, {'pilot': pilot}):
    spec = importlib.util.spec_from_file_location('pressure_pilot', DIRECTORY / 'pressure_pilot.py')
    pressure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pressure)
    spec = importlib.util.spec_from_file_location('composed_pilot', DIRECTORY / 'composed_pilot.py')
    composed = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'pressure_pilot': pressure}):
        spec.loader.exec_module(composed)


class SyntheticPassthrough(http.server.ThreadingHTTPServer):
    """Explicit test double: byte forwarding only, never claimed to be Bifrost."""
    daemon_threads = True

    def __init__(self, port):
        super().__init__(('127.0.0.1', 0), PassthroughHandler)
        self.upstream_port = port


class PassthroughHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_POST(self):
        if self.path != '/azure_passthrough/openai/v1/responses':
            self.send_error(404)
            return
        connection = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=60)
        response = None
        try:
            body = self.rfile.read(int(self.headers['Content-Length']))
            connection.request('POST', '/openai/v1/responses', body,
                               {'Content-Type': 'application/json', 'api-key': composed.PROVIDER_KEY})
            response = connection.getresponse()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ('connection', 'server', 'date', 'transfer-encoding'):
                    self.send_header(name, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            if response.getheader('Content-Type', '').startswith('text/event-stream'):
                while line := response.readline():
                    self.wfile.write(line)
                    self.wfile.flush()
            else:
                self.wfile.write(response.read())
        except (OSError, http.client.HTTPException):
            pass
        finally:
            self.close_connection = True
            if response:
                response.close()
            connection.close()


class ComposedTests(unittest.TestCase):
    def serve(self, server):
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        worker.start()
        def close():
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
        self.addCleanup(close)
        return server

    def temporary(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def test_actual_retained_entrypoint_all_cases_through_explicit_synthetic_passthrough(self):
        fixture = composed.prepare_fixture(self.temporary())
        upstream = self.serve(composed.ComposedAzure())
        passthrough = self.serve(SyntheticPassthrough(upstream.server_port))
        control = pressure.MockControl('http://127.0.0.1:' + str(upstream.server_port))
        report = composed.suite('127.0.0.1', list(composed.CASES), control, passthrough.server_port, fixture)
        failed = [row for row in report['cases'] if not row['passed']]
        self.assertEqual(failed, [], json.dumps(failed, indent=2))
        self.assertTrue(report['composed_synthetic_gate_passed'])
        self.assertTrue(report['entrypoint_verified'])
        self.assertEqual(report['harbor_runtime_digest'], json.loads((fixture / 'source.json').read_text())['runtime_id'])
        self.assertEqual(report['gateway_cleanup'], {'process_exited': True, 'sentinel_unchanged': True,
                                                    'source_unchanged': True, 'logs_content_free': True})
        self.assertFalse(report['installed_runtime_changed'])
        self.assertEqual(report['harbor_policy'], {'active_limit': 2, 'max_waiting': 16, 'wait_seconds': 30.0})
        self.assertEqual(report['total_upstream_attempts'], 34)
        self.assertEqual(report['client_total_deadline_seconds'], 55)
        self.assertTrue(report['queue_timeout_tested'])
        timeout = next(row for row in report['cases'] if row['case'] == 'queue_timeout')
        self.assertEqual(timeout['http_statuses'], [503, 200, 200])
        self.assertEqual(list(timeout['upstream_attempts'].values()), [1, 1, 0])
        self.assertGreaterEqual(timeout['queued_request_elapsed_ms'], 30000)
        self.assertGreaterEqual(timeout['queue_observed_wait_ms'], 29000)
        self.assertLess(timeout['queued_request_elapsed_ms'], 38000)
        self.assertTrue(timeout['checks']['held_streams_open_after_timeout'])
        self.assertTrue(timeout['checks']['held_streams_completed'])
        self.assertTrue(timeout['checks']['timeout_does_not_make_turn_uncertain'])
        self.assertTrue(report['all_upstream_requests_accounted_for'])

    def test_partial_case_selection_cannot_claim_composed_gate(self):
        fixture = composed.prepare_fixture(self.temporary())
        upstream = self.serve(composed.ComposedAzure())
        passthrough = self.serve(SyntheticPassthrough(upstream.server_port))
        control = pressure.MockControl('http://127.0.0.1:' + str(upstream.server_port))
        report = composed.suite('127.0.0.1', ['error429'], control, passthrough.server_port, fixture)
        self.assertTrue(report['cases'][0]['passed'])
        self.assertFalse(report['study_complete'])
        self.assertFalse(report['queue_timeout_tested'])
        self.assertFalse(report['composed_synthetic_gate_passed'])
        self.assertTrue(all(report['gateway_cleanup'].values()))

    def test_affinity_rejects_wrong_output_missing_usage_and_postterminal_frames(self):
        key = 'synthetic-request'
        event = {'type': 'response.completed', 'response': pressure.correlation_response(key)}
        valid = {'status': 200, 'events': [
            {'type': 'response.output_text.delta', 'delta': composed.CONTENT + ':' + key}, event]}
        self.assertTrue(composed.correlated(valid, key))
        wrong = copy.deepcopy(valid)
        wrong['events'][0]['delta'] = 'another-request'
        self.assertFalse(composed.correlated(wrong, key))
        wrong = copy.deepcopy(valid)
        del wrong['events'][1]['response']['usage']
        self.assertFalse(composed.correlated(wrong, key))
        wrong = copy.deepcopy(valid)
        wrong['events'].append({'type': 'response.output_text.delta', 'delta': 'late'})
        self.assertFalse(composed.correlated(wrong, key))

    def test_missing_added_frame_fails_even_when_terminal_opaque_snapshot_survives(self):
        fixture = composed.prepare_fixture(self.temporary())
        upstream = self.serve(composed.ComposedAzure())
        passthrough = self.serve(SyntheticPassthrough(upstream.server_port))
        control = pressure.MockControl('http://127.0.0.1:' + str(upstream.server_port))
        original = composed.Gateway.perform
        def lose_frame(gateway, body, **options):
            result = original(gateway, body, **options)
            result['events'] = [event for event in result.get('events', []) if not (
                event.get('type') == 'response.output_item.added' and event.get('item', {}).get('type') == 'reasoning')]
            return result
        with patch.object(composed.Gateway, 'perform', lose_frame):
            report = composed.suite('127.0.0.1', ['opaque_sse'], control, passthrough.server_port, fixture)
        self.assertFalse(report['composed_synthetic_gate_passed'])
        self.assertFalse(report['cases'][0]['checks']['sse_item_frames_preserved'])
        self.assertTrue(report['cases'][0]['checks']['ordered_output_preserved'])
        self.assertTrue(all(report['gateway_cleanup'].values()))

    def test_redirect_is_exact_and_rejects_every_other_destination(self):
        original = pilot.urllib.request.OpenerDirector.open
        with patch.object(pilot.urllib.request.OpenerDirector, 'open') as opener:
            namespace = {}
            exec(composed.redirect_source('http://synthetic:8080/azure_passthrough/openai/v1/responses'), namespace)
            try:
                request = pilot.urllib.request.Request(composed.AZURE_URL, data=b'opaque', headers={'api-key': 'synthetic'}, method='POST')
                namespace['redirect'](object(), request)
                forwarded = opener.call_args.args[1]
                self.assertEqual(forwarded.full_url, 'http://synthetic:8080/azure_passthrough/openai/v1/responses')
                self.assertEqual(forwarded.data, b'opaque')
                self.assertEqual(forwarded.get_header('Api-key'), 'synthetic')
                self.assertEqual(forwarded.get_method(), 'POST')
                for value in ('https://real.openai.azure.com/openai/v1/responses', composed.AZURE_URL + '?extra=1',
                              composed.AZURE_URL + '/extra', 'http://fixture.openai.azure.com/openai/v1/responses'):
                    with self.assertRaisesRegex(RuntimeError, 'Unexpected'):
                        namespace['redirect'](object(), pilot.urllib.request.Request(value))
                self.assertEqual(opener.call_count, 1)
            finally:
                pilot.urllib.request.OpenerDirector.open = original

    def test_export_is_readable_across_container_uids_without_changing_source_inventory(self):
        fixture = composed.prepare_fixture(self.temporary())
        source = Path(__file__).resolve().parents[1] / 'ModelHarbor/Support'
        service = composed.load_module('fixture_service', source / 'gateway_service.py')
        expected = service.inventory(source)
        manifest = json.loads((fixture / 'source.json').read_text())
        self.assertEqual(manifest, {'runtime_id': service.runtime_digest(expected), 'files': expected})
        self.assertEqual(service.inventory(fixture / 'Support'), expected)
        for path in (fixture, *(fixture.rglob('*'))):
            required = 0o005 if path.is_dir() else 0o004
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & required, required, str(path))
        self.assertFalse((fixture / 'state').exists())

    def test_staged_inventory_rejects_tampering_before_launch(self):
        fixture = composed.prepare_fixture(self.temporary())
        source = fixture / 'Support/azure_admission.py'
        source.write_text(source.read_text() + '\n# changed after stage\n')
        with patch.object(composed.subprocess, 'Popen') as process:
            with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
                composed.Gateway(fixture, 'http://127.0.0.1:1/azure_passthrough/openai/v1/responses')
            process.assert_not_called()

    def test_extra_request_and_missing_attempt_cannot_pass(self):
        body = composed.request('error429')
        key = body['metadata']['harbor_pilot_request']
        base = {'path': '/openai/v1/responses', 'body': {'model': 'pilot-deployment'},
                'synthetic_key_correct': True, 'request_id': key}
        row = composed.evidence('error429', {'correct': True}, [body], [], [], {key: 1})
        self.assertFalse(row['passed'])
        row = composed.evidence('error429', {'correct': True}, [body], [],
                                [base, dict(base, request_id='unexpected')], {key: 1})
        self.assertFalse(row['passed'])
        self.assertFalse(row['checks']['no_unexpected_upstream_requests'])

    def test_incompatible_surface_rejected_before_docker_or_staging(self):
        with patch.object(sys, 'argv', ['composed_pilot.py', '--output', '/unused', '--surface', 'converted']), \
                patch.object(pilot, 'docker') as docker, patch.object(composed, 'prepare_fixture') as stage, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                pilot.main(study=composed)
            docker.assert_not_called()
            stage.assert_not_called()

    def test_driver_requires_composed_evidence_and_copies_snapshot(self):
        root = self.temporary()
        output = root / 'report.json'
        docker = FakeDocker()
        with patch.object(sys, 'argv', ['composed_pilot.py', '--output', str(output)]), \
                patch.object(pilot, 'docker', docker), patch.object(pilot.platform, 'machine', return_value='arm64'), \
                contextlib.redirect_stdout(io.StringIO()):
            pilot.main(study=composed)
        report = json.loads(output.read_text())
        self.assertFalse(report['all_synthetic_gates_passed'])
        self.assertTrue(any(call[0] == 'cp' and call[1].endswith('/composed-source') for call in docker.calls))
        creates = [call for call in docker.calls if call[0] == 'create']
        self.assertIn('192', creates[0])
        self.assertEqual(report['cleanup_errors'], [])

    def test_bifrost_limits_cannot_explain_harbor_two_stream_cap(self):
        source = pilot.config('http://synthetic', 'azure-passthrough')
        original = copy.deepcopy(source)
        configured = composed.configure(source)
        self.assertEqual(source, original)
        provider = configured['providers']['azure']
        self.assertEqual(provider['concurrency_and_buffer_size'], {'concurrency': 32, 'buffer_size': 32})
        self.assertEqual(provider['network_config']['max_retries'], 0)
        self.assertGreater(provider['network_config']['stream_idle_timeout_in_seconds'], composed.HOLD_SECONDS)
        self.assertGreater(provider['network_config']['default_request_timeout_in_seconds'], composed.REQUEST_SECONDS)
        self.assertGreater(composed.REQUEST_SECONDS, composed.HOLD_SECONDS)
        self.assertGreater(composed.HOLD_SECONDS, 30 + 2 * composed.STEP)
        self.assertIn('queue_timeout', composed.CASES)

    def test_queue_timeout_claim_requires_complete_measured_evidence(self):
        ids = ['held-first', 'held-second', 'expired-waiter']
        row = {
            'case': 'queue_timeout', 'passed': True, 'request_ids': ids,
            'http_statuses': [503, 200, 200], 'total_upstream_attempts': 2,
            'upstream_attempts': {'held-first': 1, 'held-second': 1, 'expired-waiter': 0},
            'queued_request_elapsed_ms': 30025.0, 'queue_observed_wait_ms': 30000.0,
            'checks': {
                'two_established_streams_and_waiters': True, 'queued_has_no_upstream_attempt': True,
                'local_queue_timeout_response': True, 'production_queue_deadline': True,
                'held_streams_open_after_timeout': True, 'timed_out_waiter_removed': True,
                'timed_out_request_has_no_active_lease': True, 'timeout_does_not_make_turn_uncertain': True,
                'held_streams_completed': True, 'exact_attempt_counts': True,
                'no_unexpected_upstream_requests': True,
            },
        }
        self.assertTrue(composed.queue_timeout_verified([row]))
        for rows in ([], [dict(row, case='queue_capacity')], [row, row],
                     [dict(row, passed=False)], [dict(row, checks={})],
                     [dict(row, queued_request_elapsed_ms=25000)],
                     [dict(row, queue_observed_wait_ms=1000)],
                     [dict(row, queued_request_elapsed_ms=float('nan'))],
                     [dict(row, http_statuses=[200, 200, 200])],
                     [dict(row, upstream_attempts={'held-first': 1, 'held-second': 1, 'expired-waiter': 1})],
                     [dict(row, upstream_attempts={'held-first': 1, 'held-second': 1})]):
            with self.subTest(rows=rows):
                self.assertFalse(composed.queue_timeout_verified(rows))
        for check in row['checks']:
            changed = copy.deepcopy(row)
            changed['checks'][check] = False
            self.assertFalse(composed.queue_timeout_verified([changed]), check)

    def test_queue_timeout_attempt_evidence_rejects_dispatch_or_replay(self):
        bodies = [composed.request('queue_timeout', stream=True, hold=True) for _ in range(2)]
        bodies.append(composed.request('queue_timeout', stream=True))
        first, second, expired = [body['metadata']['harbor_pilot_request'] for body in bodies]
        def record(key):
            return {'path': '/openai/v1/responses', 'body': {'model': 'pilot-deployment'},
                    'synthetic_key_correct': True, 'request_id': key}
        for keys in ([first, second, expired], [first, first, second], [first],
                     [first, second, 'unowned-request']):
            with self.subTest(keys=keys):
                row = composed.evidence('queue_timeout', {'local_queue_timeout_response': True},
                                        bodies, [], [record(key) for key in keys],
                                        {first: 1, second: 1, expired: 0})
                self.assertFalse(row['passed'])
