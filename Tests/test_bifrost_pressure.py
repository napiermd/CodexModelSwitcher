import concurrent.futures
import contextlib
import copy
import http.client
import http.server
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from Tests.test_bifrost_pilot import FakeDocker, pilot

spec = importlib.util.spec_from_file_location(
    'bifrost_pressure', Path(__file__).resolve().parents[1] / 'experiments/bifrost/pressure_pilot.py')
pressure = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'pilot': pilot}):
    spec.loader.exec_module(pressure)


class AdmissionGateway(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, upstream_port, serialize_streams=False):
        super().__init__(('127.0.0.1', 0), AdmissionHandler)
        self.upstream_port = upstream_port
        self.serialize_streams = serialize_streams
        self.admission = threading.BoundedSemaphore(2)
        self.worker = threading.Lock()


class AdmissionHandler(pressure.PressureHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.close_connection = True
        if not self.server.admission.acquire(blocking=False):
            self.send_json(503, {'error': {'type': 'request_dropped',
                                         'message': 'request dropped: queue is full'}})
            return
        worker_held = False
        connection = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=4)
        response = None
        try:
            self.server.worker.acquire()
            worker_held = True
            connection.request('POST', '/openai/v1/responses', body,
                               {'Content-Type': 'application/json', 'api-key': 'synthetic-pilot-key'})
            response = connection.getresponse()
            streaming = response.getheader('Content-Type') == 'text/event-stream'
            if streaming and not self.server.serialize_streams:
                self.server.worker.release()
                worker_held = False
            self.send_response(response.status)
            self.send_header('Content-Type', response.getheader('Content-Type'))
            self.send_header('Connection', 'close')
            self.end_headers()
            if streaming:
                while line := response.readline():
                    self.wfile.write(line)
                    self.wfile.flush()
            else:
                self.wfile.write(response.read())
        finally:
            if response is not None:
                response.close()
            connection.close()
            if worker_held:
                self.server.worker.release()
            self.server.admission.release()


