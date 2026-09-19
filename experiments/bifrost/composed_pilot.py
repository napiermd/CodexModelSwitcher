#!/usr/bin/env python3
"""Isolated real Harbor entrypoint → pinned Bifrost → synthetic Azure study."""
import concurrent.futures
import copy
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import pilot
from pressure_pilot import MockControl, PressureAzure, PressureHandler, correlation_response

NAME = 'harbor-composed'
SCRIPT = 'composed_pilot.py'
SCOPE = 'synthetic composed Harbor/Bifrost contract; no live Azure or installed runtime changes'
GATE_FIELD = 'composed_synthetic_gate_passed'
CASES = ('stream_lifetime', 'queue_capacity', 'queue_timeout', 'queued_cancellation', 'dispatched_cancellation',
         'error429', 'error503', 'partial_disconnect', 'opaque_json', 'opaque_sse', 'invalid_encrypted')
MOCK_PIDS = 192
MOCK_MEMORY = '256m'
STEP = 8
REQUEST_SECONDS = 55
HOLD_SECONDS = 50
CONTENT = 'SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG'
OWNER_TOKEN = 'synthetic-composed-owner-token'
PROVIDER_KEY = 'synthetic-pilot-key'
AZURE_URL = 'https://fixture.openai.azure.com/openai/v1/responses'
POLICY = {'active_limit': 2, 'max_waiting': 16, 'wait_seconds': 30.0}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def configure(config):
    config = copy.deepcopy(config)
    config['client']['drop_excess_requests'] = True
    provider = config['providers']['azure']
    provider['concurrency_and_buffer_size'] = {'concurrency': 32, 'buffer_size': 32}
    provider['network_config'].update(max_retries=0, default_request_timeout_in_seconds=60,
                                      stream_idle_timeout_in_seconds=60)
    return config


def prepare_fixture(root, source=None):
    source = Path(source or Path(__file__).resolve().parents[2] / 'ModelHarbor/Support')
    service = load_module('composed_service', source / 'gateway_service.py')
    fixture = Path(root) / 'composed-source'
    fixture.mkdir()
    state = fixture / 'state'
    state.mkdir(mode=0o700)
    retained, digest = service.retain_runtime(source, state)
    entries = service.inventory(retained)
    exported = fixture / 'Support'
    shutil.copytree(retained, exported)
    # The synthetic export crosses container UIDs with capabilities dropped.
    # Directory traversal is public; the exact Python file inventory stays intact.
    for directory in (fixture, exported, *(path for path in exported.rglob('*') if path.is_dir())):
        directory.chmod(0o755)
    if service.inventory(fixture / 'Support') != entries or service.inventory(source) != entries:
        raise ValueError('Source changed while preparing the composed fixture')
    shutil.rmtree(state)
    (fixture / 'source.json').write_text(json.dumps({'runtime_id': digest, 'files': entries}, sort_keys=True))
    return fixture


def redirect_source(target):
    # Only the fixture URL is rewritten. Any other urllib destination fails closed.
    return '''import urllib.request
original = urllib.request.OpenerDirector.open
def redirect(self, req, *args, **kwargs):
    if not isinstance(req, urllib.request.Request) or req.full_url != AZURE_URL:
        raise RuntimeError('Unexpected composed-fixture destination')
    req = urllib.request.Request(TARGET, data=req.data, headers=dict(req.headers), method=req.get_method())
    return original(self, req, *args, **kwargs)
urllib.request.OpenerDirector.open = redirect
'''.replace('AZURE_URL', repr(AZURE_URL)).replace('TARGET', repr(target))


def wait_until(read, predicate, description, seconds=STEP):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = read()
        if predicate(value):
            return value
        threading.Event().wait(.025)
    raise TimeoutError(description)


