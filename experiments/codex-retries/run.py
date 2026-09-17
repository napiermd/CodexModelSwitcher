#!/usr/bin/env python3
"""Optional installed-CLI retry experiment using synthetic loopback Responses."""
import argparse
import datetime
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time

SCENARIOS = ('429', '503', 'partial_delta', 'partial_output_item')
TOKEN = 'synthetic-local-retry-fixture-only'


def child_environment(root, port):
    proxy = f'http://127.0.0.1:{port}'
    env = {
        'HOME': str(root / 'home'), 'CODEX_HOME': str(root / 'codex'),
        'TMPDIR': str(root / 'tmp'), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
        'LANG': 'en_US.UTF-8', 'HARBOR_RETRY_FIXTURE_TOKEN': TOKEN,
        'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost',
    }
    env.update({key: proxy for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                                      'http_proxy', 'https_proxy', 'all_proxy')})
    return env


def execute(command, root, port, timeout=30):
    env = child_environment(root, port)
    allowed = {'HOME', 'CODEX_HOME', 'TMPDIR', 'PATH', 'LANG', 'HARBOR_RETRY_FIXTURE_TOKEN',
               'NO_PROXY', 'no_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
               'http_proxy', 'https_proxy', 'all_proxy'}
    if set(env) != allowed or env != child_environment(root, port):
        raise ValueError('Unexpected child environment; refusing to launch')
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=root / 'workspace', env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
        return {'returncode': process.returncode, 'timed_out': timed_out,
                'elapsed_ms': round((time.monotonic() - started) * 1000),
                'stdout': stdout, 'stderr': stderr}
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(2)

    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self.server.case['blocked_proxy_requests'] += 1
        self.send_error(502, 'External network disabled by fixture proxy')

    def do_GET(self):
        self.server.case['unexpected_requests'] += 1
        self.send_error(502)

    def do_POST(self):
        case = self.server.case
        if self.path != '/v1/responses':
            case['unexpected_requests'] += 1
            self.send_error(502)
            return
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 2 * 1024 * 1024:
            case['unexpected_requests'] += 1
            self.send_error(413)
            return
        body = self.rfile.read(size)
        payload = json.loads(body)
        case['posts'].append({'path': self.path, 'body_bytes': len(body),
                              'elapsed_ms': round((time.monotonic() - case['started']) * 1000)})
        if (payload.get('model') != 'fixture-model' or payload.get('stream') is not True
                or self.headers.get('Authorization') != 'Bearer ' + TOKEN):
            case['unexpected_requests'] += 1
            self.send_error(400)
            return
        if case['scenario'] in ('429', '503'):
            body = b'{"error":{"message":"synthetic upstream rejection","type":"server_error","code":"fixture_rejection"}}'
            self.send_response(int(case['scenario']))
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '0')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            for event in stream_events(case['scenario'] == 'partial_output_item'):
                self.wfile.write(('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode())
                self.wfile.flush()
            case['partial_sent'] = True
        self.close_connection = True


def stream_events(complete_item):
    part = {'type': 'output_text', 'text': 'synthetic partial output', 'annotations': []}
    events = [
        {'type': 'response.created', 'response': {'id': 'resp_fixture', 'object': 'response', 'status': 'in_progress', 'output': []}},
        {'type': 'response.output_item.added', 'output_index': 0, 'item': {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'status': 'in_progress', 'content': []}},
        {'type': 'response.content_part.added', 'item_id': 'msg_fixture', 'output_index': 0, 'content_index': 0, 'part': dict(part, text='')},
        {'type': 'response.output_text.delta', 'item_id': 'msg_fixture', 'output_index': 0, 'content_index': 0, 'delta': part['text']},
    ]
    if complete_item:
        events.extend([
            {'type': 'response.output_text.done', 'item_id': 'msg_fixture', 'output_index': 0, 'content_index': 0, 'text': part['text']},
            {'type': 'response.content_part.done', 'item_id': 'msg_fixture', 'output_index': 0, 'content_index': 0, 'part': part},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'status': 'completed', 'content': [part]}},
        ])
    return [dict(event, sequence_number=index) for index, event in enumerate(events)]


def configuration(port):
    return f'''model = "fixture-model"
model_provider = "retry-fixture"
cli_auth_credentials_store = "file"
approval_policy = "never"
sandbox_mode = "read-only"
[agents]
enabled = false
[features]
apps = false
[model_providers.retry-fixture]
name = "Isolated retry fixture"
base_url = "http://127.0.0.1:{port}/v1"
wire_api = "responses"
env_key = "HARBOR_RETRY_FIXTURE_TOKEN"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
stream_idle_timeout_ms = 3000
'''


def fresh_case(parent, name):
    root = parent / name
    root.mkdir(mode=0o700)
    for item in ('home', 'codex', 'workspace', 'tmp'):
        (root / item).mkdir(mode=0o700)
    return root


