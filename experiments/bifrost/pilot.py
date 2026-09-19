#!/usr/bin/env python3
"""Pinned Bifrost transport against a loopback synthetic Azure server. No live keys."""
import argparse
import copy
import http.client
import http.server
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent
DEADLINE = 12
SURFACES = {
    'converted': {'path': '/openai/v1/responses', 'model': 'azure/pilot-model',
                  'max_retries': 1, 'retry_owner': 'bifrost'},
    'azure-passthrough': {'path': '/azure_passthrough/openai/v1/responses',
                          'model': 'pilot-deployment', 'max_retries': 0,
                          'retry_owner': 'none; caller policy tested separately'},
}
CASES = ('basic', 'stream', 'image', 'custom_tool', 'custom_history',
         'function_history', 'reasoning_history', 'previous_response',
         'error429', 'error500', 'error502', 'error503', 'error529',
         'pre_output_failure', 'partial_failure', 'cancel', 'stall',
         'opaque_history', 'opaque_response', 'opaque_stream', 'invalid_auth',
         'invalid_encrypted', 'partial_disconnect', 'concurrent_workers')
PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII='


def docker(*args, timeout=60):
    return subprocess.check_output(['docker', *args], text=True, timeout=timeout, stderr=subprocess.STDOUT).strip()


def completed(output=None):
    return {'id': 'resp_pilot', 'object': 'response', 'created_at': 1789660800,
            'status': 'completed', 'model': 'pilot-deployment', 'error': None,
            'incomplete_details': None, 'output': output or [
                {'id': 'msg_pilot', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                 'content': [{'type': 'output_text', 'text': 'pilot-ok', 'annotations': []}]}],
            'usage': {'input_tokens': 11, 'input_tokens_details': {'cached_tokens': 3},
                      'output_tokens': 7, 'output_tokens_details': {'reasoning_tokens': 2}, 'total_tokens': 18}}


def opaque_output():
    return [
        {'type': 'reasoning', 'id': 'rs_opaque', 'summary': [], 'encrypted_content': 'synthetic-encrypted'},
        {**completed()['output'][0], 'author': {'role': 'assistant', 'name': 'pilot-coder'},
         'recipient': 'pilot-architect', 'harbor_opaque': {'nested': ['synthetic', 17]}},
    ]


class MockAzure(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address=('127.0.0.1', 0)):
        super().__init__(address, MockHandler)
        self.lock = threading.Lock()
        self.requests = []
        self.cancelled = {}
        self.active = 0
        self.max_active = 0
        self.workers = threading.Barrier(3, timeout=4)

    def snapshot(self):
        with self.lock:
            return {'requests': copy.deepcopy(self.requests), 'cancelled': dict(self.cancelled),
                    'max_active': self.max_active}


class MockHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_json(200, self.server.snapshot() if self.path == '/snapshot' else {'data': []})

    def send_json(self, status, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def event(self, name, **payload):
        self.wfile.write(('event: ' + name + '\ndata: ' + json.dumps({'type': name, **payload}) + '\n\n').encode())
        self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        case = body.get('metadata', {}).get('harbor_pilot_case', 'unknown')
        request_id = body.get('metadata', {}).get('harbor_pilot_request', case)
        with self.server.lock:
            attempt = sum(r['case'] == case for r in self.server.requests) + 1
            self.server.active += 1
            self.server.max_active = max(self.server.max_active, self.server.active)
            record = {'case': case, 'path': self.path, 'body': body, 'request_id': request_id,
                      'attempt': attempt, 'at': time.monotonic(), 'active_at_start': self.server.active,
                      'synthetic_key_correct': self.headers.get('api-key') == 'synthetic-pilot-key'}
            self.server.requests.append(record)
        try:
            self.respond(body, case, attempt, record)
        except (BrokenPipeError, ConnectionResetError):
            if case == 'cancel':
                with self.server.lock:
                    self.server.cancelled[request_id] = time.monotonic()
        finally:
            with self.server.lock:
                record['finished_at'] = time.monotonic()
                self.server.active -= 1

    def respond(self, body, case, attempt, record):
        if not record['synthetic_key_correct']:
            self.send_json(401, {'error': {'message': 'synthetic rejected credential', 'code': 'invalid_api_key'}})
            return
        code = {'error429': 429, 'error500': 500, 'error502': 502, 'error503': 503,
                'error529': 529}.get(case)
        if code and attempt == 1:
            self.send_json(code, {'error': {'message': 'synthetic failure', 'type': 'server_error', 'code': str(code)}},
                           {'Retry-After': '1'} if code == 429 else {})
            return
        if case == 'invalid_encrypted':
            self.send_json(400, {'error': {'message': 'The encrypted content could not be verified.',
                                         'type': 'invalid_request_error', 'code': 'invalid_encrypted_content'}})
            return
        if case == 'stall':
            time.sleep(8)
        output = None
        if case in ('custom_tool', 'custom_history'):
            output = [{'type': 'custom_tool_call', 'id': 'ct_pilot', 'call_id': 'call_pilot',
                       'name': 'pilot_tool', 'input': 'synthetic tool input', 'status': 'completed'}]
        if case in ('opaque_response', 'opaque_stream'):
            output = opaque_output()
        response = completed(output)
        if case.startswith('worker_'):
            try:
                self.server.workers.wait()
            except threading.BrokenBarrierError:
                self.send_json(503, {'error': {'code': 'synthetic_concurrency_barrier_timeout'}})
                return
            with self.server.lock:
                record['worker_barrier_passed'] = True
            response['id'] = 'resp_' + record['request_id']
            response['output'][0]['content'][0]['text'] = record['request_id']
        if not body.get('stream'):
            self.send_json(200, response)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.event('response.created', response={**response, 'status': 'in_progress', 'output': []}, sequence_number=0)
        if case == 'pre_output_failure' and attempt == 1:
            self.event('response.failed', response={**response, 'status': 'failed', 'output': [],
                       'error': {'code': 'server_error', 'message': 'synthetic before output'}}, sequence_number=1)
            return
        sequence = 1
        for index, item in enumerate(response['output']):
            initial = {**item, 'status': 'in_progress'}
            if item['type'] == 'message':
                initial['content'] = []
            elif item['type'] == 'custom_tool_call':
                initial['input'] = ''
            elif item['type'] == 'reasoning':
                initial.pop('encrypted_content', None)
            self.event('response.output_item.added', output_index=index, item=initial, sequence_number=sequence)
            sequence += 1
            if item['type'] == 'custom_tool_call':
                self.event('response.custom_tool_call_input.delta', item_id=item['id'], output_index=index,
                           delta=item['input'], sequence_number=sequence)
                sequence += 1
            elif item['type'] == 'message':
                self.event('response.content_part.added', item_id=item['id'], output_index=index, content_index=0,
                           part={'type': 'output_text', 'text': '', 'annotations': []}, sequence_number=sequence)
                sequence += 1
                self.event('response.output_text.delta', item_id=item['id'], output_index=index, content_index=0,
                           delta='pilot-ok', sequence_number=sequence)
                sequence += 1
            if case == 'partial_failure':
                self.event('response.failed', response={**response, 'status': 'failed',
                           'error': {'code': 'server_error', 'message': 'synthetic after output'}}, sequence_number=sequence)
                return
            if case == 'partial_disconnect':
                return
            if case == 'cancel':
                for _ in range(100):
                    time.sleep(.1)
                    self.event('response.output_text.delta', item_id=item['id'], output_index=index, content_index=0,
                               delta='.', sequence_number=sequence)
                    sequence += 1
                return
            self.event('response.output_item.done', output_index=index, item=item, sequence_number=sequence)
            sequence += 1
        self.event('response.completed', response=response, sequence_number=sequence)


def config(mock_endpoint, surface='converted'):
    return {'governance': {}, 'client': {'enable_logging': False, 'disable_content_logging': True,
                       'retain_content_in_object_storage': False,
                       'allow_per_request_content_storage_override': False,
                       'allow_per_request_raw_override': False, 'dump_errors_in_console_logs': False},
            'config_store': {'enabled': True, 'type': 'sqlite', 'config': {'path': '/app/data/config.db'}},
            'logs_store': {'enabled': False},
            'framework': {'pricing': {'pricing_url': 'file:///app/data/empty.json',
                'model_parameters_url': 'file:///app/data/empty.json',
                'mcp_library_url': 'file:///app/data/empty-list.json',
                'mcp_library_sync_interval': 0, 'live_models_sync_interval': 0}},
            'plugins': [{'name': n, 'enabled': False} for n in ('logging', 'semantic_cache', 'telemetry', 'otel', 'maxim', 'governance')],
            'providers': {'azure': {'keys': [{'name': 'synthetic', 'value': 'synthetic-pilot-key', 'models': ['pilot-model', 'pilot-deployment'],
                'weight': 1, 'aliases': {'pilot-model': 'pilot-deployment'},
                'azure_key_config': {'endpoint': mock_endpoint}},
                {'name': 'synthetic-invalid', 'value': 'synthetic-wrong-key',
                 'models': ['pilot-invalid-deployment'], 'weight': 1,
                 'azure_key_config': {'endpoint': mock_endpoint}}],
                'network_config': {'max_retries': SURFACES[surface]['max_retries'], 'retry_backoff_initial': 100,
                                   'retry_backoff_max': 200, 'default_request_timeout_in_seconds': 3,
                                   'stream_idle_timeout_in_seconds': 5, 'allow_private_network': True}}}}


def request(case, stream=False, surface='converted'):
    value = {'model': SURFACES[surface]['model'], 'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG'}]}],
             'metadata': {'harbor_pilot_case': case, 'harbor_pilot_request': uuid.uuid4().hex}, 'stream': stream}
    if case == 'image':
        value['input'][0]['content'].append({'type': 'input_image', 'image_url': PIXEL, 'detail': 'low'})
    if case in ('custom_tool', 'custom_history'):
        value['tools'] = [{'type': 'custom', 'name': 'pilot_tool', 'description': 'Synthetic only', 'format': {'type': 'text'}}]
    if case == 'custom_history':
        value['input'] += [{'type': 'custom_tool_call', 'id': 'ct_before', 'call_id': 'call_before',
                           'name': 'pilot_tool', 'input': 'prior-input'},
                          {'type': 'custom_tool_call_output', 'call_id': 'call_before', 'output': 'prior-output'}]
    if case == 'function_history':
        value['tools'] = [{'type': 'function', 'name': 'pilot_function', 'parameters': {'type': 'object', 'properties': {}}}]
        value['input'] += [{'type': 'function_call', 'call_id': 'call_before', 'name': 'pilot_function', 'arguments': '{}'},
                          {'type': 'function_call_output', 'call_id': 'call_before', 'output': 'prior-output'}]
    if case in ('reasoning_history', 'invalid_encrypted'):
        value['input'].append({'type': 'reasoning', 'id': 'rs_pilot', 'summary': [], 'encrypted_content': 'synthetic-encrypted'})
        value['include'] = ['reasoning.encrypted_content']
    if case == 'opaque_history':
        value['input'] += [
            {'type': 'message', 'role': 'assistant', 'author': {'role': 'assistant', 'name': 'pilot-architect'},
             'recipient': 'pilot-coder', 'harbor_opaque': {'nested': ['synthetic', 17]}, 'content': [{'type': 'output_text', 'text': 'synthetic assignment'}]},
            {'type': 'reasoning', 'id': 'rs_opaque', 'summary': [], 'encrypted_content': 'synthetic-encrypted'},
        ]
        value['include'] = ['reasoning.encrypted_content']
    if case == 'invalid_auth':
        value['model'] = ('azure/' if surface == 'converted' else '') + 'pilot-invalid-deployment'
    if case == 'previous_response':
        value['previous_response_id'] = 'resp_before'
    return value