class Gateway:
    def __init__(self, fixture, target):
        self.fixture = Path(fixture)
        self.service = load_module('composed_service', self.fixture / 'Support/gateway_service.py')
        self.control = load_module('composed_control', self.fixture / 'Support/gateway_control.py')
        self.manifest = json.loads((self.fixture / 'source.json').read_text())
        entries = self.service.inventory(self.fixture / 'Support')
        if self.manifest != {'runtime_id': self.service.runtime_digest(entries), 'files': entries}:
            raise ValueError('Composed source inventory mismatch')
        self.tmp = tempfile.TemporaryDirectory(prefix='harbor-composed-owned-')
        self.root = Path(self.tmp.name)
        self.process = None
        self.log = None
        self.cleanup = {'process_exited': False, 'sentinel_unchanged': False,
                        'source_unchanged': False, 'logs_content_free': False}
        try:
            self.config = self.root / 'config'
            self.config.mkdir(mode=0o700)
            state = self.root / 'state'
            state.mkdir(mode=0o700)
            (self.config / 'token').write_text(OWNER_TOKEN)
            (self.config / 'token').chmod(0o600)
            (self.config / 'model-switcher.json').write_text(json.dumps({'services': [
                {'id': 'azure', 'baseURL': 'https://fixture.openai.azure.com/openai/v1',
                 'models': [{'id': 'pilot-deployment'}]}]}))
            (self.config / 'model-catalogs').mkdir()
            (self.config / 'model-catalogs/azure.json').write_text(json.dumps({'models': [
                {'slug': 'pilot-deployment', 'default_reasoning_level': 'none'}]}))
            self.sentinel = self.config / 'history.jsonl'
            self.sentinel.write_bytes(b'{"synthetic":"untouched conversation history"}\n')
            self.sentinel_digest = hashlib.sha256(self.sentinel.read_bytes()).hexdigest()
            retained, self.digest = self.service.retain_runtime(self.fixture / 'Support', state)
            (self.root / 'sitecustomize.py').write_text(redirect_source(target))
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                self.port = reservation.getsockname()[1]
            env = {'PATH': os.defpath, 'HOME': str(self.root), 'CODEX_HOME': str(self.config),
                   'PYTHONPATH': str(self.root), 'PYTHONDONTWRITEBYTECODE': '1',
                   'MODEL_HARBOR_PORT': str(self.port), 'MODEL_HARBOR_CONFIG_DIR': str(self.config),
                   'MODEL_HARBOR_TOKEN_PATH': str(self.config / 'token'), 'MODEL_HARBOR_STATE_DIR': str(state),
                   'MODEL_HARBOR_INDEPENDENT': '1', 'MODEL_HARBOR_RUNTIME_DIGEST': self.digest}
            self.command = [sys.executable, '-B', '-u', str(retained / 'grok_adapter.py')]
            self.log = (self.root / 'gateway.log').open('wb')
            self.process = subprocess.Popen(self.command, env=env, stdin=subprocess.DEVNULL,
                                            stdout=self.log, stderr=self.log)
            wait_until(self.startup_status, lambda value: value is not None, 'Owned gateway failed to start')
            self.initial_status = self.status()
            runtime = self.initial_status['runtime']
            self.verified = (runtime['mode'] == 'independent' and runtime['runtime_id'] == self.digest
                             and runtime['protocol_version'] == 1 and bool(runtime['boot_id']))
            if not self.verified:
                raise ValueError('Owned gateway identity mismatch')
            raw, _ = self.control.owner_request('POST', '/harbor/providers/azure', json.dumps({
                'endpoint': 'https://fixture.openai.azure.com', 'key': PROVIDER_KEY}).encode(),
                self.port, self.config / 'token')
            if json.loads(raw).get('configured') is not True:
                raise ValueError('Synthetic provider configuration failed')
        except BaseException:
            self.close()
            raise

    def startup_status(self):
        if self.process.poll() is not None:
            raise RuntimeError('Owned gateway exited during startup')
        try:
            return self.status()
        except (OSError, self.control.ControlError):
            return None

    def status(self):
        raw, _ = self.control.owner_request('GET', '/harbor/status', b'', self.port, self.config / 'token')
        return json.loads(raw)

    def traffic(self, active, waiting):
        return wait_until(self.status, lambda s: s['azure_traffic'] == {
            'active': active, 'waiting': waiting, 'keys': int(bool(active or waiting))},
            'Harbor admission state did not converge')

    def perform(self, body, **options):
        return pilot.perform(self.port, body, path='/harbor/v1/responses',
                             headers={'Authorization': 'Bearer ' + OWNER_TOKEN},
                             deadline=REQUEST_SECONDS, **options)

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.cleanup['process_exited'] = self.process.poll() is not None
        if self.log:
            self.log.close()
        if hasattr(self, 'sentinel'):
            self.cleanup['sentinel_unchanged'] = (hashlib.sha256(self.sentinel.read_bytes()).hexdigest()
                                                   == self.sentinel_digest)
        self.cleanup['source_unchanged'] = (self.service.inventory(self.fixture / 'Support') == self.manifest['files'])
        log_path = self.root / 'gateway.log'
        self.cleanup['logs_content_free'] = (not log_path.exists() or not any(
            secret in log_path.read_text(errors='replace') for secret in (CONTENT, OWNER_TOKEN, PROVIDER_KEY)))
        self.tmp.cleanup()


