#!/usr/bin/env python3
"""Opt-in live OpenRouter tool round trip through a source or installed bridge.

Uses a synthetic prompt and a saved OpenRouter key. Provider usage is billable.
No existing task or conversation is read or resumed.
"""
import argparse
import http.server
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'anthropic/claude-fable-5.1'


def request(url, token, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        'Authorization': 'Bearer ' + token, 'X-Model-Harbor-Token': token,
        'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as response:
        if not body.get('stream'):
            return json.load(response)
        for line in response:
            if not line.startswith(b'data: ') or line.strip() == b'data: [DONE]':
                continue
            event = json.loads(line[6:])
            if event.get('type') == 'response.failed':
                raise RuntimeError('Streaming continuation failed.')
            if event.get('type') == 'response.completed':
                return event['response']
    raise RuntimeError('No completed response received.')


def verify(url, token, model):
    marker = 'HARBOR_' + uuid.uuid4().hex[:12]
    history = [{'role': 'user', 'content':
        'Call harbor_echo with value ' + marker + '. After receiving its result, reply with only that value.'}]
    body = {'model': 'harbor/openrouter/' + model, 'input': history, 'stream': False,
            'tools': [{'type': 'function', 'name': 'harbor_echo',
                       'description': 'Echo a value for a routing check.',
                       'parameters': {'type': 'object', 'properties': {'value': {'type': 'string'}},
                                      'required': ['value'], 'additionalProperties': False}}],
            'tool_choice': 'auto', 'parallel_tool_calls': False,
            'reasoning': {'effort': 'low'}, 'max_output_tokens': 512}
    first = request(url, token, body)
    assert first.get('status') == 'completed', first.get('status')
    calls = [item for item in first['output'] if item.get('type') == 'function_call']
    assert len(calls) == 1 and calls[0]['name'] == 'harbor_echo', 'Expected one echo tool call'
    assert json.loads(calls[0]['arguments']) == {'value': marker}, 'Tool arguments changed'
    print('PASS: auto tool selection with parallel_tool_calls=false returned the expected call.', flush=True)
    body['input'] = history + first['output'] + [
        {'type': 'function_call_output', 'call_id': calls[0]['call_id'], 'output': marker}]
    body['stream'] = True
    second = request(url, token, body)
    text = ''.join(part.get('text', '') for item in second.get('output', [])
                   if item.get('type') == 'message' for part in item.get('content', []))
    assert text.strip() == marker, 'Continuation did not return the tool result'
    print('PASS: streamed continuation preserved the tool result on ' + model + '.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--model', default=MODEL)
    args = parser.parse_args()
    if args.installed:
        token = (Path.home() / '.codex/model-harbor-bridge-token').read_text().strip()
        verify('http://127.0.0.1:48118/harbor/v1/responses', token, args.model)
        return
    spec = importlib.util.spec_from_file_location('bridge', ROOT / 'ModelHarbor/Support/grok_adapter.py')
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    credential = subprocess.run(['security', 'find-generic-password', '-s', 'dev.napier.ModelHarbor',
                                 '-a', 'provider:openrouter', '-w'], capture_output=True, text=True, timeout=45)
    if credential.returncode:
        raise RuntimeError('The saved OpenRouter credential is unavailable.')
    with tempfile.TemporaryDirectory(prefix='harbor-openrouter-tools-') as directory:
        bridge.CONFIG_DIR = Path(directory)
        bridge.TOKEN_PATH = Path(directory) / 'token'
        token = 'synthetic-local-verification-token'
        bridge.TOKEN_PATH.write_text(token)
        (bridge.CONFIG_DIR / 'model-switcher.json').write_text(json.dumps({
            'services': [{'id': 'openrouter', 'models': [{'id': args.model}]}]}))
        bridge.OPENROUTER_KEY = credential.stdout.strip()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            verify(f'http://127.0.0.1:{server.server_port}/harbor/v1/responses', token, args.model)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == '__main__':
    main()