def decode_frame(lines):
    parts = [line[5:].lstrip() for line in lines if line.startswith(b'data:')]
    if not parts or parts == [b'[DONE]']:
        return None
    try:
        value = json.loads(b'\n'.join(parts))
        return value if isinstance(value, dict) else None
    except ValueError:
        return None


def perform(port, body, cancel=False, host="127.0.0.1", surface="converted",
            on_sent=None, on_event=None, path=None, headers=None, deadline=None, on_connect=None):
    start = time.monotonic()
    duration = DEADLINE if deadline is None else deadline
    conn = http.client.HTTPConnection(host, port, timeout=duration)
    transport_socket = [None]
    def expire():
        if transport_socket[0] is not None:
            try:
                transport_socket[0].shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    timer = threading.Timer(duration, expire)
    timer.daemon = True
    timer.start()
    chunks, events = [], []
    first_token, cancelled_at, response = None, None, None
    try:
        conn.connect()
        transport_socket[0] = conn.sock
        if on_connect is not None:
            on_connect(conn.sock)
        conn.request('POST', path or SURFACES[surface]['path'], json.dumps(body),
                     {'Content-Type': 'application/json', **(headers or {})})
        if on_sent is not None:
            on_sent()
        response = conn.getresponse()
        status = response.status
        headers = dict(response.getheaders())
        if body['stream'] and response.getheader('Content-Type', '').startswith('text/event-stream'):
            frame = []
            while line := response.readline():
                chunks.append(line)
                if line.strip():
                    frame.append(line.rstrip(b'\r\n'))
                    continue
                event = decode_frame(frame)
                frame = []
                if event is None:
                    continue
                events.append(event)
                if on_event is not None:
                    on_event(event)
                event_type = event.get('type')
                if event_type in ('response.output_text.delta', 'response.custom_tool_call_input.delta') and event.get('delta'):
                    if first_token is None:
                        first_token = round((time.monotonic() - start) * 1000, 2)
                    if cancel:
                        cancelled_at = time.monotonic()
                        expire()
                        break
            data = b''.join(chunks)
        else:
            data = response.read()
        return {'status': status, 'headers': headers, 'data': data.decode(errors='replace'), 'events': events,
                'elapsed_ms': round((time.monotonic() - start) * 1000, 2), 'first_output_ms': first_token,
                'cancelled_at': cancelled_at}
    except (OSError, http.client.HTTPException) as error:
        return {'status': None, 'error': type(error).__name__, 'elapsed_ms': round((time.monotonic() - start) * 1000, 2),
                'data': b''.join(chunks).decode(errors='replace'), 'first_output_ms': first_token,
                'events': events, 'cancelled_at': cancelled_at}
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        conn.close()