def opaque_output(alias):
    nested = {'type': 'function_call', 'name': alias, 'arguments': '{"input":"opaque-value"}'}
    return [
        {'type': 'reasoning', 'id': 'rs_composed', 'summary': [], 'encrypted_content': 'synthetic-ciphertext',
         'extension': {'output': [nested], 'item': nested}},
        {'type': 'function_call', 'id': 'fc_composed', 'call_id': 'call_composed',
         'name': alias, 'arguments': '{"input":"synthetic-tool-input"}'},
        {'type': 'compaction', 'id': 'cmp_composed', 'encrypted_content': 'synthetic-compaction',
         'extension': nested},
        {**pilot.completed()['output'][0], 'author': {'role': 'assistant', 'name': 'fixture-author'},
         'recipient': 'fixture-recipient'}]


class ComposedAzure(PressureAzure):
    def __init__(self, address=('127.0.0.1', 0)):
        super().__init__(address)
        self.RequestHandlerClass = ComposedHandler

    def await_release(self, request_id):
        with self.condition:
            return self.condition.wait_for(lambda: request_id in self.released, HOLD_SECONDS)


class ComposedHandler(PressureHandler):
    def respond(self, body, case, attempt, record):
        if not record['synthetic_key_correct']:
            return self.send_json(401, {'error': {'code': 'synthetic_auth_failed'}})
        if case in ('error429', 'error503', 'invalid_encrypted'):
            code = 400 if case == 'invalid_encrypted' else int(case[5:])
            error = error_body(case)
            return self.send_json(code, error, {'Retry-After': '7', 'X-Request-Id': record['request_id']})
        request_id = record['request_id']
        response = correlation_response(request_id)
        if case in ('opaque_json', 'opaque_sse'):
            response['output'] = opaque_output(body['tools'][0]['name'])
        if not body.get('stream'):
            return self.send_json(200, response)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.event('response.created', response={**response, 'status': 'in_progress', 'output': []})
        self.event('response.output_text.delta', delta=CONTENT + ':' + request_id,
                   item_id='msg_pilot', output_index=0, content_index=0)
        with self.server.lock:
            record['stream_started_at'] = time.monotonic()
        if case == 'partial_disconnect':
            return
        if body.get('metadata', {}).get('composed_hold'):
            if not self.server.await_release(request_id):
                self.event('response.failed', response={'status': 'failed', 'error': {'code': 'fixture_hold_timeout'}})
                return
        if case == 'dispatched_cancellation':
            end = time.monotonic() + HOLD_SECONDS
            try:
                while time.monotonic() < end:
                    self.event('response.output_text.delta', delta='synthetic-cancel-tick',
                               item_id='msg_pilot', output_index=0, content_index=0)
                    threading.Event().wait(.05)
            except (BrokenPipeError, ConnectionResetError):
                with self.server.lock:
                    record['disconnect_observed'] = True
                return
            return
        for index, item in enumerate(response['output']):
            self.event('response.output_item.added', output_index=index, item=item)
            self.event('response.output_item.done', output_index=index, item=item)
        self.event('response.completed', response=response)


def error_body(case):
    return {'error': {'code': 'invalid_encrypted_content' if case == 'invalid_encrypted' else 'synthetic_' + case,
                      'message': 'Synthetic upstream rejection'}}


def request(case, *, stream=False, hold=False):
    body = pilot.request(case, stream, 'azure-passthrough')
    body['model'] = 'harbor/azure/pilot-deployment'
    request_id = body['metadata']['harbor_pilot_request']
    body['client_metadata'] = {'thread_id': 'composed-' + request_id, 'turn_id': 'synthetic-turn'}
    if hold:
        body['metadata']['composed_hold'] = True
    return body


