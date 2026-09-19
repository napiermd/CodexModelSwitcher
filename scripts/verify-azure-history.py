#!/usr/bin/env python3
"""Opt-in, five-request Azure continuation check.

Isolated mode reads the Azure API key from stdin. Installed mode uses the
authenticated local gateway and needs no provider credential. Outputs aggregate
evidence only: no key, conversation, ciphertext, or provider error text. Uses a
synthetic read-only tool; no tool process is executed.
"""
import argparse
import copy
import http.client
import http.server
import importlib.util
import json
import re
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time


class VerificationError(Exception):
    pass


def load_gateway_control(support):
    control_spec = importlib.util.spec_from_file_location(
        'history_probe_control', support.with_name('gateway_control.py'))
    control = importlib.util.module_from_spec(control_spec)
    control_spec.loader.exec_module(control)
    return control


def installed_runtime_matches(control, port, token_path, expected_runtime_id):
    _, runtime = control.owner_request('GET', '/harbor/status', b'', port, token_path)
    runtime_id = runtime.get('runtime_id') if isinstance(runtime, dict) else None
    if not isinstance(runtime_id, str) or not re.fullmatch('[0-9a-f]{64}', runtime_id):
        raise VerificationError('Installed gateway did not provide a valid runtime identity.')
    if runtime_id != expected_runtime_id:
        raise VerificationError('Installed gateway runtime does not match the expected activation.')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--endpoint')
    parser.add_argument('--deployment', required=True)
    parser.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh'], required=True)
    parser.add_argument('--expected-runtime-id')
    args = parser.parse_args()
    if not args.installed and not args.endpoint:
        parser.error('--endpoint is required unless --installed is used')
    if args.expected_runtime_id and not args.installed:
        parser.error('--expected-runtime-id requires --installed')
    if args.expected_runtime_id and not re.fullmatch('[0-9a-f]{64}', args.expected_runtime_id):
        parser.error('--expected-runtime-id must be a 64-character lowercase hexadecimal runtime ID')
    key = None
    if not args.installed:
        key = sys.stdin.buffer.read(4097).decode().strip()
        if not key or len(key) > 4096 or any(c.isspace() for c in key):
            raise VerificationError('A single Azure API key is required on stdin.')
    support = Path(__file__).resolve().parents[1] / 'ModelHarbor/Support/grok_adapter.py'
    spec = importlib.util.spec_from_file_location('history_probe_gateway', support)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    endpoint = bridge.azure_endpoint(args.endpoint) if args.endpoint else None
    evidence = {'isolated': not args.installed, 'installed': args.installed,
                'installed_runtime_matches_expected': None,
                'deployment': args.deployment, 'effort': args.effort, 'cases': []}
    upstream_requests = []
    real_build_opener = bridge.urllib.request.build_opener

    class ObservedOpener:
        def __init__(self, *handlers):
            self.delegate = real_build_opener(*handlers)

        def open(self, request, **kwargs):
            payload = json.loads(request.data)
            upstream_requests.append(copy.deepcopy(payload['input']))
            return self.delegate.open(request, **kwargs)

    with tempfile.TemporaryDirectory(prefix='harbor-azure-history-') as directory:
        root = Path(directory)
        server = thread = None
        if args.installed:
            token_path = Path.home() / '.codex/model-harbor-bridge-token'
            token = token_path.read_text().strip()
            port = 48118
            if args.expected_runtime_id:
                evidence['installed_runtime_matches_expected'] = installed_runtime_matches(
                    load_gateway_control(support), port, token_path, args.expected_runtime_id)
        else:
            bridge.CONFIG_DIR = root
            bridge.TOKEN_PATH = root / 'token'
            token = secrets.token_hex(32)
            bridge.TOKEN_PATH.write_text(token)
            bridge.TOKEN_PATH.chmod(0o600)
            bridge.AZURE_CONNECTION = {'key': key, 'endpoint': endpoint}
            (root / 'model-switcher.json').write_text(json.dumps({'services': [
                {'id': 'azure', 'baseURL': endpoint, 'models': [{'id': args.deployment}]}]}))
            (root / 'model-catalogs').mkdir()
            (root / 'model-catalogs/azure.json').write_text(json.dumps({'models': [
                {'slug': args.deployment, 'default_reasoning_level': args.effort}]}))
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port
            bridge.urllib.request.build_opener = ObservedOpener

        def request(payload):
            started = time.monotonic()
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=185)
            try:
                connection.request('POST', '/harbor/v1/responses', json.dumps(payload), {
                    'Authorization': 'Bearer ' + token,
                    'X-Model-Harbor-Token': token})
                response = connection.getresponse()
                status = response.status
                wire = response.read(8 * 1024 * 1024)
                if 'text/event-stream' in response.getheader('Content-Type', ''):
                    events = [json.loads(line[5:]) for line in wire.splitlines()
                              if line.startswith(b'data:') and line[5:].strip() != b'[DONE]']
                    terminals = [e for e in events if e.get('type') in (
                        'response.completed', 'response.failed', 'response.incomplete')]
                    if len(terminals) != 1:
                        raise VerificationError('Expected exactly one terminal SSE event.')
                    value = terminals[0]['response']
                else:
                    value = json.loads(wire)
                return status, value, round(time.monotonic() - started, 3)
            finally:
                connection.close()

        try:
            marker = 'HARBOR_SYNTHETIC_MARKER_42'
            tool = {'type': 'function', 'name': 'read_fixture',
                    'description': 'Return a synthetic verification marker.',
                    'parameters': {'type': 'object', 'properties': {},
                                   'required': [], 'additionalProperties': False}, 'strict': True}
            base = {'model': 'harbor/azure/' + args.deployment, 'max_output_tokens': 2048,
                    'tools': [tool], 'input': [{'role': 'user', 'content':
                    'Work out whether 137 multiplied by 19 is greater than 2500. '
                    'Then call read_fixture once. After its result, reply with only the exact marker it returns.'}]}
            last_followup = None
            for stream in [False, True]:
                metadata = {'thread_id': 'synthetic-' + secrets.token_hex(16),
                            'turn_id': secrets.token_hex(16)}
                first = dict(base, stream=stream, client_metadata=metadata,
                             tool_choice={'type': 'function', 'name': 'read_fixture'})
                status, response, first_seconds = request(first)
                if status != 200 or response.get('status') != 'completed':
                    raise VerificationError(f'Initial response did not complete (HTTP {status}).')
                output = response.get('output', [])
                reasoning = [item for item in output if item.get('type') == 'reasoning' and item.get('encrypted_content')]
                calls = [item for item in output if item.get('type') == 'function_call']
                if not reasoning or len(calls) != 1 or calls[0].get('name') != 'read_fixture':
                    raise VerificationError('Initial response lacked encrypted reasoning or the expected tool call.')
                followup = dict(base, stream=stream, client_metadata=metadata,
                    tool_choice='none', input=base['input'] + output + [
                    {'type': 'function_call_output', 'call_id': calls[0]['call_id'], 'output': marker}])
                status, response, continuation_seconds = request(followup)
                if status != 200 or response.get('status') != 'completed':
                    raise VerificationError(f'Continuation did not complete (HTTP {status}).')
                text = ''.join(part.get('text', '') for item in response.get('output', [])
                               if item.get('type') == 'message' for part in item.get('content', []))
                if text.strip() != marker:
                    raise VerificationError('Continuation did not return the tool marker.')
                if not args.installed:
                    forwarded = [item for item in upstream_requests[-1] if item.get('type') == 'reasoning']
                    if forwarded != reasoning:
                        raise VerificationError('Encrypted reasoning changed before upstream dispatch.')
                evidence['cases'].append({'stream': stream, 'completed': True,
                    'encrypted_reasoning_preserved': True, 'tool_continuation': True,
                    'first_seconds': first_seconds, 'continuation_seconds': continuation_seconds})
                last_followup = followup
            foreign = copy.deepcopy(last_followup)
            foreign['stream'] = False
            next(item for item in foreign['input'] if item.get('type') == 'reasoning')['encrypted_content'] = 'unknown-synthetic-ciphertext'
            foreign['client_metadata'] = {'thread_id': 'synthetic-' + secrets.token_hex(16),
                                          'turn_id': secrets.token_hex(16)}
            before = len(upstream_requests)
            status, response, seconds = request(foreign)
            if status != 200 or response.get('status') != 'completed':
                raise VerificationError('Unknown ciphertext was not removed before one successful Azure request.')
            if not args.installed and len(upstream_requests) != before + 1:
                raise VerificationError('Unknown ciphertext did not make exactly one upstream request.')
            if not args.installed and any(item.get('encrypted_content') for item in upstream_requests[-1]):
                raise VerificationError('Unknown ciphertext reached Azure.')
            evidence['foreign_history'] = {'http_status': status, 'completed': True,
                                           'encrypted_reasoning_removed': True, 'seconds': seconds}
            if args.installed:
                evidence['gateway_requests'] = 5
            else:
                evidence['foreign_history']['upstream_attempts'] = 1
                evidence['upstream_requests'] = len(upstream_requests)
            print(json.dumps(evidence, indent=2))
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                bridge.urllib.request.build_opener = real_build_opener
                bridge.AZURE_CONNECTION = None
            upstream_requests.clear()


if __name__ == '__main__':
    try:
        main()
    except (VerificationError, OSError, ValueError, KeyError) as error:
        # Provider bodies and transport exception strings may contain private inputs.
        print(json.dumps({'verified': False, 'failure_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, VerificationError) else 'Probe failed; no private details emitted.'}))
        raise SystemExit(1)