def changed_paths(expected, actual, path=''):
    if isinstance(expected, dict) and isinstance(actual, dict):
        return [p for key in sorted(expected.keys() | actual.keys())
                for p in ([path + '/' + key] if key not in expected or key not in actual
                          else changed_paths(expected[key], actual[key], path + '/' + key))]
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [p for index, (left, right) in enumerate(zip(expected, actual))
                for p in changed_paths(left, right, path + '/' + str(index))]
    return [] if expected == actual else [path]


def evaluate(case, body, result, seen, surface="converted"):
    ok = result.get('status') == 200
    attempts = [r for r in seen if r['case'] == case]
    wire = attempts[-1]['body'] if attempts else {}
    events = result.get('events', [])
    event_types = [e.get('type') for e in events]
    terminal = [e for e in events if e.get('type') in ('response.completed', 'response.failed', 'response.incomplete', 'error')]
    if body.get('stream'):
        response = terminal[-1].get('response', {}) if terminal else {}
    else:
        try:
            response = json.loads(result['data'])
        except ValueError:
            response = {}
    success = ok and response.get('status') == 'completed' and not response.get('error')
    checks = {'upstream_reached': bool(attempts), 'bounded_attempts': len(attempts) <= 1 + SURFACES[surface]['max_retries'],
              'bounded_deadline': result['elapsed_ms'] < (DEADLINE + 1) * 1000}
    if case.startswith('error'):
        if SURFACES[surface]['max_retries']:
            checks['recovered'] = success
            checks['one_retry'] = len(attempts) == 2
            if case == 'error429':
                checks['retry_after_honored'] = len(attempts) == 2 and attempts[1]['at'] - attempts[0]['at'] >= .95
        else:
            checks['upstream_status_preserved'] = result.get('status') == int(case[5:])
            checks['no_gateway_retry'] = len(attempts) == 1
            if case == 'error429':
                checks['retry_after_forwarded'] = {k.lower(): v for k, v in result.get('headers', {}).items()}.get('retry-after') == '1'
    elif case == 'invalid_auth':
        checks['incorrect_key_reached_upstream'] = bool(attempts) and all(not r['synthetic_key_correct'] for r in attempts)
        checks['auth_error_surfaces'] = result.get('status') == 401 and not success
        checks['no_auth_retry'] = len(attempts) == 1
    elif case == 'invalid_encrypted':
        checks['rejected_history_surfaces'] = result.get('status') == 400 and not success
        checks['no_history_rewrite_retry'] = len(attempts) == 1 and wire.get('input') == body['input']
    elif case == 'stall':
        checks['timeout_surfaces'] = not ok
    elif case in ('partial_failure', 'partial_disconnect'):
        checks['nonempty_output_received'] = result.get('first_output_ms') is not None
        checks['no_retry_after_output'] = len(attempts) == 1
        checks['no_false_completion'] = 'response.completed' not in event_types
        if case == 'partial_failure':
            checks['failure_surfaces'] = response.get('status') == 'failed' and 'response.failed' in event_types
    elif case == 'pre_output_failure':
        checks['failure_reported_or_recovered'] = success or 'response.failed' in event_types or (not ok and bool(response.get('error')))
    elif case == 'cancel':
        checks['nonempty_output_received'] = result.get('first_output_ms') is not None
        checks['no_terminal_success'] = 'response.completed' not in event_types
        checks['no_replay_after_cancel'] = len(attempts) == 1
    else:
        checks['http_success'] = ok
        checks['response_success'] = success
        checks['correct_path'] = bool(attempts) and attempts[-1]['path'].split('?')[0] == '/openai/v1/responses'
        checks['deployment_mapped'] = wire.get('model') == 'pilot-deployment'
        checks['synthetic_azure_auth'] = bool(attempts) and attempts[-1]['synthetic_key_correct']
        checks['usage_preserved'] = response.get('usage') == completed()['usage']
        if body.get('stream'):
            checks['one_terminal_completed'] = len(terminal) == 1 and event_types[-1:] == ['response.completed']
            checks['nonempty_output_received'] = result.get('first_output_ms') is not None
        if case == 'image':
            checks['image_unchanged'] = wire.get('input') == body['input']
        if case in ('custom_tool', 'custom_history'):
            checks['custom_definition_preserved'] = wire.get('tools') == body['tools']
            checks['custom_output_preserved'] = any(i.get('type') == 'custom_tool_call' and i.get('input') == 'synthetic tool input' for i in response.get('output', []))
            if body.get('stream'):
                checks['custom_delta_preserved'] = ''.join(e.get('delta', '') for e in events if e.get('type') == 'response.custom_tool_call_input.delta') == 'synthetic tool input'
        if case.endswith('_history'):
            checks['history_preserved'] = wire.get('input') == body['input']
        if case in ('reasoning_history', 'opaque_history'):
            checks['include_preserved'] = wire.get('include') == body['include']
            checks['encrypted_content_preserved'] = any(i.get('encrypted_content') == 'synthetic-encrypted' for i in wire.get('input', []))
        if case in ('opaque_response', 'opaque_stream'):
            checks['opaque_output_preserved'] = response.get('output') == opaque_output()
        if case == 'opaque_stream':
            expected_output = opaque_output()
            expected_added = []
            for index, item in enumerate(expected_output):
                initial = {**item, 'status': 'in_progress'}
                if item['type'] == 'message':
                    initial['content'] = []
                elif item['type'] == 'reasoning':
                    initial.pop('encrypted_content', None)
                expected_added.append((index, initial))
            checks['opaque_added_events_preserved'] = [(e.get('output_index'), e.get('item')) for e in events if e.get('type') == 'response.output_item.added'] == expected_added
            checks['opaque_done_events_preserved'] = [(e.get('output_index'), e.get('item')) for e in events if e.get('type') == 'response.output_item.done'] == list(enumerate(expected_output))
            checks['opaque_text_delta_preserved'] = [(e.get('output_index'), e.get('item_id'), e.get('delta')) for e in events if e.get('type') == 'response.output_text.delta'] == [(1, 'msg_pilot', 'pilot-ok')]
        if case == 'previous_response':
            checks['previous_id_preserved'] = wire.get('previous_response_id') == body['previous_response_id']
    return {'case': case, 'passed': all(checks.values()), 'checks': checks, 'upstream_attempts': len(attempts),
            'http_status': result.get('status'), 'elapsed_ms': result['elapsed_ms'],
            'first_output_ms': result.get('first_output_ms'), 'response_event_types': event_types,
            'error_type': result.get('error'),
            'input_changed_paths': changed_paths(body.get('input'), wire.get('input')),
            'retry_gap_ms': round((attempts[1]['at'] - attempts[0]['at']) * 1000, 2) if len(attempts) > 1 else None}