class Run:
    def __init__(self, pool, gateway, body):
        self.body, self.id = body, body['metadata']['harbor_pilot_request']
        self.output = threading.Event()
        self.socket = None
        self.future = pool.submit(gateway.perform, body, on_connect=self.connected, on_event=self.observe)

    def connected(self, sock):
        self.socket = sock

    def observe(self, event):
        if event.get('type') == 'response.output_text.delta' and event.get('delta'):
            self.output.set()

    def await_output(self):
        if not self.output.wait(STEP):
            raise TimeoutError('Expected a complete stream delta')

    def cancel(self):
        if self.socket is not None:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def result(self):
        return self.future.result(timeout=REQUEST_SECONDS + 2)


def terminal(result):
    return [event for event in result.get('events', []) if event.get('type') == 'response.completed']


def correlated(result, request_id):
    events = terminal(result)
    frames = result.get('events', [])
    deltas = [event.get('delta') for event in frames if event.get('type') == 'response.output_text.delta']
    return (result.get('status') == 200 and len(events) == 1 and frames[-1] is events[0]
            and events[0].get('response') == correlation_response(request_id)
            and deltas == [CONTENT + ':' + request_id])


def evidence(case, checks, bodies, results, records, expected_counts):
    ids = [body['metadata']['harbor_pilot_request'] for body in bodies]
    counts = {key: sum(row['request_id'] == key for row in records) for key in ids}
    checks.update(exact_attempt_counts=counts == expected_counts,
                  no_unexpected_upstream_requests=all(row['request_id'] in ids for row in records),
                  native_path=all(row['path'] == '/openai/v1/responses' for row in records),
                  synthetic_auth=all(row['synthetic_key_correct'] for row in records),
                  exact_deployment=all(row['body']['model'] == 'pilot-deployment' for row in records))
    return {'case': case, 'passed': bool(checks) and all(value is True for value in checks.values()),
            'checks': checks, 'request_ids': ids, 'upstream_attempts': counts,
            'total_upstream_attempts': len(records), 'http_statuses': [r.get('status') for r in results]}


def admission_case(case, gateway, control, baseline):
    bodies, runs, results, checks = [], [], [], {}
    measurements = {}
    before_uncertain = gateway.status()['runtime']['uncertain_turns']
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        try:
            for _ in range(2):
                body = request(case, stream=True, hold=True)
                bodies.append(body)
                run = Run(pool, gateway, body)
                runs.append(run)
                run.await_output()
            gateway.traffic(2, 0)
            followers = 16 if case == 'queue_capacity' else 1
            for _ in range(followers):
                body = request(case, stream=True)
                bodies.append(body)
                runs.append(Run(pool, gateway, body))
            status = gateway.traffic(2, followers)
            queued_observed_at = time.monotonic()
            initial = control.snapshot()['requests'][baseline:]
            checks['two_established_streams_and_waiters'] = (len(initial) == 2 and all(
                'stream_started_at' in row for row in initial) and status['azure_traffic']['waiting'] == followers)
            checks['queued_has_no_upstream_attempt'] = {r['request_id'] for r in initial} == {r.id for r in runs[:2]}
            rejected = None
            if case == 'queue_capacity':
                body = request(case, stream=True)
                bodies.append(body)
                overflow = gateway.perform(body)
                results.append(overflow)
                rejected = body['metadata']['harbor_pilot_request']
                checks['overflow_rejected_before_dispatch'] = (overflow.get('status') == 503
                    and {k.lower(): v for k, v in overflow.get('headers', {}).items()}.get('retry-after') == '1')
                gateway.traffic(2, 16)
            if case == 'queue_timeout':
                rejected = runs[2].id
                expired = runs[2].result()
                measurements = {
                    'queued_request_elapsed_ms': expired.get('elapsed_ms'),
                    'queue_observed_wait_ms': round((time.monotonic() - queued_observed_at) * 1000, 2),
                }
                results.append(expired)
                try:
                    local_error = json.loads(expired.get('data', '')) == {'error': 'Azure admission queue timed out'}
                except (TypeError, ValueError):
                    local_error = False
                checks['local_queue_timeout_response'] = (expired.get('status') == 503 and local_error
                    and not expired.get('events')
                    and {k.lower(): v for k, v in expired.get('headers', {}).items()}.get('retry-after') == '1')
                checks['production_queue_deadline'] = queue_timeout_timing(measurements)
                after_timeout = gateway.traffic(2, 0)
                held = control.snapshot()['requests'][baseline:]
                checks['held_streams_open_after_timeout'] = (all(not run.future.done() for run in runs[:2])
                    and {row['request_id'] for row in held} == {run.id for run in runs[:2]}
                    and all('finished_at' not in row for row in held))
                checks['timed_out_waiter_removed'] = after_timeout['azure_traffic']['waiting'] == 0
                settled = wait_until(gateway.status, lambda value: value['runtime']['active_requests'] == 2,
                                     'Expired waiter retained an active request lease')
                checks['timed_out_request_has_no_active_lease'] = settled['runtime']['active_requests'] == 2
                checks['timeout_does_not_make_turn_uncertain'] = settled['runtime']['uncertain_turns'] == before_uncertain
            if case == 'queued_cancellation':
                rejected = runs[2].id
                runs[2].cancel()
                results.append(runs[2].result())
                gateway.traffic(2, 0)
                checks['cancelled_waiter_removed_while_streams_open'] = len(control.snapshot()['requests'][baseline:]) == 2
            control.release(runs[0].id)
            if case == 'stream_lifetime':
                runs[2].await_output()
                checks['third_started_after_permit_release'] = correlated(runs[0].result(), runs[0].id)
                checks['second_stream_still_open'] = not runs[1].future.done()
            control.release(runs[1].id)
            for run in runs:
                if run.id != rejected:
                    result = run.result()
                    results.append(result)
                    checks['response_affinity_' + run.id] = correlated(result, run.id)
            gateway.traffic(0, 0)
            expected = {b['metadata']['harbor_pilot_request']: int(b['metadata']['harbor_pilot_request'] != rejected) for b in bodies}
            if case == 'queue_timeout':
                checks['held_streams_completed'] = all(checks['response_affinity_' + run.id] for run in runs[:2])
            row = evidence(case, checks, bodies, results, control.snapshot()['requests'][baseline:], expected)
            row.update(measurements)
            return row
        finally:
            for run in runs:
                try:
                    control.release(run.id)
                except Exception:
                    pass
                finally:
                    run.cancel()


