#!/usr/bin/env python3
"""Opt-in, five-request Azure continuation check against an isolated source gateway.

Reads the Azure API key from stdin. Outputs aggregate evidence only: no key,
conversation, ciphertext, or provider error text. Does not contact or change the
installed gateway. Uses a synthetic read-only tool; no tool process is executed.
"""
import argparse
import copy
import http.client
import http.server
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time


class VerificationError(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--deployment', required=True)
    parser.add_argument('--effort', choices=['low', 'medium', 'high', 'xhigh'], required=True)
    args = parser.parse_args()
    key = sys.stdin.buffer.read(4097).decode().strip()
    if not key or len(key) > 4096 or any(c.isspace() for c in key):
        raise VerificationError('A single Azure API key is required on stdin.')
    support = Path(__file__).resolve().parents[1] / 'ModelHarbor/Support/grok_adapter.py'
    spec = importlib.util.spec_from_file_location('history_probe_gateway', support)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    endpoint = bridge.azure_endpoint(args.endpoint)
    evidence = {'isolated': True, 'installed_runtime_changed': False,
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
        bridge.urllib.request.build_opener = ObservedOpener

        def request(payload):
            started = time.monotonic()
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=185)
            try:
                connection.request('POST', '/harbor/v1/responses', json.dumps(payload),
                                   {'Authorization': 'Bearer ' + token})
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
                    for event in events:
                        item = event.get('item', {})
                        if event.get('type') == 'response.output_item.done' and item.get('type') == 'reasoning':
                            if item not in value.get('output', []):
                                raise VerificationError('Reasoning item changed between SSE snapshots.')
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
                    'Call read_fixture once. Then reply with only the exact marker it returns.'}]}
            last_followup = None
            for stream in [False, True]:
                first = dict(base, stream=stream, tool_choice={'type': 'function', 'name': 'read_fixture'})
                status, response, first_seconds = request(first)
                if status != 200 or response.get('status') != 'completed':
                    raise VerificationError(f'Initial response did not complete (HTTP {status}).')
                output = response.get('output', [])
                reasoning = [item for item in output if item.get('type') == 'reasoning' and item.get('encrypted_content')]
                calls = [item for item in output if item.get('type') == 'function_call']
                if not reasoning or len(calls) != 1 or calls[0].get('name') != 'read_fixture':
                    raise VerificationError('Initial response lacked encrypted reasoning or the expected tool call.')
                followup = dict(base, stream=stream, tool_choice='none', input=base['input'] + output + [
                    {'type': 'function_call_output', 'call_id': calls[0]['call_id'], 'output': marker}])
                status, response, continuation_seconds = request(followup)
                if status != 200 or response.get('status') != 'completed':
                    raise VerificationError(f'Continuation did not complete (HTTP {status}).')
                text = ''.join(part.get('text', '') for item in response.get('output', [])
                               if item.get('type') == 'message' for part in item.get('content', []))
                if text.strip() != marker:
                    raise VerificationError('Continuation did not return the tool marker.')
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
            before = len(upstream_requests)
            status, response, seconds = request(foreign)
            if status != 200 or response.get('status') != 'completed' or len(upstream_requests) != before + 1:
                raise VerificationError('Unknown ciphertext was not removed before one successful Azure request.')
            if any(item.get('encrypted_content') for item in upstream_requests[-1]):
                raise VerificationError('Unknown ciphertext reached Azure.')
            evidence['foreign_history'] = {'http_status': status, 'completed': True,
                                           'encrypted_reasoning_removed': True,
                                           'upstream_attempts': 1, 'seconds': seconds}
            evidence['upstream_requests'] = len(upstream_requests)
            print(json.dumps(evidence, indent=2))
        finally:
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