def main(study=None):
    case_names = study.CASES if study is not None else CASES
    parser = argparse.ArgumentParser(description=study.SCOPE if study is not None else __doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=case_names)
    parser.add_argument('--surface', choices=SURFACES,
                        default='azure-passthrough' if study is not None else 'converted')
    args = parser.parse_args()
    if study is not None and args.surface != 'azure-passthrough':
        parser.error('This study requires native Azure passthrough')
    pin = json.loads((HERE / 'pin.json').read_text())
    arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine())
    if arch not in pin['images']:
        raise SystemExit('No pinned image for this architecture')
    image = pin['images'][arch]
    name = 'harbor-bifrost-pilot-' + uuid.uuid4().hex[:10]
    mock_name, network = name + '-azure', name + '-network'
    result = {'schema_version': 2, 'scope': study.SCOPE if study is not None else 'synthetic Azure contract only', 'pin': pin,
              'image': image, 'live_azure_tested': False, 'surface': args.surface,
              'route': SURFACES[args.surface]['path'], 'retry_owner': SURFACES[args.surface]['retry_owner'],
              'max_retries': SURFACES[args.surface]['max_retries'],
              'client_total_deadline_seconds': DEADLINE, 'cases': [],
              'matrix_complete': False, 'all_synthetic_gates_passed': False,
              'production_decision': 'no-go: live Azure parity and latency unverified',
              'container_state': {}, 'cleanup_errors': []}
    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(result, indent=2) + '\n')
        temporary.replace(args.output)
    save()
    docker('image', 'inspect', image)
    created = []
    network_created = False
    with tempfile.TemporaryDirectory(prefix='harbor-bifrost-') as temp:
        root = Path(temp)
        root.chmod(0o755)
        (root/'empty.json').write_text('{}')
        (root/'empty-list.json').write_text('[]')
        provider_config = config('http://' + mock_name + ':8081', args.surface)
        if study is not None:
            provider_config = study.configure(provider_config)
        (root/'config.json').write_text(json.dumps(provider_config))
        fixture = study.prepare_fixture(root) if study is not None and hasattr(study, 'prepare_fixture') else None
        try:
            docker('network', 'create', '--internal', network)
            network_created = True
            docker('create', '--name', mock_name, '--network', network, '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--pids-limit', str(getattr(study, 'MOCK_PIDS', 64)),
                   '--memory', getattr(study, 'MOCK_MEMORY', '128m'),
                   '-e', 'HOME=/tmp', pin['mock_image'],
                   'python3', *(['/tmp/' + study.SCRIPT, '--mock'] if study is not None else ['/tmp/mock_server.py']))
            created.append(mock_name)
            docker('cp', str(HERE) + '/.', mock_name + ':/tmp/')
            if fixture is not None:
                docker('cp', str(fixture), mock_name + ':/tmp/')
            docker('start', mock_name)
            def probe(*arguments):
                return json.loads(docker('exec', mock_name, 'python3',
                                        '/tmp/' + study.SCRIPT if study is not None else '/tmp/probe.py',
                                        *arguments, timeout=180))
            def mock_snapshot():
                return probe('--snapshot')
            docker('create', '--name', name, '--network', network, '--init',
                   '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128',
                   '--memory', '1536m',
                   '-e', 'HOME=/app/data/home', '-e', 'LOG_LEVEL=info', image)
            created.append(name)
            docker('cp', str(root) + '/.', name + ':/app/data/')
            docker('start', name)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if probe('--health', name).get('ready'):
                    break
                time.sleep(.2)
            else:
                raise RuntimeError('Pinned Bifrost failed health readiness')
            cases = args.cases or list(case_names)
            result['expected_case_names'] = list(case_names)
            result.update(probe('--suite', name, args.surface, json.dumps(cases)))
            result['requested_cases_complete'] = [row.get('case') for row in result['cases']] == cases
            result['matrix_complete'] = not args.cases and result['requested_cases_complete']
            save()
            for row in result['cases']:
                print(json.dumps(row), flush=True)
            for container in created:
                result['container_state'][container] = json.loads(docker('inspect', '--format', '{{json .State}}', container, timeout=15))
            save()
            logs = docker('logs', name, timeout=15)
            result['transport_version_banner_verified'] = 'v2.2.0' in logs
            collected = root / 'collected'
            collected.mkdir()
            docker('cp', name + ':/app/data/.', str(collected))
            result['synthetic_content_in_console_logs'] = 'SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG' in logs
            result['runtime_files'] = sorted(str(p.relative_to(collected)) for p in collected.rglob('*') if p.is_file())
            result['synthetic_content_in_runtime_files'] = any(b'SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG' in p.read_bytes()
                                                             for p in collected.rglob('*') if p.is_file() and p.name != 'config.json')
            result['all_requested_case_checks_passed'] = (result['requested_cases_complete']
                                                         and all(row['passed'] for row in result['cases']))
            result['all_synthetic_gates_passed'] = (result['matrix_complete']
                                                   and result['all_requested_case_checks_passed']
                                                   and result['transport_version_banner_verified']
                                                   and not result['synthetic_content_in_console_logs']
                                                   and not result['synthetic_content_in_runtime_files']
                                                   and (not getattr(study, 'GATE_FIELD', None)
                                                        or result.get(study.GATE_FIELD) is True))
            save()
        except Exception as error:
            result['harness_error'] = str(error)
            if isinstance(error, subprocess.CalledProcessError):
                result['command_error_output'] = error.output[-5000:]
            for container in created:
                try:
                    result['container_state'][container] = json.loads(docker('inspect', '--format', '{{json .State}}', container, timeout=10))
                except Exception as inspection_error:
                    result['container_state'][container] = {'inspection_error': str(inspection_error)}
            save()
            raise
        finally:
            try:
                save()
            finally:
                for container in reversed(created):
                    try:
                        docker('rm', '-f', '-v', container, timeout=15)
                    except Exception as cleanup_error:
                        result['cleanup_errors'].append({'resource': container, 'error': str(cleanup_error)})
                if network_created:
                    try:
                        docker('network', 'rm', network, timeout=15)
                    except Exception as cleanup_error:
                        result['cleanup_errors'].append({'resource': network, 'error': str(cleanup_error)})
                save()


if __name__ == '__main__':
    main()