def single_case(case, gateway, control, baseline):
    stream = case in ('partial_disconnect', 'dispatched_cancellation')
    body = request(case, stream=stream)
    key = body['metadata']['harbor_pilot_request']
    checks, results = {}, []
    before_uncertain = gateway.status()['runtime']['uncertain_turns']
    if case == 'invalid_encrypted':
        body['input'].append({'type': 'reasoning', 'id': 'rs_invalid', 'summary': [],
                              'encrypted_content': 'synthetic-invalid-ciphertext'})
    if case == 'dispatched_cancellation':
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            run = Run(pool, gateway, body)
            try:
                run.await_output()
                run.cancel()
                result = run.result()
                wait_until(control.snapshot, lambda s: any(r['request_id'] == key and r.get('disconnect_observed')
                           for r in s['requests']), 'Upstream disconnect was not observed')
                checks['upstream_disconnect_observed'] = True
            finally:
                run.cancel()
    else:
        result = gateway.perform(body)
    results.append(result)
    gateway.traffic(0, 0)
    if stream:
        status = wait_until(gateway.status, lambda s: s['runtime']['active_requests'] == 0,
                            'Request lease not released')
        checks['partial_output_without_completion'] = (sum(e.get('type') == 'response.output_text.delta'
            and e.get('delta') == CONTENT + ':' + key for e in result.get('events', [])) == 1 and not terminal(result))
        checks['uncertain_turn_retained'] = status['runtime']['uncertain_turns'] == before_uncertain + 1
        retry = gateway.perform(body)
        results.append(retry)
        checks['same_turn_replay_rejected'] = retry.get('status') == 400 and 'uncertain' in retry.get('data', '')
    else:
        code = 400 if case == 'invalid_encrypted' else int(case[5:])
        try:
            unchanged = json.loads(result.get('data', '')) == error_body(case)
        except ValueError:
            unchanged = False
        checks['upstream_error_unchanged'] = result.get('status') == code and unchanged
        checks['retry_after_forwarded'] = {k.lower(): v for k, v in result.get('headers', {}).items()}.get('retry-after') == '7'
    records = control.snapshot()['requests'][baseline:]
    if case == 'invalid_encrypted':
        portable = [item for item in body['input'] if item.get('type') not in ('reasoning', 'compaction')
                    and 'encrypted_content' not in item]
        checks['unknown_history_removed'] = len(records) == 1 and records[0]['body']['input'] == portable
    return evidence(case, checks, [body], results, records, {key: 1})


