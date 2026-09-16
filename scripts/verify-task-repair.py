#!/usr/bin/env python3
"""Reproduce and repair a task-provider mismatch using Codex and local mock endpoints.

Requires the Codex CLI on PATH (verified with 0.150.1). All model requests use
loopback HTTP and synthetic keys. Uses a temporary Codex home, reads no account
credentials, and never modifies real tasks. Run separately from the unit suite.
"""
import http.server
import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'harbor/baseten/verification-model'
spec = importlib.util.spec_from_file_location('repair', ROOT / 'scripts/repair-task-provider.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class Endpoint(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['content-length'])))
        self.server.requests.append((self.path, body['model']))
        if self.path.startswith('/native/'):
            data = json.dumps({'error': {'message': 'Synthetic native provider refuses Harbor model',
                                         'type': 'invalid_request_error'}}).encode()
            self.send_response(400)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        item = {'type': 'message', 'id': 'msg_test', 'status': 'completed', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': 'HARBOR_REPAIR_OK', 'annotations': []}]}
        response = {'id': 'resp_test', 'object': 'response', 'status': 'completed', 'output': [item],
                    'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}
        events = [
            {'type': 'response.created', 'response': dict(response, status='in_progress', output=[])},
            {'type': 'response.output_item.added', 'output_index': 0,
             'item': dict(item, status='in_progress', content=[])},
            {'type': 'response.output_text.delta', 'item_id': 'msg_test', 'output_index': 0,
             'content_index': 0, 'delta': 'HARBOR_REPAIR_OK'},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
            {'type': 'response.completed', 'response': response},
        ]
        data = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Client:
    def __init__(self, root):
        self.events = queue.Queue()
        self.pending = []
        self.count = 0
        self.process = subprocess.Popen(
            [shutil.which('codex'), 'app-server', '--stdio'], cwd=root,
            env=dict(os.environ, CODEX_HOME=str(root), HARBOR_TEST_KEY='synthetic-local-test',
                     OPENAI_API_KEY='synthetic-local-test'),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.reader = threading.Thread(target=self.read_events, daemon=True)
        self.reader.start()

    def read_events(self):
        for line in self.process.stdout:
            self.events.put(json.loads(line))

    def initialize(self):
        self.rpc('initialize', {'clientInfo': {'name': 'harbor_repair_test', 'version': '1'},
                                'capabilities': {'experimentalApi': True}})
        self.process.stdin.write('{"method":"initialized"}\n')
        self.process.stdin.flush()

    def rpc(self, method, params):
        self.count += 1
        self.process.stdin.write(json.dumps({'id': self.count, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        while True:
            event = self.events.get(timeout=30)
            if event.get('id') == self.count:
                if 'error' in event:
                    raise RuntimeError(event['error'])
                return event['result']
            self.pending.append(event)

    def turn(self, task_id, text):
        self.pending.clear()
        self.rpc('turn/start', {'threadId': task_id, 'input': [{'type': 'text', 'text': text}]})
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            event = self.pending.pop(0) if self.pending else self.events.get(timeout=30)
            if event.get('method') == 'turn/completed':
                return event['params']['turn']
        raise TimeoutError('Codex did not complete the synthetic turn.')

    def close(self):
        self.process.terminate()
        self.process.wait(timeout=10)
        self.reader.join(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()


def verify(root, server):
    catalog = root / 'catalog.json'
    entry = json.loads((ROOT / 'examples/baseten-models.json').read_text())['models'][0]
    entry.update(slug=MODEL, display_name='Synthetic repair test',
                 base_instructions='Reply with the requested marker.')
    catalog.write_text(json.dumps({'models': [entry]}))
    (root / 'config.toml').write_text(f'''
openai_base_url = "http://127.0.0.1:{server.server_port}/native/v1"
model = "{MODEL}"
model_provider = "model-harbor"
model_catalog_json = {json.dumps(str(catalog))}
[features]
apps = false
[model_providers.model-harbor]
name = "Model Harbor test"
base_url = "http://127.0.0.1:{server.server_port}/harbor/v1"
wire_api = "responses"
env_key = "HARBOR_TEST_KEY"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
''')
    first = Client(root)
    try:
        first.initialize()
        started = first.rpc('thread/start', {
            'cwd': str(root), 'modelProvider': 'openai', 'model': MODEL,
            'historyMode': 'paginated', 'approvalPolicy': 'never', 'sandbox': 'read-only'})
        task_id = started['thread']['id']
        before = first.turn(task_id, 'Synthetic marker: PRESERVE_THIS_HISTORY. Reply HARBOR_REPAIR_OK.')
        assert started['modelProvider'] == 'openai'
        assert before['status'] == 'failed', before['status']
        assert server.requests[-1] == ('/native/v1/responses', MODEL)
        print('BEFORE: existing task fails through native provider.', flush=True)
    finally:
        first.close()
    plan = repair.task_plan(root, task_id)
    assert plan['metadata']['payload']['history_mode'] == 'paginated'
    # This isolated home has no running client. Real CLI repairs always keep the process guard.
    result = repair.apply_plan(root, plan, process_check=lambda: [])
    assert result['conversation_unchanged']
    assert b'PRESERVE_THIS_HISTORY' in Path(plan['rollout']).read_bytes()
    print('REPAIR: only provider changed; conversation bytes preserved.', flush=True)
    second = Client(root)
    try:
        second.initialize()
        resumed = second.rpc('thread/resume', {'threadId': task_id, 'excludeTurns': True})
        assert resumed['modelProvider'] == 'model-harbor', resumed['modelProvider']
        assert resumed['thread']['id'] == task_id
        after = second.turn(task_id, 'Continue the synthetic routing verification only.')
        assert after['status'] == 'completed', after['status']
        assert server.requests[-1] == ('/harbor/v1/responses', MODEL)
        print('AFTER: same task resumes through Harbor and completes.', flush=True)
    finally:
        second.close()


def main():
    if shutil.which('codex') is None:
        raise SystemExit('Install Codex CLI and add it to PATH before running this verification.')
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='harbor-repair-e2e-') as temporary:
            verify(Path(temporary).resolve(), server)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