class BifrostPressureTests(unittest.TestCase):
    def serve(self, server):
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.addCleanup(close)
        return server

    def mock_and_gateway(self, serialize_streams=False):
        upstream = self.serve(pressure.PressureAzure())
        gateway = self.serve(AdmissionGateway(upstream.server_port, serialize_streams))
        control = pressure.MockControl('http://127.0.0.1:' + str(upstream.server_port))
        return upstream, gateway, control

    def test_queue_capacity_is_observed_without_assuming_follower_order(self):
        upstream, gateway, control = self.mock_and_gateway()
        row = pressure.queue_phase('127.0.0.1', control, gateway.server_port)
        self.assertTrue(row['passed'], row)
        self.assertEqual(row['http_statuses'], [200, 200, 503])
        self.assertEqual(row['upstream_attempts'], {
            row['held_request_id']: 1, row['queued_request_id']: 1, row['rejected_request_id']: 0})
        self.assertEqual([r['request_id'] for r in upstream.requests],
                         [row['held_request_id'], row['queued_request_id']])
        self.assertEqual(row['total_upstream_attempts'], 2)
        self.assertTrue(all(pressure.CONTENT_SENTINEL in json.dumps(r['body']) for r in upstream.requests))

    def test_open_stream_overlap_is_valid_evidence_and_disproves_lifetime_bound(self):
        upstream, gateway, control = self.mock_and_gateway()
        row = pressure.stream_phase('127.0.0.1', control, gateway.server_port)
        self.assertTrue(row['passed'], row)
        self.assertEqual(row['observation'], 'overlap_observed')
        self.assertTrue(row['active_stream_bound_disproved'])
        self.assertFalse(row['pacing_verified'])
        self.assertEqual(row['total_upstream_attempts'], 2)
        self.assertEqual(list(row['upstream_attempts'].values()), [1, 1])
        self.assertEqual(upstream.max_active, 2)

    def test_serialized_streams_do_not_claim_a_general_pacing_guarantee(self):
        _, gateway, control = self.mock_and_gateway(serialize_streams=True)
        with patch.object(pressure, 'OVERLAP_WINDOW', .05):
            row = pressure.stream_phase('127.0.0.1', control, gateway.server_port)
        self.assertTrue(row['passed'], row)
        self.assertEqual(row['observation'], 'no_overlap_during_window')
        self.assertFalse(row['active_stream_bound_disproved'])
        self.assertFalse(row['pacing_verified'])

    def test_phase_baselines_cover_all_new_requests_and_exclude_prior_phase(self):
        _, gateway, control = self.mock_and_gateway()
        result = pressure.suite('127.0.0.1', pressure.CASES, control, gateway.server_port)
        self.assertEqual([row['case'] for row in result['cases']], ['queue_capacity', 'stream_lifetime'])
        self.assertTrue(all(row['passed'] for row in result['cases']), result)
        self.assertEqual([row['total_upstream_attempts'] for row in result['cases']], [2, 2])
        self.assertFalse(result['pacing_verified'])

    def test_mock_hold_has_a_bounded_timeout(self):
        upstream = self.serve(pressure.PressureAzure())
        body = pilot.request('queue_first', surface='azure-passthrough')
        with patch.object(pressure, 'HOLD_TIMEOUT', .03):
            connection = http.client.HTTPConnection('127.0.0.1', upstream.server_port, timeout=1)
            try:
                connection.request('POST', '/openai/v1/responses', json.dumps(body),
                                   {'Content-Type': 'application/json', 'api-key': 'synthetic-pilot-key'})
                response = connection.getresponse()
                try:
                    self.assertEqual(response.status, 504)
                    self.assertEqual(json.loads(response.read())['error']['code'], 'synthetic_hold_timeout')
                finally:
                    response.close()
            finally:
                connection.close()

    def test_release_failure_still_releases_every_other_request(self):
        released = []
        def release(request_id):
            released.append(request_id)
            if request_id == 'first':
                raise OSError('synthetic failure')
        with self.assertRaises(RuntimeError):
            pressure.release_all(SimpleNamespace(release=release),
                                 [SimpleNamespace(request_id=x) for x in ('first', 'second', 'third')])
        self.assertEqual(released, ['first', 'second', 'third'])

    def test_started_request_is_released_when_phase_setup_fails(self):
        _, gateway, control = self.mock_and_gateway()
        original_wait = control.wait_for_request
        def fail_after_arrival(request_id):
            original_wait(request_id)
            raise RuntimeError('synthetic setup failure')
        with patch.object(control, 'wait_for_request', side_effect=fail_after_arrival):
            result = pressure.suite('127.0.0.1', ['queue_capacity'], control, gateway.server_port)
        self.assertFalse(result['cases'][0]['passed'])
        snapshot = control.snapshot()
        self.assertEqual(len(snapshot['requests']), 1)
        self.assertIn(snapshot['requests'][0]['request_id'], snapshot['released'])

    def queue_evidence(self):
        runs = [SimpleNamespace(request_id=name, body=pilot.request(name, surface='azure-passthrough'))
                for name in ('held', 'accepted', 'rejected')]
        records = [{'request_id': run.request_id, 'body': run.body, 'path': '/openai/v1/responses',
                    'synthetic_key_correct': True, 'at': at}
                   for run, at in zip(runs, (1, 3))]
        before = {'requests': copy.deepcopy(records[:1]), 'released': {}}
        final = {'requests': records, 'released': {'held': 2}}
        results = [{'status': 200, 'elapsed_ms': 10, 'data': json.dumps(pressure.correlation_response(name))}
                   for name in ('held', 'accepted')]
        results.append({'status': 503, 'elapsed_ms': 5, 'data': json.dumps({
            'error': {'type': 'request_dropped', 'message': 'request dropped: queue is full'}})})
        return runs, results, before, final

    def test_queue_evidence_rejects_replay_misattribution_wrong_error_and_extra_requests(self):
        runs, results, before, final = self.queue_evidence()
        self.assertTrue(pressure.evaluate_queue(runs, results, before, final)['passed'])
        mutations = {
            'replayed accepted': lambda r, f: f['requests'].append(copy.deepcopy(f['requests'][1])),
            'unexpected ID': lambda r, f: f['requests'].append({**f['requests'][1], 'request_id': 'unknown'}),
            'rejected reached upstream': lambda r, f: f['requests'].append({**f['requests'][1], 'request_id': 'rejected'}),
            'wrong response caller': lambda r, f: r[1].update(data=r[0]['data']),
            'wrong error': lambda r, f: r[2].update(data='{"error":{"type":"upstream_error"}}'),
            'request content removed': lambda r, f: f['requests'][1]['body'].update(input=[]),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                altered_results, altered_final = copy.deepcopy(results), copy.deepcopy(final)
                mutate(altered_results, altered_final)
                self.assertFalse(pressure.evaluate_queue(runs, altered_results, before, altered_final)['passed'])

    def test_stream_affinity_requires_terminal_usage_and_output_for_the_same_request(self):
        response = pressure.correlation_response('first')
        result = {'status': 200, 'events': [
            {'type': 'response.output_text.delta', 'delta': pressure.CONTENT_SENTINEL + ':first'},
            {'type': 'response.completed', 'response': response}]}
        self.assertTrue(pressure.response_matches(result, 'first', stream=True))
        self.assertFalse(pressure.response_matches(result, 'second', stream=True))
        duplicate = copy.deepcopy(result)
        duplicate['events'].append(duplicate['events'][-1])
        self.assertFalse(pressure.response_matches(duplicate, 'first', stream=True))
        missing_usage = copy.deepcopy(result)
        del missing_usage['events'][-1]['response']['usage']
        self.assertFalse(pressure.response_matches(missing_usage, 'first', stream=True))
        missing_sentinel = copy.deepcopy(result)
        missing_sentinel['events'][0]['delta'] = 'first'
        self.assertFalse(pressure.response_matches(missing_sentinel, 'first', stream=True))

    def run_driver(self, docker, extra=()):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'pressure.json'
            argv = ['pressure_pilot.py', '--output', str(output), *extra]
            with patch.object(pilot, 'docker', docker), patch.object(sys, 'argv', argv), \
                    patch.object(pilot.platform, 'machine', return_value='arm64'), \
                    contextlib.redirect_stdout(io.StringIO()):
                pilot.main(study=pressure)
            return json.loads(output.read_text())

    def test_shared_driver_uses_pressure_config_and_keeps_isolation_and_cleanup(self):
        docker = FakeDocker()
        copied_config = []
        def capture(*args, **kwargs):
            if args[0] == 'cp' and args[2].endswith(':/app/data/'):
                copied_config.append(json.loads((Path(args[1]) / 'config.json').read_text()))
            result = docker(*args, **kwargs)
            if args[0] == 'exec' and '--suite' in args:
                return json.dumps({**json.loads(result), 'study': pressure.NAME, 'pacing_verified': False})
            return result
        report = self.run_driver(capture)
        self.assertEqual(report['surface'], 'azure-passthrough')
        self.assertEqual(report['expected_case_names'], ['queue_capacity', 'stream_lifetime'])
        self.assertEqual(report['scope'], pressure.SCOPE)
        self.assertEqual(report['study'], 'provider-pressure')
        self.assertFalse(report['pacing_verified'])
        self.assertTrue(report['all_synthetic_gates_passed'])
        provider = copied_config[0]['providers']['azure']
        self.assertEqual(provider['concurrency_and_buffer_size'], {'concurrency': 1, 'buffer_size': 1})
        self.assertEqual(provider['network_config']['max_retries'], 0)
        self.assertTrue(copied_config[0]['client']['drop_excess_requests'])
        creates = [call for call in docker.calls if call[0] == 'create']
        self.assertEqual(creates[0][-3:], ('python3', '/tmp/pressure_pilot.py', '--mock'))
        self.assertIn('@sha256:', creates[1][-1])
        self.assertTrue(all('--cap-drop' in call and '--memory' in call for call in creates))
        self.assertTrue(any(call[:3] == ('network', 'create', '--internal') for call in docker.calls))
        containers = [call[call.index('--name') + 1] for call in creates]
        cleanup = [call for call in docker.calls if call[0] == 'rm']
        self.assertEqual(cleanup, [('rm', '-f', '-v', name) for name in reversed(containers)])
        self.assertEqual(report['cleanup_errors'], [])

    def test_incompatible_surface_fails_before_any_docker_call(self):
        docker = FakeDocker()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.run_driver(docker, ('--surface', 'converted'))
        self.assertEqual(docker.calls, [])

    def test_partial_study_cannot_claim_complete_gates(self):
        report = self.run_driver(FakeDocker(), ('--cases', 'stream_lifetime'))
        self.assertFalse(report['matrix_complete'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_study_keeps_content_leak_gate(self):
        report = self.run_driver(FakeDocker(logs='Bifrost v2.2.0 ' + pressure.CONTENT_SENTINEL))
        self.assertTrue(report['synthetic_content_in_console_logs'])
        self.assertFalse(report['all_synthetic_gates_passed'])


if __name__ == '__main__':
    unittest.main()
