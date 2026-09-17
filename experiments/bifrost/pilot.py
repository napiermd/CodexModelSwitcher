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
PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3ioAAAAASUVORK5CYII='


def docker(*args, timeout=60):
    return subprocess.check_output(['docker', *args], text=True, timeout=timeout, stderr=subprocess.STDOUT).strip()


def completed(output=None):
    return {'id': 'resp_pilot', 'object': 'response', 'created_at': 1789660800,
            'status': 'completed', 'model': 'pilot-deployment', 'error': None,
            'incomplete_details': None, 'output': output or [
                {'id': 'msg_pilot', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                 'content': [{'type': 'output_text', 'text': 'pilot-ok', 'annotations': []}]}],
            'usage': {'input_tokens': 2, 'output_tokens': 2, 'total_tokens': 4}}


class MockAzure(http.server.ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address=('127.0.0.1', 0)):
        super().__init__(address, MockHandler)
        self.lock = threading.Lock()
        self.requests = []
        self.cancelled = threading.Event()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.requests)


class MockHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_json(200, {'requests': self.server.snapshot(), 'cancelled': self.server.cancelled.is_set()} if self.path == '/snapshot' else {'data': []})

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
        with self.server.lock:
            attempt = sum(r['case'] == case for r in self.server.requests) + 1
            self.server.requests.append({'case': case, 'path': self.path, 'body': body,
                                         'attempt': attempt, 'at': time.monotonic(),
                                         'synthetic_key_correct': self.headers.get('api-key') == 'synthetic-pilot-key'})
        code = {'error429': 429, 'error500': 500, 'error502': 502, 'error503': 503,
                'error529': 529}.get(case)
        if code and attempt == 1:
            self.send_json(code, {'error': {'message': 'synthetic failure', 'type': 'server_error', 'code': str(code)}},
                           {'Retry-After': '1'} if code == 429 else {})
            return
        if case == 'stall':
            time.sleep(8)
        output = None
        if case in ('custom_tool', 'custom_history'):
            output = [{'type': 'custom_tool_call', 'id': 'ct_pilot', 'call_id': 'call_pilot',
                       'name': 'pilot_tool', 'input': 'synthetic tool input', 'status': 'completed'}]
        response = completed(output)
        if not body.get('stream'):
            self.send_json(200, response)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        try:
            self.event('response.created', response={**response, 'status': 'in_progress', 'output': []}, sequence_number=0)
            if case == 'pre_output_failure' and attempt == 1:
                self.event('response.failed', response={**response, 'status': 'failed', 'output': [],
                           'error': {'code': 'server_error', 'message': 'synthetic before output'}}, sequence_number=1)
                return
            item = response['output'][0]
            self.event('response.output_item.added', output_index=0, item=item, sequence_number=1)
            if item['type'] == 'custom_tool_call':
                self.event('response.custom_tool_call_input.delta', item_id=item['id'], output_index=0,
                           delta=item['input'], sequence_number=2)
            else:
                self.event('response.content_part.added', item_id=item['id'], output_index=0, content_index=0,
                           part={'type': 'output_text', 'text': '', 'annotations': []}, sequence_number=2)
                self.event('response.output_text.delta', item_id=item['id'], output_index=0, content_index=0,
                           delta='pilot-ok', sequence_number=3)
            if case == 'partial_failure':
                self.event('response.failed', response={**response, 'status': 'failed',
                           'error': {'code': 'server_error', 'message': 'synthetic after output'}}, sequence_number=4)
                return
            if case == 'cancel':
                for _ in range(100):
                    time.sleep(.1)
                    self.event('response.output_text.delta', item_id=item['id'], output_index=0, content_index=0,
                               delta='.', sequence_number=4)
                return
            self.event('response.output_item.done', output_index=0, item=item, sequence_number=5)
            self.event('response.completed', response=response, sequence_number=6)
        except (BrokenPipeError, ConnectionResetError):
            if case == 'cancel':
                self.server.cancelled.set()


