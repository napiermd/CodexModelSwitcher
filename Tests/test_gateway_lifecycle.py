"""Real adapter entrypoint, isolated HOME/state/ports, synthetic Azure transport."""
import concurrent.futures
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import socketserver
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

if os.environ.get('HARBOR_TEST_BUNDLE_RESOURCES'):
    sys.dont_write_bytecode = True
SUPPORT = Path(os.environ.get('HARBOR_TEST_BUNDLE_RESOURCES', Path(__file__).resolve().parents[1] / 'ModelHarbor/Support')).resolve()
sys.path.insert(0, str(SUPPORT))
from gateway_service import inventory, runtime_digest, retain_runtime
from gateway_control import owner_request


class LocalServer(http.server.ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('HARBOR_TEST_BUNDLE_RESOURCES'):
            signature = self.bundle_snapshot()
            self.addCleanup(lambda: self.assertEqual(self.bundle_snapshot(), signature))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.token = 'synthetic-owner-token'
        (self.config / 'token').write_text(self.token)
        (self.config / 'model-switcher.json').write_text(json.dumps({'services': [
            {'id': 'azure', 'baseURL': 'https://fixture.openai.azure.com/openai/v1',
             'models': [{'id': 'a'}, {'id': 'b'}]}]}))
        (self.config / 'model-catalogs').mkdir()
        (self.config / 'model-catalogs/azure.json').write_text(json.dumps({'models': [
            {'slug': model, 'default_reasoning_level': 'none'} for model in ('a', 'b')]}))
        self.sentinel = self.config / 'history.jsonl'
        self.sentinel.write_bytes(b'{"private":"synthetic history must stay unchanged"}\n')
        self.history = self.sentinel.read_bytes()
        self.requests = []
        self.release = threading.Event()
        self.started = threading.Event()
        owner = self

        class Upstream(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *_):
                pass
            def do_POST(self):
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(value)
                mode = value.get('input')
                if self.headers.get('api-key') == 'invalid':
                    body = b'{"error":"synthetic invalid credentials"}'
                    self.send_response(401)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif value.get('stream'):
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    event = {'type': 'response.output_text.delta', 'delta': value['model']}
                    self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
                    self.wfile.flush()
                    owner.started.set()
                    if mode == 'partial':
                        self.close_connection = True
                        return
                    owner.release.wait(8)
                    end = {'type': 'response.completed', 'response': {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}]}}
                    try:
                        self.wfile.write(('data: ' + json.dumps(end) + '\n\n').encode())
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    self.close_connection = True
                else:
                    body = json.dumps({'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}], 'model': value['model']}).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(body)
        self.upstream = LocalServer(('127.0.0.1', 0), Upstream)
        self.upstream.daemon_threads = True
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.addCleanup(self.stop_upstream)
        # Test-only transport hook. The process executes the unmodified production main.
        (self.root / 'sitecustomize.py').write_text('''import urllib.request
original = urllib.request.OpenerDirector.open
def redirect(self, req, *args, **kwargs):
    if isinstance(req, urllib.request.Request) and req.full_url.startswith('https://fixture.openai.azure.com/'):
        req = urllib.request.Request('http://127.0.0.1:PORT/responses', data=req.data, headers=dict(req.headers), method=req.get_method())
    return original(self, req, *args, **kwargs)
urllib.request.OpenerDirector.open = redirect
'''.replace('PORT', str(self.upstream.server_port)))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.gui_source = self.root / 'GUI.app/Resources'
        shutil.copytree(SUPPORT, self.gui_source, ignore=shutil.ignore_patterns('__pycache__'))
        self.retained, self.digest = retain_runtime(self.gui_source, self.state)
        self.env = {'HOME': str(self.root), 'CODEX_HOME': str(self.config), 'PATH': '/usr/bin:/bin',
                    'PYTHONPATH': str(self.root), 'MODEL_HARBOR_PORT': str(self.port),
                    'MODEL_HARBOR_CONFIG_DIR': str(self.config), 'MODEL_HARBOR_TOKEN_PATH': str(self.config / 'token'),
                    'MODEL_HARBOR_STATE_DIR': str(self.state), 'MODEL_HARBOR_INDEPENDENT': '1',
                    'MODEL_HARBOR_RUNTIME_DIGEST': self.digest}
        self.log = (self.root / 'gateway.log').open('wb')
        self.addCleanup(self.log.close)
        self.process = None
        self.addCleanup(self.stop_gateway)
        self.start_gateway()
        self.configure()

    @staticmethod
    def bundle_snapshot():
        return {str(path.relative_to(SUPPORT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in SUPPORT.rglob('*') if path.is_file()}

    def stop_upstream(self):
        self.release.set()
        self.upstream.shutdown()
        self.upstream.server_close()

    def stop_gateway(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.assertEqual(self.sentinel.read_bytes(), self.history)

    def start_gateway(self, parent=False, keep_parent=False):
        command = [sys.executable, '-B', '-u', str(self.retained / 'grok_adapter.py')]
        if keep_parent:
            self.launcher = subprocess.Popen([sys.executable, '-c',
                'import subprocess,sys; p=subprocess.Popen(sys.argv[1:],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid,flush=True); sys.stdin.buffer.read(1)', *command],
                env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.detached_pid = int(self.launcher.stdout.readline())
            self.launcher.stdout.close()
            self.addCleanup(self.stop_launcher)
            self.addCleanup(self.kill_detached)
        elif parent:
            # The launcher exits immediately, exactly the previous failure trigger.
            runner = subprocess.run([sys.executable, '-c',
                'import subprocess,sys; p=subprocess.Popen(sys.argv[1:],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid)', *command],
                env=self.env, capture_output=True, text=True, timeout=5, check=True)
            self.detached_pid = int(runner.stdout)
            self.addCleanup(self.kill_detached)
        else:
            self.process = subprocess.Popen(command, env=self.env, stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                code, value = self.request('GET', '/harbor/status')
                if code == 200:
                    return value
            except (OSError, http.client.HTTPException):
                pass
            if not parent and not keep_parent and self.process.poll() is not None:
                self.fail('Production gateway exited: ' + (self.root / 'gateway.log').read_text())
            time.sleep(.04)
        self.fail('Production gateway did not start')

    def stop_launcher(self):
        if self.launcher.stdin and not self.launcher.stdin.closed:
            self.launcher.stdin.close()
        self.launcher.wait(timeout=5)

    def kill_detached(self):
        import signal
        try:
            os.kill(self.detached_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def request(self, method, path, payload=None, token=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=12)
        try:
            conn.request(method, path, body=json.dumps(payload).encode() if payload is not None else None,
                         headers={'Authorization': 'Bearer ' + (self.token if token is None else token)})
            response = conn.getresponse()
            body = response.read()
            try:
                value = json.loads(body)
            except ValueError:
                value = body
            return response.status, value
        finally:
            conn.close()

    def configure(self, key='synthetic-azure-secret'):
        body, runtime = owner_request('POST', '/harbor/providers/azure',
            json.dumps({'endpoint': 'https://fixture.openai.azure.com', 'key': key}).encode(),
            self.port, self.config / 'token')
        self.assertTrue(json.loads(body)['configured'])
        self.assertEqual(runtime['mode'], 'independent')

    def infer(self, task='first', turn='one', model='a', stream=False, text='hello'):
        return self.request('POST', '/harbor/v1/responses', {'model': 'harbor/azure/' + model,
            'input': text, 'stream': stream, 'client_metadata': {'thread_id': task, 'turn_id': turn}})

    def test_parent_exit_and_tool_gap_keep_original_owner(self):
        self.stop_gateway()
        status = self.start_gateway(parent=True)
        self.configure()
        boot = status['runtime']['boot_id']
        self.assertEqual(self.infer()[1]['model'], 'a')
        shutil.rmtree(self.gui_source.parent)
        # Longer than the legacy two-second parent watcher, with no HTTP inference active.
        time.sleep(2.2)
        status = self.request('GET', '/harbor/status')[1]
        self.assertEqual(status['runtime']['active_requests'], 0)
        self.assertEqual(status['runtime']['unresolved_turns'], 1)
        self.assertEqual(self.infer(model='b')[1]['model'], 'a')
        self.assertEqual(self.infer(task='second', model='b')[1]['model'], 'b')
        self.assertEqual(self.request('GET', '/harbor/status')[1]['runtime']['boot_id'], boot)

    def test_control_client_reconnects_to_same_proven_runtime(self):
        first, identity = owner_request('GET', '/harbor/status', b'', self.port, self.config / 'token')
        second, reopened = owner_request('GET', '/harbor/status', b'', self.port, self.config / 'token')
        self.assertEqual(identity, reopened)
        self.assertEqual(json.loads(first)['runtime']['boot_id'], json.loads(second)['runtime']['boot_id'])
        self.assertFalse(json.loads(second)['runtime']['retirement_allowed'])

    def test_ui_parent_exits_during_two_active_streams(self):
        self.stop_gateway()
        status = self.start_gateway(keep_parent=True)
        self.configure()
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            pending = [pool.submit(self.infer, task, 'one', model, True)
                       for task, model in [('first', 'a'), ('second', 'b')]]
            deadline = time.monotonic() + 5
            while len(self.requests) < 2 and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(len(self.requests), 2)
            self.stop_launcher()
            shutil.rmtree(self.gui_source.parent)
            time.sleep(2.2)
            current = self.request('GET', '/harbor/status')[1]['runtime']
            self.assertEqual(current['boot_id'], status['runtime']['boot_id'])
            self.assertEqual(current['active_requests'], 2)
            self.release.set()
            for response in pending:
                code, body = response.result(timeout=5)
                self.assertEqual(code, 200)
                self.assertIn(b'response.completed', body)
        self.assertEqual(self.infer(model='b')[1]['model'], 'a')

    def test_two_streams_survive_refused_update_and_false_readiness(self):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            a = pool.submit(self.infer, 'first', 'one', 'a', True)
            b = pool.submit(self.infer, 'second', 'one', 'b', True)
            self.assertTrue(self.started.wait(5))
            for action in ('promote', 'retire', 'rollback', 'shutdown'):
                self.assertEqual(self.request('POST', '/harbor/runtime/' + action, {})[0], 409)
            self.release.set()
            for future in (a, b):
                code, body = future.result(timeout=5)
                self.assertEqual(code, 200)
                self.assertEqual(body.count(b'response.completed'), 1)
        status = self.request('GET', '/harbor/status')[1]
        self.assertEqual(status['runtime']['unresolved_turns'], 2)
        self.assertFalse(status['runtime']['promotion_allowed'])
        self.assertFalse(status['runtime']['retirement_allowed'])

    def test_credentials_are_not_readiness_and_probes_are_explicit(self):
        status = self.request('GET', '/harbor/status')[1]
        self.assertTrue(status['providers']['credentials_available']['azure'])
        self.assertFalse(status['providers']['azure_ready'])
        self.assertEqual(len(self.requests), 0)
        self.assertEqual(self.request('POST', '/harbor/verify', {'model': 'harbor/azure/a'}, token='wrong')[0], 401)
        code, proof = self.request('POST', '/harbor/verify', {'model': 'harbor/azure/a'})
        self.assertEqual(code, 200)
        self.assertTrue(proof['verified'])
        self.assertEqual(proof['boot_id'], status['runtime']['boot_id'])
        for _ in range(3):
            self.assertTrue(self.request('GET', '/harbor/status')[1]['providers']['azure_ready'])
        self.assertEqual(len(self.requests), 1)
        self.configure('invalid')
        self.assertFalse(self.request('GET', '/harbor/status')[1]['providers']['azure_ready'])
        code, proof = self.request('POST', '/harbor/verify', {'model': 'harbor/azure/a'})
        self.assertEqual(code, 503)
        self.assertEqual(proof['result'], 'auth_failed')
        self.assertNotIn('synthetic-azure-secret', json.dumps(status))

    def test_partial_stream_is_not_replayed_and_remains_uncertain_after_restart(self):
        code, body = self.infer(stream=True, text='partial')
        self.assertEqual(code, 200)
        self.assertIn(b'response.output_text.delta', body)
        self.assertNotIn(b'response.completed', body)
        self.assertEqual(self.infer()[0], 400)
        self.stop_gateway()
        self.start_gateway()
        self.configure()
        self.assertEqual(self.infer()[0], 400)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.request('GET', '/harbor/status')[1]['runtime']['uncertain_turns'], 1)

    def test_duplicate_start_does_not_change_existing_gateway(self):
        boot = self.request('GET', '/harbor/status')[1]['runtime']['boot_id']
        other = subprocess.run([sys.executable, '-B', str(self.retained / 'grok_adapter.py')], env=self.env,
                               capture_output=True, timeout=5)
        self.assertNotEqual(other.returncode, 0)
        self.assertIn(b'Another gateway owns', other.stderr)
        self.assertEqual(self.request('GET', '/harbor/status')[1]['runtime']['boot_id'], boot)
        self.assertEqual(self.infer()[0], 200)

    def test_occupied_port_and_wrong_artifact_never_replace_listener(self):
        boot = self.request('GET', '/harbor/status')[1]['runtime']['boot_id']
        alternate = self.root / 'candidate-state'
        env = dict(self.env, MODEL_HARBOR_STATE_DIR=str(alternate))
        failed = subprocess.run([sys.executable, '-B', str(self.retained / 'grok_adapter.py')],
                                env=env, capture_output=True, timeout=5)
        self.assertNotEqual(failed.returncode, 0)
        env['MODEL_HARBOR_RUNTIME_DIGEST'] = 'f' * 64
        failed = subprocess.run([sys.executable, '-B', str(self.retained / 'grok_adapter.py')],
                                env=env, capture_output=True, timeout=5)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(b'does not match', failed.stderr)
        self.assertEqual(self.request('GET', '/harbor/status')[1]['runtime']['boot_id'], boot)
        self.assertEqual(self.infer()[0], 200)

    def test_client_cancel_never_dispatches_request_again(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request('POST', '/harbor/v1/responses', body=json.dumps({
            'model': 'harbor/azure/a', 'input': 'partial', 'stream': True,
            'client_metadata': {'thread_id': 'cancelled', 'turn_id': 'one'}}),
            headers={'Authorization': 'Bearer ' + self.token})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.readline())
        conn.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.request('GET', '/harbor/status')[1]['runtime']
            if status['active_requests'] == 0:
                break
            time.sleep(.02)
        self.assertEqual(self.infer(task='cancelled')[0], 400)
        self.assertEqual(len(self.requests), 1)

    def test_account_change_refuses_continuation_without_dispatch(self):
        self.assertEqual(self.infer()[0], 200)
        self.configure('another-synthetic-account')
        self.assertEqual(self.infer()[0], 400)
        self.assertEqual(len(self.requests), 1)
        self.configure()
        self.assertEqual(self.infer()[0], 200)


if __name__ == '__main__':
    unittest.main()