def opaque_case(case, gateway, control, baseline):
    body = request(case, stream=case == 'opaque_sse')
    body['tools'] = [{'type': 'custom', 'name': 'pilot_tool', 'format': {'type': 'text'}}]
    first = gateway.perform(body)
    records = control.snapshot()['requests'][baseline:]
    if len(records) != 1:
        raise ValueError('Opaque first request attempt mismatch')
    alias = records[0]['body']['tools'][0]['name']
    native = opaque_output(alias)
    expected = copy.deepcopy(native)
    expected[1] = {'type': 'custom_tool_call', 'id': 'fc_composed', 'call_id': 'call_composed',
                   'name': 'pilot_tool', 'input': 'synthetic-tool-input'}
    if body['stream']:
        completed = terminal(first)
        output = completed[0]['response']['output'] if len(completed) == 1 else None
        frames = first.get('events', [])
        added = [e.get('item') for e in frames if e.get('type') == 'response.output_item.added']
        done = [e.get('item') for e in frames if e.get('type') == 'response.output_item.done']
        frame_check = added == expected and done == expected and bool(completed) and frames[-1] is completed[0]
    else:
        output = json.loads(first['data']).get('output')
        frame_check = True
    expected_response = correlation_response(body['metadata']['harbor_pilot_request'])
    expected_response['output'] = expected
    first_response = completed[0]['response'] if body['stream'] and len(completed) == 1 else (
        None if body['stream'] else json.loads(first['data']))
    checks = {'ordered_output_preserved': first.get('status') == 200 and first_response == expected_response,
              'sse_item_frames_preserved': frame_check}
    if output != expected:
        return evidence(case, checks, [body], [first], records, {body['metadata']['harbor_pilot_request']: 1})
    followup = request(case, stream=body['stream'])
    followup['client_metadata'] = copy.deepcopy(body['client_metadata'])
    followup['tools'] = copy.deepcopy(body['tools'])
    followup['input'] = copy.deepcopy(body['input']) + output + [
        {'type': 'custom_tool_call_output', 'call_id': 'call_composed', 'output': 'synthetic-tool-result'}]
    second = gateway.perform(followup)
    records = control.snapshot()['requests'][baseline:]
    portable = copy.deepcopy(native)
    portable[1]['arguments'] = json.dumps({'input': 'synthetic-tool-input'})
    expected_input = copy.deepcopy(body['input']) + portable + [
        {'type': 'function_call_output', 'call_id': 'call_composed', 'output': 'synthetic-tool-result'}]
    for item in expected_input:
        if item.get('type') in ('message', 'function_call', 'function_call_output'):
            item.pop('id', None)
    checks['ordered_history_and_tool_links_preserved'] = (len(records) == 2 and records[1]['body']['input'] == expected_input)
    checks['reasoning_include_preserved'] = all('reasoning.encrypted_content' in r['body'].get('include', []) for r in records)
    second_expected = correlation_response(followup['metadata']['harbor_pilot_request'])
    second_expected['output'] = expected
    second_terminals = terminal(second)
    second_response = (second_terminals[0]['response'] if len(second_terminals) == 1 else None) if body['stream'] else json.loads(second.get('data', '{}'))
    checks['same_turn_continuation_completed'] = second.get('status') == 200 and second_response == second_expected
    return evidence(case, checks, [body, followup], [first, second], records,
                    {b['metadata']['harbor_pilot_request']: 1 for b in (body, followup)})


def queue_timeout_timing(row):
    elapsed, observed = row.get('queued_request_elapsed_ms'), row.get('queue_observed_wait_ms')
    return (type(elapsed) in (int, float) and math.isfinite(elapsed)
            and type(observed) in (int, float) and math.isfinite(observed)
            and POLICY['wait_seconds'] * 1000 <= elapsed < (POLICY['wait_seconds'] + STEP) * 1000
            and (POLICY['wait_seconds'] - 1) * 1000 <= observed <= elapsed)