def config(mock_endpoint):
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
            'providers': {'azure': {'keys': [{'name': 'synthetic', 'value': 'synthetic-pilot-key', 'models': ['*'],
                'weight': 1, 'aliases': {'pilot-model': 'pilot-deployment'},
                'azure_key_config': {'endpoint': mock_endpoint}}],
                'network_config': {'max_retries': 1, 'retry_backoff_initial': 100,
                                   'retry_backoff_max': 200, 'default_request_timeout_in_seconds': 3,
                                   'stream_idle_timeout_in_seconds': 5, 'allow_private_network': True}}}}


def request(case, stream=False):
    value = {'model': 'azure/pilot-model', 'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG'}]}],
             'metadata': {'harbor_pilot_case': case}, 'stream': stream}
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
    if case == 'reasoning_history':
        value['input'].append({'type': 'reasoning', 'id': 'rs_pilot', 'summary': [], 'encrypted_content': 'synthetic-encrypted'})
        value['include'] = ['reasoning.encrypted_content']
    if case == 'previous_response':
        value['previous_response_id'] = 'resp_before'
    return value


def perform(port, body, cancel=False, host="127.0.0.1"):
    start = time.monotonic()
    conn = http.client.HTTPConnection(host, port, timeout=DEADLINE)
    transport_socket = [None]
    def expire():
        if transport_socket[0] is not None:
            try:
                transport_socket[0].shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    timer = threading.Timer(DEADLINE, expire)
    timer.daemon = True
    timer.start()
    chunks = []
    first_token = None
    try:
        conn.connect()
        transport_socket[0] = conn.sock
        conn.request('POST', '/openai/v1/responses', json.dumps(body), {'Content-Type': 'application/json'})
        response = conn.getresponse()
        status = response.status
        headers = dict(response.getheaders())
        if body['stream']:
            while line := response.readline():
                chunks.append(line)
                if b'output_text.delta' in line or b'custom_tool_call_input.delta' in line:
                    if first_token is None:
                        first_token = round((time.monotonic() - start) * 1000, 2)
                    if cancel:
                        break
                if line.startswith(b'data: '):
                    try:
                        event_type = json.loads(line[6:]).get('type')
                    except (ValueError, AttributeError):
                        event_type = None
                    if event_type in ('response.completed', 'response.failed', 'response.incomplete', 'error'):
                        break
            data = b''.join(chunks)
        else:
            data = response.read()
        return {'status': status, 'headers': headers, 'data': data.decode(errors='replace'),
                'elapsed_ms': round((time.monotonic() - start) * 1000, 2), 'first_output_ms': first_token}
    except (OSError, http.client.HTTPException) as error:
        return {'status': None, 'error': type(error).__name__, 'elapsed_ms': round((time.monotonic() - start) * 1000, 2),
                'data': b''.join(chunks).decode(errors='replace'), 'first_output_ms': first_token}
    finally:
        timer.cancel()
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