def run(cli, expected_version, report):
    with tempfile.TemporaryDirectory(prefix='harbor-cli-retries-', dir='/tmp') as temporary:
        root = Path(temporary).resolve()
        server = Server(('127.0.0.1', 0), Handler)
        server.case = {'blocked_proxy_requests': 0, 'unexpected_requests': 0}
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        try:
            help_root = fresh_case(root, 'help')
            version = execute([str(cli), '--version'], help_root, server.server_port, timeout=10)
            if version['returncode'] != 0 or version['stdout'].strip() != 'codex-cli ' + expected_version:
                raise ValueError('CLI version does not match --expected-version')
            report['cli_version'] = version['stdout'].strip()
            help_result = execute([str(cli), 'exec', '--help'], help_root, server.server_port, timeout=10)
            if help_result['returncode'] != 0 or any(flag not in help_result['stdout'] for flag in
                    ('--strict-config', '--ephemeral', '--ignore-rules', '--skip-git-repo-check')):
                raise ValueError('CLI help does not expose the required isolation options')
            for scenario in SCENARIOS:
                case_root = fresh_case(root, scenario)
                (case_root / 'codex/config.toml').write_text(configuration(server.server_port))
                case = {'scenario': scenario, 'posts': [], 'partial_sent': False,
                        'blocked_proxy_requests': 0, 'unexpected_requests': 0, 'started': time.monotonic()}
                server.case = case
                command = [str(cli), 'exec', '--json', '--ephemeral', '--strict-config', '--ignore-rules',
                           '--skip-git-repo-check', '-s', 'read-only', '-C', str(case_root / 'workspace'), 'Reply only OK.']
                result = execute(command, case_root, server.server_port)
                events = [json.loads(line) for line in result.pop('stdout').splitlines() if line.strip()]
                failed = [event for event in events if event.get('type') == 'turn.failed']
                message = failed[0].get('error', {}).get('message', '') if len(failed) == 1 else ''
                output = any(event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'agent_message'
                             and event['item'].get('text') == 'synthetic partial output' for event in events)
                expected_error = scenario in ('429', '503') and scenario in message or scenario.startswith('partial_') and 'stream closed before response.completed' in message
                passed = (result['returncode'] == 1 and not result['timed_out'] and len(case['posts']) == 1
                          and case['unexpected_requests'] == 0 and expected_error
                          and not any(event.get('type') == 'turn.completed' for event in events)
                          and (not scenario.startswith('partial_') or case['partial_sent'])
                          and (scenario != 'partial_output_item' or output))
                row = dict(result, scenario=scenario, passed=bool(passed), post_attempts=len(case['posts']),
                           posts=case['posts'], partial_sse_sent=case['partial_sent'], output_item_observed=output,
                           blocked_proxy_requests=case['blocked_proxy_requests'], unexpected_requests=case['unexpected_requests'], events=events)
                sanitized = json.dumps(row).replace(str(cli), '<cli>').replace(str(root), '<temporary-root>').replace(TOKEN, '<synthetic-token>')
                sanitized = re.sub(r'127\.0\.0\.1:\d+', '127.0.0.1:<port>', sanitized)
                sanitized = re.sub(r'"thread_id":\s*"[^"]+"', '"thread_id": "<synthetic-thread>"', sanitized)
                report['cases'].append(json.loads(sanitized))
                print(f'{scenario}: posts={len(case["posts"])} elapsed_ms={result["elapsed_ms"]} passed={bool(passed)}', flush=True)
            report['passed'] = len(report['cases']) == len(SCENARIOS) and all(case['passed'] for case in report['cases'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError('Fixture server did not stop')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', required=True, type=Path, help='Absolute path to the native Codex CLI executable')
    parser.add_argument('--expected-version', default='0.150.1')
    parser.add_argument('--output', required=True, type=Path, help='New report file; existing files are never replaced')
    args = parser.parse_args()
    cli = args.codex.resolve(strict=True)
    if not args.codex.is_absolute() or not cli.is_file() or not os.access(cli, os.X_OK):
        parser.error('--codex must identify an executable file by absolute path')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', args.expected_version):
        parser.error('Invalid expected CLI version')
    report = {'schema_version': 1, 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'cli_version': None, 'cli_sha256': hashlib.sha256(cli.read_bytes()).hexdigest(),
              'request_max_retries': 0, 'stream_max_retries': 0,
              'command': '<cli> exec --json --ephemeral --strict-config --ignore-rules --skip-git-repo-check -s read-only -C <temporary-workspace> "Reply only OK."',
              'cases': [], 'passed': False, 'desktop_verified': False, 'real_provider_verified': False}
    with args.output.open('x') as output:
        try:
            run(cli, args.expected_version, report)
        except Exception as error:
            report['passed'] = False
            report['failure_type'] = type(error).__name__
        finally:
            json.dump(report, output, indent=2)
            output.write('\n')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