def queue_timeout_verified(rows):
    matches = [row for row in rows if row.get('case') == 'queue_timeout']
    if len(matches) != 1:
        return False
    row = matches[0]
    ids = row.get('request_ids', [])
    required = ('two_established_streams_and_waiters', 'queued_has_no_upstream_attempt',
                'local_queue_timeout_response', 'production_queue_deadline',
                'held_streams_open_after_timeout', 'timed_out_waiter_removed',
                'timed_out_request_has_no_active_lease', 'timeout_does_not_make_turn_uncertain',
                'held_streams_completed', 'exact_attempt_counts', 'no_unexpected_upstream_requests')
    return (row.get('passed') is True and len(ids) == 3 and len(set(ids)) == 3
            and row.get('upstream_attempts') == {ids[0]: 1, ids[1]: 1, ids[2]: 0}
            and row.get('total_upstream_attempts') == 2
            and row.get('http_statuses') == [503, 200, 200]
            and queue_timeout_timing(row)
            and all(row.get('checks', {}).get(check) is True for check in required))


def suite(host, cases, control=None, port=8080, fixture=None):
    control = control or MockControl()
    fixture = Path(fixture or '/tmp/composed-source')
    if not cases or len(set(cases)) != len(cases) or any(case not in CASES for case in cases):
        raise ValueError('Invalid composed case selection')
    gateway, rows = None, []
    report = {'study': NAME, 'entrypoint_verified': False, 'installed_runtime_changed': False,
              'live_azure_tested': False, 'composed_synthetic_gate_passed': False,
              'client_total_deadline_seconds': REQUEST_SECONDS,
              'redirect_scope': AZURE_URL, 'harbor_policy': POLICY,
              'queue_timeout_tested': False,
              'bifrost_provider_limits': {'concurrency': 32, 'buffer_size': 32},
              'cancellation_claim': 'observed socket/queue cleanup only; no billing or provider rollback claim'}
    try:
        gateway = Gateway(fixture, f'http://{host}:{port}/azure_passthrough/openai/v1/responses')
        report.update(entrypoint_verified=gateway.verified, harbor_runtime_digest=gateway.digest,
                      harbor_source_inventory=gateway.manifest['files'])
        initial = len(control.snapshot()['requests'])
        report['fixture_started_without_upstream_requests'] = initial == 0
        for case in cases:
            baseline = len(control.snapshot()['requests'])
            try:
                if case in ('stream_lifetime', 'queue_capacity', 'queue_timeout', 'queued_cancellation'):
                    row = admission_case(case, gateway, control, baseline)
                elif case in ('opaque_json', 'opaque_sse'):
                    row = opaque_case(case, gateway, control, baseline)
                else:
                    row = single_case(case, gateway, control, baseline)
            except Exception as error:
                row = {'case': case, 'passed': False, 'checks': {'experiment_completed': False},
                       'harness_error_type': type(error).__name__}
            rows.append(row)
        total = len(control.snapshot()['requests']) - initial
        report['all_upstream_requests_accounted_for'] = total == sum(row.get('total_upstream_attempts', 0) for row in rows)
        report['total_upstream_attempts'] = total
    finally:
        if gateway is not None:
            gateway.close()
            report['gateway_cleanup'] = gateway.cleanup
    report['cases'] = rows
    report['queue_timeout_tested'] = queue_timeout_verified(rows)
    report['study_complete'] = list(cases) == list(CASES)
    report[GATE_FIELD] = (report['study_complete'] and report['entrypoint_verified'] and report['queue_timeout_tested']
        and len(rows) == len(cases)
        and all(row['passed'] for row in rows) and report.get('all_upstream_requests_accounted_for') is True
        and report.get('fixture_started_without_upstream_requests') is True
        and all(report.get('gateway_cleanup', {}).values()) and bool(report.get('gateway_cleanup')))
    return report


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == '--mock':
        with ComposedAzure(('0.0.0.0', 8081)) as server:
            server.serve_forever()
    elif mode == '--health':
        try:
            with urllib.request.urlopen('http://' + sys.argv[2] + ':8080/health', timeout=2) as response:
                ready = response.status == 200
        except OSError:
            ready = False
        print(json.dumps({'ready': ready}))
    elif mode == '--snapshot':
        print(json.dumps(MockControl().snapshot()))
    elif mode == '--suite':
        if sys.argv[3] != 'azure-passthrough':
            raise SystemExit('Composed study requires native Azure passthrough')
        print(json.dumps(suite(sys.argv[2], json.loads(sys.argv[4]))))
    else:
        pilot.main(study=sys.modules[__name__])


if __name__ == '__main__':
    main()