def evaluate(case, body, result, seen):
    ok = result.get('status') == 200
    attempts = [r for r in seen if r['case'] == case]
    wire = attempts[-1]['body'] if attempts else {}
    checks = {'upstream_reached': bool(attempts), 'bounded_attempts': len(attempts) <= 2,
              'bounded_deadline': result['elapsed_ms'] < (DEADLINE + 1) * 1000}
    if case.startswith('error'):
        checks['recovered'] = ok
        checks['one_retry'] = len(attempts) == 2
        if case == 'error429':
            checks['retry_after_honored'] = len(attempts) == 2 and attempts[1]['at'] - attempts[0]['at'] >= .95
    elif case == 'stall':
        checks['timeout_surfaces'] = not ok
    elif case == 'partial_failure':
        checks['no_retry_after_output'] = len(attempts) == 1
        checks['failure_not_success'] = 'response.failed' in result['data'] and 'response.completed' not in result['data']
    elif case == 'pre_output_failure':
        checks['failure_reported_or_recovered'] = 'response.failed' in result['data'] or 'response.completed' in result['data']
    elif case == 'cancel':
        checks['no_replay_after_cancel'] = len(attempts) == 1
    else:
        checks['http_success'] = ok
        checks['response_success'] = 'completed' in result['data']
        checks['correct_path'] = bool(attempts) and attempts[-1]['path'].split('?')[0] == '/openai/v1/responses'
        checks['deployment_mapped'] = wire.get('model') == 'pilot-deployment'
        checks['synthetic_azure_auth'] = bool(attempts) and attempts[-1]['synthetic_key_correct']
        if case == 'image':
            checks['image_unchanged'] = wire.get('input') == body['input']
        if case in ('custom_tool', 'custom_history'):
            checks['custom_definition_preserved'] = wire.get('tools') == body['tools']
            checks['custom_output_preserved'] = 'custom_tool_call' in result['data'] and 'synthetic tool input' in result['data']
        if case.endswith('_history'):
            checks['history_preserved'] = wire.get('input') == body['input']
        if case == 'reasoning_history':
            checks['include_preserved'] = wire.get('include') == body['include']
            checks['encrypted_content_preserved'] = any(i.get('encrypted_content') == 'synthetic-encrypted' for i in wire.get('input', []))
        if case == 'previous_response':
            checks['previous_id_preserved'] = wire.get('previous_response_id') == body['previous_response_id']
    return {'case': case, 'passed': all(checks.values()), 'checks': checks, 'upstream_attempts': len(attempts),
            'http_status': result.get('status'), 'elapsed_ms': result['elapsed_ms'],
            'first_output_ms': result.get('first_output_ms'),
            'response_event_types': sorted(set(line[7:] for line in result['data'].splitlines() if line.startswith('event: '))),
            'error_type': result.get('error'),
            'input_changed_paths': changed_paths(body.get('input'), wire.get('input')),
            'retry_gap_ms': round((attempts[1]['at'] - attempts[0]['at']) * 1000, 2) if len(attempts) > 1 else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+')
    args = parser.parse_args()
    pin = json.loads((HERE / 'pin.json').read_text())
    arch = {'aarch64': 'arm64', 'arm64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine())
    if arch not in pin['images']:
        raise SystemExit('No pinned image for this architecture')
    image = pin['images'][arch]
    name = 'harbor-bifrost-pilot-' + uuid.uuid4().hex[:10]
    mock_name, network = name + '-azure', name + '-network'
    result = {'schema_version': 1, 'scope': 'synthetic Azure contract only', 'pin': pin,
              'image': image, 'live_azure_tested': False, 'retry_owner': 'bifrost', 'max_retries': 1,
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
        (root/'config.json').write_text(json.dumps(config('http://' + mock_name + ':8081')))
        try:
            docker('network', 'create', '--internal', network)
            network_created = True
            docker('create', '--name', mock_name, '--network', network, '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--pids-limit', '64', '--memory', '128m',
                   '-e', 'HOME=/tmp', pin['mock_image'],
                   'python3', '/tmp/mock_server.py')
            created.append(mock_name)
            docker('cp', str(HERE) + '/.', mock_name + ':/tmp/')
            docker('start', mock_name)
            def probe(*arguments):
                return json.loads(docker('exec', mock_name, 'python3', '/tmp/probe.py', *arguments, timeout=180))
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
            cases = args.cases or ['basic', 'stream', 'image', 'custom_tool', 'custom_history',
                                  'function_history', 'reasoning_history', 'previous_response',
                                  'error429', 'error500', 'error502', 'error503', 'error529',
                                  'pre_output_failure', 'partial_failure', 'cancel', 'stall']
            result.update(probe('--suite', name, json.dumps(cases)))
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
                                                   and not result['synthetic_content_in_runtime_files'])
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
