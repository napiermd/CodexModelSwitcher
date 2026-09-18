import copy
import http.client
import http.server
import socket
import time
import concurrent.futures
import io
import json
from pathlib import Path
import threading
import unittest
import urllib.error
from unittest.mock import patch

from Tests import test_provider_connections as fixtures

bridge = fixtures.bridge


class RestoreTests(unittest.TestCase):
    stop = fixtures.ProviderConnectionsTests.stop
    request = fixtures.ProviderConnectionsTests.request

    def setUp(self):
        fixtures.ProviderConnectionsTests.setUp(self)
        self.endpoint = 'https://fixture.openai.azure.com/openai/v1'
        (self.root / 'model-switcher.json').write_text(json.dumps({'services': [
            {'id': 'azure', 'baseURL': self.endpoint, 'models': [{'id': 'coding-prod'}]},
            {'id': 'openrouter', 'models': [{'id': 'fixture/coder'}]}]}))
        (self.root / 'model-catalogs').mkdir()
        (self.root / 'model-catalogs/azure.json').write_text(json.dumps({'models': [
            {'slug': 'coding-prod', 'default_reasoning_level': 'high'}]}))
        self.runtime = bridge._runtime_module.GatewayRuntime(self.root / 'state', 'a' * 64, bridge.READINESS.boot_id)
        self.addCleanup(self.runtime.close)
        for name, value in [('RUNTIME', self.runtime), ('AZURE_CONNECTION', None),
                            ('AZURE_ADMISSION', bridge._admission_module.AdmissionQueue())]:
            p = patch.object(bridge, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.payload = {'expected_runtime': self.identity(),
                        'expected_configuration_revision': bridge.configuration_revision(),
                        'connections': {'azure': {'endpoint': self.endpoint, 'key': 'synthetic-azure-key'},
                                        'openrouter': {'key': 'synthetic-router-key'}},
                        'required_models': ['harbor/azure/coding-prod', 'harbor/openrouter/fixture/coder']}

    def identity(self):
        return {k: self.runtime.status()[k] for k in ('protocol_version', 'runtime_id', 'boot_id', 'mode')}

    def restore(self, payload=None, headers=None):
        return self.request(path='/harbor/providers/restore', body=payload or self.payload, headers=headers)

    @staticmethod
    def response(body=b'{"status":"completed","output":[]}'):
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json'}
        return Response(body)

    def test_batch_publishes_only_after_every_probe_and_preserves_files_and_journal(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file() and 'state' not in p.parts}
        observed = []
        def open_request(request, **kwargs):
            self.assertIsNone(bridge.AZURE_CONNECTION)
            self.assertEqual(bridge.OPENROUTER_KEY, '')
            observed.append(request)
            return self.response()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = open_request
            status, value = self.restore()
        self.assertEqual(status, 200, value)
        self.assertEqual(value, {'runtime': self.identity(), 'configuration_revision': bridge.configuration_revision(),
                                'restored': ['azure', 'openrouter'], 'already_present': [],
                                'routes': [{'model': model, 'verified': True} for model in self.payload['required_models']]})
        self.assertEqual([r.full_url for r in observed], [self.endpoint + '/responses', 'https://openrouter.ai/api/v1/responses'])
        self.assertEqual([json.loads(r.data)['model'] for r in observed], ['coding-prod', 'fixture/coder'])
        self.assertEqual(json.loads(observed[0].data)['reasoning'], {'effort': 'high'})
        self.assertEqual(json.loads(observed[1].data)['provider'], {'require_parameters': True})
        self.assertEqual(observed[0].get_header('Api-key'), 'synthetic-azure-key')
        self.assertEqual(observed[1].get_header('Authorization'), 'Bearer synthetic-router-key')
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(self.runtime.status()['unresolved_turns'], 0)
        self.assertEqual(self.runtime.status()['untracked_admissions'], 0)
        proofs = bridge.provider_status()['route_verification']
        self.assertEqual(len(proofs), 2)
        self.assertTrue(all(p['verified'] and p['configuration_revision'] == value['configuration_revision'] for p in proofs))
        self.assertNotIn('synthetic-', json.dumps(value))

    def test_second_failed_probe_publishes_nothing_and_redacts_upstream(self):
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = [self.response(), urllib.error.HTTPError(
                'https://openrouter.ai/api/v1/responses', 401, 'synthetic-router-key', {}, io.BytesIO(b'synthetic-azure-key'))]
            status, value = self.restore()
        self.assertEqual(status, 503)
        self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.OPENROUTER_KEY, '')
        self.assertEqual(bridge.provider_status()['route_verification'], [])
        self.assertNotIn('synthetic-', json.dumps(value))
        self.assertEqual(opener.return_value.open.call_count, 2)

    def test_context_and_occupancy_conflicts_are_rejected_before_probe(self):
        for change in ('boot', 'runtime', 'revision', 'occupied'):
            with self.subTest(change=change):
                payload = copy.deepcopy(self.payload)
                if change == 'boot': payload['expected_runtime']['boot_id'] = '00000000-0000-4000-8000-000000000000'
                if change == 'runtime': payload['expected_runtime']['runtime_id'] = 'b' * 64
                if change == 'revision': payload['expected_configuration_revision'] = '0' * 64
                with patch.object(bridge, 'OPENROUTER_KEY', 'other-key' if change == 'occupied' else ''):
                    if change == 'occupied': payload['expected_configuration_revision'] = bridge.configuration_revision()
                    with patch.object(bridge.urllib.request, 'build_opener') as opener:
                        self.assertEqual(self.restore(payload)[0], 409)
                        opener.assert_not_called()
                self.assertIsNone(bridge.AZURE_CONNECTION)

    def test_untracked_admission_blocks_missing_credentials(self):
        self.runtime.pin(None, lambda: {'provider': 'openrouter', 'model': 'fixture/coder'})
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            self.assertEqual(self.restore()[0], 409)
            opener.assert_not_called()

    def test_untracked_admission_during_staged_probe_prevents_publication(self):
        def open_request(request, **kwargs):
            self.runtime.pin(None, lambda: {'provider': 'azure', 'model': 'coding-prod'})
            return self.response()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = open_request
            self.assertEqual(self.restore()[0], 409)
        self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.OPENROUTER_KEY, '')

    def test_validation_and_authorization_never_probe_or_mutate(self):
        mutations = [
            lambda p: p.update(extra=True),
            lambda p: p['connections'].update(grok={'key': 'unaccepted'}),
            lambda p: p['connections']['openrouter'].update(model='other'),
            lambda p: p['connections']['openrouter'].update(key=''),
            lambda p: p['connections']['openrouter'].update(key='bad\nkey'),
            lambda p: p['connections']['azure'].update(endpoint='https://other.openai.azure.com'),
            lambda p: p.update(required_models=['harbor-selected']),
            lambda p: p.update(required_models=['harbor/azure/missing', 'harbor/openrouter/fixture/coder']),
            lambda p: p.update(required_models=['harbor/azure/coding-prod']),
            lambda p: p.update(required_models=['harbor/azure/coding-prod'] * 9),
            lambda p: p.update(required_models=[{}]),
            lambda p: p.update(connections={}),
            lambda p: p['expected_runtime'].update(mode='legacy'),
            lambda p: p['expected_runtime'].update(extra=True),
        ]
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            for mutate in mutations:
                payload = copy.deepcopy(self.payload)
                mutate(payload)
                with self.subTest(payload=payload):
                    self.assertEqual(self.restore(payload)[0], 400)
            for headers in ({}, {'X-Model-Harbor-Token': 'wrong'},
                            {'X-Model-Harbor-Token': 'synthetic-owner-token', 'Origin': 'https://example.test'}):
                self.assertEqual(self.restore(headers=headers)[0], 401)
            opener.assert_not_called()
        self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.OPENROUTER_KEY, '')

    def test_single_provider_preserves_unspecified_connection_and_only_proves_selected_route(self):
        bridge.OPENROUTER_KEY = 'existing-router-key'
        prior = {'provider': 'openrouter', 'model': 'fixture/coder'}
        revision = bridge.configuration_revision()
        bridge.READINESS.record(prior, revision, 'verified')
        payload = copy.deepcopy(self.payload)
        payload['expected_configuration_revision'] = revision
        payload['connections'].pop('openrouter')
        payload['required_models'] = ['harbor/azure/coding-prod']
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = self.response()
            status, result = self.restore(payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['restored'], ['azure'])
        self.assertEqual(result['routes'], [{'model': 'harbor/azure/coding-prod', 'verified': True}])
        self.assertEqual(bridge.OPENROUTER_KEY, 'existing-router-key')
        self.assertEqual(opener.return_value.open.call_count, 1)
        status = bridge.provider_status()
        self.assertTrue(status['azure_ready'])
        self.assertFalse(status['openrouter_ready'])

    def test_identical_connections_are_a_no_op_even_with_retained_ownership(self):
        bridge.OPENROUTER_KEY = self.payload['connections']['openrouter']['key']
        bridge.AZURE_CONNECTION = dict(self.payload['connections']['azure'])
        self.runtime.pin('retained', lambda: {'provider': 'azure', 'model': 'coding-prod'})
        revision = bridge.configuration_revision()
        self.payload['expected_configuration_revision'] = revision
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            status, value = self.restore()
        self.assertEqual(status, 200, value)
        self.assertEqual(value['restored'], [])
        self.assertEqual(value['already_present'], ['azure', 'openrouter'])
        self.assertEqual(value['configuration_revision'], revision)
        self.assertEqual(self.runtime.status()['unresolved_turns'], 1)

    def test_old_runtime_uncertain_rows_survive_successful_restore(self):
        self.runtime.close()
        old = bridge._runtime_module.GatewayRuntime(self.root / 'state', 'b' * 64, 'old-boot')
        old.pin('old-turn', lambda: {'provider': 'azure', 'model': 'coding-prod'})
        old.begin('old-turn', 'original-private-binding')
        old.close()
        self.runtime = bridge._runtime_module.GatewayRuntime(self.root / 'state', 'a' * 64, bridge.READINESS.boot_id)
        self.addCleanup(self.runtime.close)
        with patch.object(bridge, 'RUNTIME', self.runtime), patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            status, value = self.restore()
        self.assertEqual(status, 200, value)
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.runtime.status()['recovered_uncertain_requests'], 1)
        with self.assertRaisesRegex(ValueError, 'another runtime'):
            self.runtime.pin('old-turn', lambda: self.fail('old owner reselected'))
        self.assertEqual(self.runtime.db.execute('SELECT binding FROM turns WHERE id=?', ('old-turn',)).fetchone()[0],
                         'original-private-binding')
        self.assertFalse(self.runtime.status()['promotion_allowed'])
        self.assertFalse(self.runtime.status()['retirement_allowed'])

    def test_configuration_change_during_probe_prevents_commit(self):
        changed = self.root / 'model-catalogs/other.json'
        def open_request(*args, **kwargs):
            changed.write_text('{"models":[]}')
            return self.response()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = open_request
            status, value = self.restore()
        self.assertEqual(status, 409, value)
        self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.OPENROUTER_KEY, '')
        self.assertEqual(bridge.provider_status()['route_verification'], [])

    def test_concurrent_configure_is_preserved_when_restore_loses_revision(self):
        def open_request(*args, **kwargs):
            self.assertEqual(self.request(body={'key': 'concurrent-connection'})[0], 200)
            return self.response()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = open_request
            status, value = self.restore()
        self.assertEqual(status, 409, value)
        self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.OPENROUTER_KEY, 'concurrent-connection')

    def test_competing_restores_never_publish_a_mixed_batch(self):
        other = copy.deepcopy(self.payload)
        other['connections']['azure']['key'] = 'second-azure'
        other['connections']['openrouter']['key'] = 'second-router'
        barrier = threading.Barrier(2)
        def open_request(request, **kwargs):
            if request.get_header('Api-key'):
                barrier.wait(timeout=2)
            return self.response()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = open_request
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                calls = [pool.submit(self.restore, payload) for payload in (self.payload, other)]
                results = [call.result(timeout=4) for call in calls]
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        winner = self.payload if results[0][0] == 200 else other
        self.assertEqual(bridge.AZURE_CONNECTION, winner['connections']['azure'])
        self.assertEqual(bridge.OPENROUTER_KEY, winner['connections']['openrouter']['key'])
        self.assertEqual(opener.return_value.open.call_count, 4)

    def test_invalid_probe_responses_and_deadline_do_not_publish(self):
        for body in (b'not json synthetic-azure-key', b'[]', b'{"status":"in_progress"}'):
            with self.subTest(body=body), patch.object(bridge.urllib.request, 'build_opener') as opener:
                opener.return_value.open.return_value = self.response(body)
                self.assertEqual(self.restore()[0], 503)
                self.assertIsNone(bridge.AZURE_CONNECTION)
                self.assertEqual(bridge.OPENROUTER_KEY, '')
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = bridge._transport_module.RequestDeadline('private transport detail')
            status, value = self.restore()
        self.assertEqual(status, 503)
        self.assertNotIn('private transport detail', json.dumps(value))
        self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.OPENROUTER_KEY, '')
        self.assertEqual(opener.return_value.open.call_count, 1)

    def inference(self):
        return self.request(path='/harbor/v1/responses', body={'model': 'harbor/azure/coding-prod',
            'input': 'Synthetic original history', 'stream': False,
            'client_metadata': {'thread_id': 'retained-task', 'turn_id': 'retained-turn'}},
            headers={'Authorization': 'Bearer synthetic-owner-token'})

    def restart(self):
        self.runtime.close()
        bridge.READINESS = bridge._runtime_module.RouteReadiness()
        self.runtime = bridge._runtime_module.GatewayRuntime(self.root / 'state', 'a' * 64, bridge.READINESS.boot_id)
        self.addCleanup(self.runtime.close)
        bridge.RUNTIME = self.runtime
        bridge.AZURE_CONNECTION = None
        bridge.OPENROUTER_KEY = ''
        self.payload['expected_runtime'] = self.identity()
        self.payload['expected_configuration_revision'] = bridge.configuration_revision()

    def wait_for_finished_requests(self):
        deadline = time.monotonic() + 2
        while self.runtime.status()['active_requests'] and time.monotonic() < deadline:
            threading.Event().wait(.01)
        self.assertEqual(self.runtime.status()['active_requests'], 0)

    def test_original_tracked_binding_resumes_after_same_runtime_restart_and_restore(self):
        bridge.AZURE_CONNECTION = dict(self.payload['connections']['azure'])
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.inference()[0], 200)
            self.wait_for_finished_requests()
            original = self.runtime.db.execute('SELECT id,route,runtime,binding,uncertain FROM turns').fetchall()
            self.restart()
            self.assertEqual(self.restore()[0], 200)
            self.assertEqual(self.runtime.db.execute('SELECT id,route,runtime,binding,uncertain FROM turns').fetchall(), original)
            self.assertEqual(self.inference()[0], 200)
            self.wait_for_finished_requests()
            self.assertEqual(opener.return_value.open.call_count, 4)
        self.assertEqual(self.runtime.status()['untracked_admissions'], 0)

    def test_valid_different_saved_key_cannot_dispatch_original_tracked_history(self):
        bridge.AZURE_CONNECTION = dict(self.payload['connections']['azure'])
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.inference()[0], 200)
            self.wait_for_finished_requests()
            binding = self.runtime.db.execute('SELECT binding FROM turns').fetchone()[0]
            self.restart()
            self.payload['connections']['azure']['key'] = 'valid-other-account-key'
            self.assertEqual(self.restore()[0], 200)
            calls = opener.return_value.open.call_count
            status, value = self.inference()
            self.assertEqual(status, 400, value)
            self.assertIn('Account or route configuration changed', value['error'])
            self.assertEqual(opener.return_value.open.call_count, calls)
        self.assertEqual(self.runtime.db.execute('SELECT binding FROM turns').fetchone()[0], binding)

    def test_uncertain_tracked_history_remains_refused_after_restore(self):
        source = {'client_metadata': {'thread_id': 'retained-task', 'turn_id': 'retained-turn'}}
        key = bridge._runtime_module.turn_key(source, {})
        self.runtime.pin(key, lambda: {'provider': 'azure', 'model': 'coding-prod'})
        self.runtime.begin(key, 'original-binding')
        self.restart()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.restore()[0], 200)
            calls = opener.return_value.open.call_count
            status, value = self.inference()
            self.assertEqual(status, 400, value)
            self.assertIn('uncertain', value['error'])
            self.assertEqual(opener.return_value.open.call_count, calls)
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)

    def test_disconnected_restore_client_cancels_staged_probe_without_publishing(self):
        entered, release = threading.Event(), threading.Event()
        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Length', '1000')
                self.end_headers()
                self.wfile.write(b'{')
                self.wfile.flush()
                entered.set()
                release.wait(3)
        upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        upstream.daemon_threads = True
        thread = threading.Thread(target=lambda: upstream.serve_forever(poll_interval=.01), daemon=True)
        thread.start()
        real_opener = bridge.urllib.request.build_opener
        observed = []
        def make_opener(*handlers):
            delegate = real_opener(*handlers)
            class RedirectToFixture:
                def open(self, request, **kwargs):
                    observed.append(request)
                    request.full_url = 'http://127.0.0.1:' + str(upstream.server_port) + '/responses'
                    return delegate.open(request, **kwargs)
            return RedirectToFixture()
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            with patch.object(bridge.urllib.request, 'build_opener', side_effect=make_opener):
                client.request('POST', '/harbor/providers/restore', body=json.dumps(self.payload).encode(),
                               headers={'X-Model-Harbor-Token': 'synthetic-owner-token'})
                self.assertTrue(entered.wait(2))
                client.sock.shutdown(socket.SHUT_RDWR)
                client.close()
                deadline = time.monotonic() + 2
                while bridge.AZURE_ADMISSION.snapshot()['active'] and time.monotonic() < deadline:
                    threading.Event().wait(.01)
                self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['active'], 0)
                self.assertIsNone(bridge.AZURE_CONNECTION)
                self.assertEqual(bridge.OPENROUTER_KEY, '')
                self.assertEqual(len(observed), 1)
                self.assertEqual(self.runtime.status()['untracked_admissions'], 0)
        finally:
            client.close()
            release.set()
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=2)

    def test_explicit_verification_never_records_an_ownership_admission(self):
        bridge.AZURE_CONNECTION = dict(self.payload['connections']['azure'])
        bridge.OPENROUTER_KEY = self.payload['connections']['openrouter']['key']
        source = {'client_metadata': {'thread_id': 'existing-task', 'turn_id': 'existing-turn'}}
        key = bridge._runtime_module.turn_key(source, {})
        self.runtime.pin(key, lambda: {'provider': 'azure', 'model': 'coding-prod'})
        before = self.runtime.status()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            for model in self.payload['required_models']:
                status, value = self.request(path='/harbor/verify', body={'model': model}, headers={
                    'X-Model-Harbor-Token': 'synthetic-owner-token',
                    'x-codex-turn-metadata': json.dumps({'thread_id': 'probe-must-not-pin', 'turn_id': 'probe'})})
                self.assertEqual(status, 200, value)
            self.assertEqual(self.runtime.status(), before)
            status, value = self.request(path='/harbor/v1/responses', body={
                'model': 'harbor/azure/coding-prod', 'input': 'Untracked synthetic request', 'stream': False},
                headers={'Authorization': 'Bearer synthetic-owner-token'})
            self.assertEqual(status, 200, value)
            self.wait_for_finished_requests()
        self.assertEqual(self.runtime.status()['untracked_admissions'], 1)
        self.assertEqual(self.runtime.status()['unresolved_turns'], 1)

    def test_slow_drip_restore_body_cannot_outlive_total_deadline(self):
        done = threading.Event()
        original = bridge.Handler.restore_saved_connections
        def bounded(handler):
            try:
                return original(handler)
            finally:
                done.set()
        client = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
        try:
            with patch.object(bridge, 'RESTORE_SECONDS', .2), \
                 patch.object(bridge.Handler, 'restore_saved_connections', bounded), \
                 patch.object(bridge.urllib.request, 'build_opener') as opener:
                client.sendall(b'POST /harbor/providers/restore HTTP/1.1\r\nHost: localhost\r\n'
                               b'X-Model-Harbor-Token: synthetic-owner-token\r\nContent-Length: 1000\r\n\r\n{')
                started = time.monotonic()
                while not done.wait(.03) and time.monotonic() - started < 1:
                    try:
                        client.sendall(b' ')
                    except OSError:
                        break
                self.assertTrue(done.wait(.5))
                self.assertLess(time.monotonic() - started, 1)
                opener.assert_not_called()
                self.assertIsNone(bridge.AZURE_CONNECTION)
                self.assertEqual(bridge.OPENROUTER_KEY, '')
        finally:
            client.close()

    def test_authenticated_control_helper_restores_through_real_handler(self):
        bridge.TOKEN_PATH.chmod(0o600)
        bridge._control_module.ensure_proof_secret(bridge.TOKEN_PATH)
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            raw, runtime = bridge._control_module.owner_request(
                'POST', '/harbor/providers/restore', json.dumps(self.payload).encode(),
                self.server.server_port, bridge.TOKEN_PATH, expected_runtime=self.identity())
        self.assertEqual(runtime, self.identity())
        value = json.loads(raw)
        self.assertEqual(value['runtime'], runtime)
        self.assertEqual(value['restored'], ['azure', 'openrouter'])
        self.assertEqual(value['configuration_revision'], bridge.configuration_revision())
        self.assertEqual(opener.return_value.open.call_count, 2)
