import concurrent.futures
import base64
import http.client
import socket
import subprocess
import sys
import http.server
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

spec = importlib.util.spec_from_file_location('gateway_service', Path(__file__).resolve().parents[1] / 'ModelHarbor/Support/gateway_service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
control = service._control


class RecordingLaunchctl:
    def __init__(self, registered=False, fail=False):
        self.exists = registered
        self.fail = fail
        self.calls = []

    def registered(self, target):
        self.calls.append(('print', target))
        return self.exists

    def bootstrap(self, domain, path):
        self.calls.append(('bootstrap', domain, path))
        if self.fail:
            raise service.ServiceError('Synthetic service-manager failure')
        self.exists = True


class GatewayServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'grok_adapter.py').write_text('VALUE = 1\n')
        (self.source / 'task_repair.py').write_text('VALUE = 2\n')
        (self.source / 'nested').mkdir()
        (self.source / 'nested' / 'provider.py').write_text('VALUE = 3\n')
        self.state = self.root / 'state'
        self.config = self.root / 'config'
        self.token = self.root / 'token'
        self.token.write_text('synthetic-private-token')
        self.token.chmod(0o600)
        self.proof_secret = control.ensure_proof_secret(self.token)
        self.launcher = RecordingLaunchctl()

    def ensure(self, launcher=None, probe=None):
        return service.ensure_service(self.source, self.state, self.config, self.token, 49123,
            Path('/fixture/python3'), self.root, launcher or self.launcher, probe or (lambda *_: None))

    def test_existing_gateway_reports_matching_bundle_without_writes(self):
        digest = service.runtime_digest(service.inventory(self.source))
        existing = {'state': 'attached', 'mode': 'independent', 'runtime_id': digest,
                    'maintenance_required': False}
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}
        result = self.ensure(probe=lambda *_: existing)
        after = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(result['candidate_runtime_id'], digest)
        self.assertFalse(result['maintenance_required'])
        self.assertEqual(before, after)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.launcher.calls, [])

    def test_existing_gateway_reports_different_bundle_without_promoting(self):
        existing = {'state': 'attached', 'mode': 'independent', 'runtime_id': 'a' * 64,
                    'maintenance_required': False}
        result = self.ensure(probe=lambda *_: existing)
        self.assertTrue(result['maintenance_required'])
        self.assertEqual(result['runtime_id'], 'a' * 64)
        self.assertEqual(result['candidate_runtime_id'], service.runtime_digest(service.inventory(self.source)))
        self.assertFalse(existing['maintenance_required'])
        self.assertFalse(self.state.exists())
        self.assertEqual(self.launcher.calls, [])

    def test_new_service_retains_all_python_files_and_declares_exact_identity(self):
        result = self.ensure()
        self.assertEqual(result['state'], 'registered')
        runtime = self.state / 'runtimes' / result['runtime_id']
        self.assertEqual(service.inventory(runtime), service.inventory(self.source))
        job = plistlib.loads((self.state / 'service-49123.plist').read_bytes())
        self.assertEqual(job['ProgramArguments'], ['/fixture/python3', '-B', '-u', str(runtime / 'grok_adapter.py')])
        self.assertEqual(job['EnvironmentVariables']['MODEL_HARBOR_INDEPENDENT'], '1')
        self.assertEqual(job['EnvironmentVariables']['MODEL_HARBOR_RUNTIME_DIGEST'], result['runtime_id'])
        self.assertNotIn('synthetic-private-token', json.dumps(result) + repr(job))
        self.assertEqual([call[0] for call in self.launcher.calls], ['print', 'bootstrap'])

    def test_duplicate_attempts_register_only_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.ensure(), range(6)))
        self.assertEqual(sum(call[0] == 'bootstrap' for call in self.launcher.calls), 1)
        self.assertTrue(all(result['state'] == 'registered' for result in results))
        self.assertEqual(len(list((self.state / 'runtimes').iterdir())), 1)

    def test_existing_job_preserves_old_runtime_when_source_changes(self):
        first = self.ensure()
        original = (self.state / 'service-49123.plist').read_bytes()
        (self.source / 'grok_adapter.py').write_text('VALUE = 999\n')
        self.ensure()
        self.assertEqual((self.state / 'service-49123.plist').read_bytes(), original)
        self.assertEqual((self.state / 'runtimes' / first['runtime_id'] / 'grok_adapter.py').read_text(), 'VALUE = 1\n')
        self.assertEqual(sum(call[0] == 'bootstrap' for call in self.launcher.calls), 1)

    def test_failed_registration_retries_original_verified_runtime(self):
        failed = RecordingLaunchctl(fail=True)
        with self.assertRaises(service.ServiceError):
            self.ensure(launcher=failed)
        original = (self.state / 'service-49123.plist').read_bytes()
        (self.source / 'grok_adapter.py').write_text('VALUE = 999\n')
        self.ensure()
        self.assertEqual((self.state / 'service-49123.plist').read_bytes(), original)
        self.assertEqual([call[0] for call in failed.calls], ['print', 'bootstrap'])

    def test_corrupt_retained_runtime_is_not_bootstrapped(self):
        failed = RecordingLaunchctl(fail=True)
        with self.assertRaises(service.ServiceError):
            self.ensure(launcher=failed)
        runtime = next((self.state / 'runtimes').iterdir())
        (runtime / 'task_repair.py').write_text('VALUE = 999\n')
        with self.assertRaisesRegex(service.ServiceError, 'integrity'):
            self.ensure()
        self.assertEqual([call[0] for call in self.launcher.calls], ['print'])

    def test_extra_unverified_runtime_file_blocks_registration(self):
        failed = RecordingLaunchctl(fail=True)
        with self.assertRaises(service.ServiceError):
            self.ensure(launcher=failed)
        runtime = next((self.state / 'runtimes').iterdir())
        (runtime / 'provider_usage.so').write_bytes(b'unverified')
        with self.assertRaisesRegex(service.ServiceError, 'unverified file'):
            self.ensure()
        self.assertEqual([call[0] for call in self.launcher.calls], ['print'])

    def test_changed_config_does_not_replace_prior_registration_attempt(self):
        failed = RecordingLaunchctl(fail=True)
        with self.assertRaises(service.ServiceError):
            self.ensure(launcher=failed)
        self.config = self.root / 'different-config'
        with self.assertRaisesRegex(service.ServiceError, 'maintenance'):
            self.ensure()
        self.assertEqual([call[0] for call in self.launcher.calls], ['print'])

    def test_legacy_attach_does_not_prepare_files_or_register(self):
        expected = {'state': 'attached', 'mode': 'legacy', 'maintenance_required': True}
        self.assertEqual(self.ensure(probe=lambda *_: expected), expected)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.launcher.calls, [])

    def test_foreign_listener_error_does_not_register(self):
        def foreign(*_):
            raise service.ServiceError('Foreign listener')
        with self.assertRaisesRegex(service.ServiceError, 'Foreign'):
            self.ensure(probe=foreign)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.launcher.calls, [])

    def test_runtime_links_and_syntax_errors_fail_before_registration(self):
        for invalid in ('link', 'syntax', 'fifo'):
            with self.subTest(invalid=invalid):
                path = self.source / 'task_repair.py'
                path.unlink()
                if invalid == 'link':
                    path.symlink_to(self.token)
                elif invalid == 'syntax':
                    path.write_text('not python ?!')
                else:
                    os.mkfifo(path)
                with self.assertRaises((service.ServiceError, OSError)):
                    self.ensure()
                self.assertFalse(any(call[0] == 'bootstrap' for call in self.launcher.calls))

    def test_source_change_during_copy_does_not_publish(self):
        original = service.inventory
        count = 0
        def changing(path):
            nonlocal count
            count += 1
            if count == 3:
                (self.source / 'grok_adapter.py').write_text('VALUE = 88\n')
            return original(path)
        with patch.object(service, 'inventory', side_effect=changing):
            with self.assertRaisesRegex(service.ServiceError, 'changed'):
                self.ensure()
        self.assertEqual(list((self.state / 'runtimes').iterdir()), [])
        self.assertFalse(any(call[0] == 'bootstrap' for call in self.launcher.calls))

    def test_private_state_symlink_and_world_readable_state_are_rejected(self):
        self.state.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(service.ServiceError):
            self.ensure()
        self.state.unlink()
        self.state.mkdir(mode=0o755)
        with self.assertRaises(service.ServiceError):
            self.ensure()

    def runtime(self, mode='independent'):
        return {'protocol_version': 1, 'runtime_id': 'a' * 64,
                'boot_id': str(uuid.uuid4()), 'mode': mode}

    def start_server(self, value=None, proof_mode='valid', close_after_proof=False, port=0):
        observed = []
        token = 'synthetic-private-token'
        secret = self.proof_secret
        if value is None:
            value = {'routing': 'per-task', 'providers': {}, 'runtime': self.runtime()}
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass
            def reply(self, value, status=200, close=False):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                if close:
                    self.send_header('Connection', 'close')
                    self.close_connection = True
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                observed.append({'path': self.path, 'token': self.headers.get('X-Model-Harbor-Token'),
                                 'body': body, 'peer': self.client_address})
                if self.path == '/harbor/handshake':
                    if proof_mode == 'legacy':
                        return self.reply({'error': 'unsupported'}, 404, close=True)
                    runtime = dict(value.get('runtime') or {})
                    nonce = json.loads(body)['nonce']
                    binding = control.connection_binding(self.connection, server=True)
                    if proof_mode == 'foreign_socket':
                        binding['client_port'] += 1
                    signed_nonce = '0' * 64 if proof_mode == 'stale_nonce' else nonce
                    proof_key = token if proof_mode == 'public_token' else secret
                    signature = control.proof_digest(proof_key, signed_nonce, runtime, binding)
                    if proof_mode == 'wrong':
                        signature = '0' * 64
                    if proof_mode == 'wrong_identity':
                        runtime['boot_id'] = str(uuid.uuid4())
                    self.reply({'runtime': runtime, 'connection': binding, 'proof': signature},
                               close=close_after_proof)
                elif self.path == '/harbor/status':
                    self.reply(value)
                elif self.path == '/harbor/usage':
                    self.reply({'entries': []})
                else:
                    self.reply({'configured': True})
            do_GET = handle_request
            do_POST = handle_request
        server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, observed

    def test_authenticated_independent_and_proven_legacy_status(self):
        for mode in ('independent', 'legacy'):
            value = {'routing': 'per-task', 'providers': {}, 'runtime': self.runtime(mode)}
            server, observed = self.start_server(value)
            result = service.listener_status(server.server_port, self.token)
            self.assertEqual(result['mode'], mode)
            self.assertEqual(result['maintenance_required'], mode == 'legacy')
            self.assertIsNone(observed[0]['token'])
            self.assertEqual(observed[1]['token'], 'synthetic-private-token')
            self.assertEqual(observed[0]['peer'], observed[1]['peer'])
            self.assertNotIn('synthetic-private-token', json.dumps(result))

    def test_fake_listener_never_receives_token_or_provider_payload(self):
        server, observed = self.start_server(proof_mode='wrong')
        payload = b'{"key":"synthetic-provider-secret"}'
        with self.assertRaisesRegex(control.ControlError, 'authentication'):
            control.owner_request('POST', '/harbor/providers/openrouter', payload,
                                  server.server_port, self.token)
        self.assertEqual([entry['path'] for entry in observed], ['/harbor/handshake'])
        self.assertIsNone(observed[0]['token'])
        self.assertNotIn(b'synthetic-provider-secret', observed[0]['body'])
        with self.assertRaises(service.ServiceError):
            service.listener_status(server.server_port, self.token)
        self.assertTrue(all(entry['token'] is None for entry in observed))

    def test_old_legacy_without_server_proof_is_left_untouched(self):
        server, observed = self.start_server(proof_mode='legacy')
        with self.assertRaisesRegex(service.ServiceError, 'maintenance'):
            self.ensure(probe=lambda *_: service.listener_status(server.server_port, self.token))
        self.assertFalse(self.state.exists())
        self.assertEqual(self.launcher.calls, [])
        self.assertEqual(len(observed), 1)
        self.assertIsNone(observed[0]['token'])

    def test_listener_knowing_inference_token_cannot_prove_server_ownership(self):
        server, observed = self.start_server(proof_mode='public_token')
        with self.assertRaisesRegex(control.ControlError, 'authentication'):
            control.owner_request('POST', '/harbor/providers/azure', b'{"key":"private"}',
                                  server.server_port, self.token)
        self.assertEqual(len(observed), 1)
        self.assertIsNone(observed[0]['token'])
        self.assertNotIn(b'private', observed[0]['body'])

    def test_private_proof_secret_creation_is_persistent_exclusive_and_concurrent(self):
        path = control.proof_secret_path(self.token)
        path.unlink()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: control.ensure_proof_secret(self.token), range(16)))
        self.assertEqual(len(set(values)), 1)
        self.assertEqual(control.ensure_proof_secret(self.token), values[0])
        self.assertEqual(control.read_proof_secret(self.token), values[0])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.token.read_text(), 'synthetic-private-token')
        self.assertNotEqual(values[0], self.token.read_text())
        self.assertEqual(list(self.root.glob('.token.server-proof.*')), [])

    def test_missing_proof_secret_is_never_created_by_control_client(self):
        path = control.proof_secret_path(self.token)
        path.unlink()
        server, observed = self.start_server()
        with self.assertRaisesRegex(control.ControlError, 'server-proof secret'):
            control.owner_request('GET', '/harbor/status', b'', server.server_port, self.token)
        self.assertFalse(path.exists())
        self.assertEqual(observed, [])

    def test_unsafe_proof_secret_is_not_replaced_or_used(self):
        path = control.proof_secret_path(self.token)
        path.chmod(0o644)
        before = path.read_text()
        with self.assertRaises(control.ControlError):
            control.ensure_proof_secret(self.token)
        self.assertEqual(path.read_text(), before)
        path.unlink()
        path.symlink_to(self.token)
        with self.assertRaises(control.ControlError):
            control.ensure_proof_secret(self.token)
        self.assertTrue(path.is_symlink())
        self.assertEqual(self.token.read_text(), 'synthetic-private-token')

    def test_usage_uses_proven_connection_and_never_discloses_proof_secret(self):
        server, observed = self.start_server()
        result, _ = control.owner_request('GET', '/harbor/usage', b'', server.server_port, self.token)
        self.assertEqual(json.loads(result), {'entries': []})
        self.assertEqual([item['path'] for item in observed], ['/harbor/handshake', '/harbor/usage'])
        self.assertIsNone(observed[0]['token'])
        self.assertEqual(observed[0]['peer'], observed[1]['peer'])
        self.assertEqual(control.COMMAND_TIMEOUTS[('GET', '/harbor/usage')], 120)
        self.assertLessEqual(control.MAX_RESPONSE, 2 * 1024 * 1024)

    def test_nonce_identity_and_connection_binding_reject_replayed_proofs(self):
        for mode in ('stale_nonce', 'wrong_identity', 'foreign_socket'):
            with self.subTest(mode=mode):
                server, observed = self.start_server(proof_mode=mode)
                with self.assertRaises(control.ControlError):
                    control.owner_request('POST', '/harbor/providers/azure', b'{"key":"private"}',
                                          server.server_port, self.token)
                self.assertEqual(len(observed), 1)
                self.assertIsNone(observed[0]['token'])

    def test_closed_proof_connection_does_not_receive_control(self):
        server, observed = self.start_server(close_after_proof=True)
        with self.assertRaises(control.ControlError):
            control.owner_request('POST', '/harbor/providers/openrouter', b'{"key":"private"}',
                                  server.server_port, self.token)
        self.assertEqual(len(observed), 1)
        self.assertIsNone(observed[0]['token'])

    def test_listener_replacement_cannot_trigger_reconnection(self):
        original, observed = self.start_server()
        owner = self
        fake_requests = []
        base = http.client.HTTPConnection
        class ReplacementConnection(base):
            def request(self, method, url, *args, **kwargs):
                if url != '/harbor/handshake':
                    original.shutdown()
                    original.server_close()
                    replacement, recorded = owner.start_server(proof_mode='wrong', port=original.server_port)
                    fake_requests.append(recorded)
                    self.sock.close()
                    self.sock = None
                return super().request(method, url, *args, **kwargs)
        with patch.object(control.http.client, 'HTTPConnection', ReplacementConnection):
            with self.assertRaises(control.ControlError):
                control.owner_request('POST', '/harbor/providers/openrouter', b'{"key":"private"}',
                                      original.server_port, self.token)
        self.assertEqual(len(observed), 1)
        self.assertEqual(fake_requests, [[]])

    def test_legitimate_control_uses_authenticated_socket_and_stdin_cli(self):
        server, observed = self.start_server()
        secret = 'synthetic-provider-secret'
        body = json.dumps({'key': secret}).encode()
        request = {'method': 'POST', 'path': '/harbor/providers/openrouter',
                   'body_base64': base64.b64encode(body).decode()}
        environment = dict(os.environ, MODEL_HARBOR_TOKEN_PATH=str(self.token),
                           MODEL_HARBOR_PORT=str(server.server_port))
        result = subprocess.run([sys.executable, '-B', str(Path(control.__file__))],
                                input=json.dumps(request).encode(), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=environment, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['ok'])
        self.assertEqual(json.loads(base64.b64decode(value['body_base64'])), {'configured': True})
        self.assertIsNone(observed[0]['token'])
        self.assertEqual(observed[1]['token'], 'synthetic-private-token')
        self.assertEqual(observed[1]['body'], body)
        self.assertEqual(observed[0]['peer'], observed[1]['peer'])
        self.assertNotIn(secret.encode(), result.stdout + result.stderr)
        self.assertNotIn('synthetic-private-token'.encode(), result.stdout + result.stderr)
        self.assertNotIn(self.proof_secret.encode(), result.stdout + result.stderr + b''.join(item['body'] for item in observed))

    def test_missing_token_on_occupied_port_never_looks_free(self):
        server, observed = self.start_server()
        self.token.unlink()
        with self.assertRaises(service.ServiceError):
            service.listener_status(server.server_port, self.token)
        self.assertEqual([item['path'] for item in observed], ['/harbor/handshake'])
        self.assertIsNone(observed[0]['token'])

    def test_launchctl_commands_are_read_or_bootstrap_only(self):
        launcher = service.Launchctl()
        with patch.object(service.subprocess, 'run') as run:
            run.return_value.returncode = 113
            self.assertFalse(launcher.registered('gui/123/fixture'))
            run.return_value.returncode = 0
            launcher.bootstrap('gui/123', Path('/fixture/job.plist'))
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['/bin/launchctl', 'print', 'gui/123/fixture'],
            ['/bin/launchctl', 'bootstrap', 'gui/123', '/fixture/job.plist']])


if __name__ == '__main__':
    unittest.main()
