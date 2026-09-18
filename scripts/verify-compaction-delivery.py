#!/usr/bin/env python3
"""Opt-in synthetic compaction/continuation proof; closes at the terminal SSE frame."""
import argparse
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--installed', action='store_true')
    args = parser.parse_args()
    home = Path.home() / '.codex'
    support = Path(__file__).resolve().parents[1] / 'ModelHarbor/Support/grok_adapter.py'
    spec = importlib.util.spec_from_file_location('compaction_bridge', support)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    with tempfile.TemporaryDirectory(prefix='harbor-compaction-proof-') as directory:
        root = Path(directory)
        server = None
        runtime = None
        token = (home / 'model-harbor-bridge-token').read_text().strip()
        port = 48118
        if not args.installed:
            saved = json.loads((home / 'model-switcher.json').read_text())
            azure = next(s for s in saved['services'] if s['id'] == 'azure')
            key = subprocess.run(['security', 'find-generic-password', '-s', 'dev.napier.ModelHarbor',
                                  '-a', 'provider:azure', '-w'], capture_output=True, text=True, check=True).stdout.strip()
            bridge.CONFIG_DIR = root
            bridge.TOKEN_PATH = root / 'token'
            bridge.TOKEN_PATH.write_text(token)
            (root / 'model-switcher.json').write_text(json.dumps(saved))
            (root / 'model-catalogs').mkdir()
            (root / 'model-catalogs/azure.json').write_bytes((home / 'model-catalogs/azure.json').read_bytes())
            bridge.AZURE_CONNECTION = {'key': key, 'endpoint': azure['baseURL']}
            bridge.OPENROUTER_KEY = subprocess.run(['security', 'find-generic-password', '-s',
                'dev.napier.ModelHarbor', '-a', 'provider:openrouter', '-w'],
                capture_output=True, text=True, check=True).stdout.strip()
            runtime = bridge._runtime_module.GatewayRuntime(root / 'runtime', 'candidate', 'proof')
            bridge.RUNTIME = runtime
            server = bridge.GatewayHTTPServer(('127.0.0.1', 0), bridge.Handler)
            port = server.server_port
            threading.Thread(target=server.serve_forever, daemon=True).start()
        metadata = {'thread_id': 'synthetic-' + uuid.uuid4().hex, 'turn_id': uuid.uuid4().hex}
        def request(compact, previous=False):
            md = dict(metadata, request_kind='compaction' if compact else 'turn')
            if compact:
                md['compaction'] = {'phase': 'pre_turn' if previous else 'mid_turn', 'reason': 'token_limit'}
            body = {'model': 'harbor/azure/gpt-5.6-sol', 'stream': True,
                    'client_metadata': {'x-codex-turn-metadata': json.dumps(md)},
                    'input': [{'role': 'user', 'content': 'Reply with only HARBOR_COMPACT_OK.'}],
                    'reasoning': {'effort': 'low'}, 'max_output_tokens': 256}
            if previous:
                body['model'] = 'harbor/openrouter/anthropic/claude-fable-5.1'
            client = http.client.HTTPConnection('127.0.0.1', port, timeout=90)
            try:
                client.request('POST', '/harbor/v1/responses', json.dumps(body), {'Authorization': 'Bearer ' + token})
                response = client.getresponse()
                assert response.status == 200, response.status
                assert response.getheader('X-Model-Harbor-Provider') == ('openrouter' if previous else 'azure')
                for line in response:
                    if line.startswith(b'data: '):
                        event = json.loads(line[6:])
                        if event.get('type') == 'response.completed':
                            assert event['response']['status'] == 'completed'
                            response.close()
                            return
                raise AssertionError('No completed response')
            finally:
                client.close()
        try:
            request(True, previous=True)
            time.sleep(.15)
            for index in range(3):
                request(index == 0)
                time.sleep(.15)
            if runtime:
                assert runtime.status()['uncertain_turns'] == 0
            print(json.dumps({'passed': True, 'installed': args.installed,
                              'same_turn_requests': 4, 'previous_model_switch': True, 'closed_at_terminal': True}))
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if runtime:
                runtime.close()


if __name__ == '__main__':
    main()
