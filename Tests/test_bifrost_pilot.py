import concurrent.futures
import contextlib
import copy
import http.client
import http.server
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'bifrost_pilot', Path(__file__).resolve().parents[1] / 'experiments/bifrost/pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class FakeDocker:
    def __init__(self, *, logs='Bifrost v2.2.0', file_content='', omit_last_case=False,
                 before_suite=None):
        self.calls = []
        self.logs = logs
        self.file_content = file_content
        self.omit_last_case = omit_last_case
        self.before_suite = before_suite

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('image', 'inspect'):
            return '{}'
        if args[0] == 'exec':
            if '--health' in args:
                return '{"ready": true}'
            if '--suite' in args:
                if self.before_suite:
                    self.before_suite()
                cases = json.loads(args[-1])
                if self.omit_last_case:
                    cases = cases[:-1]
                return json.dumps({'cases': [{'case': case, 'passed': True} for case in cases]})
        if args[0] == 'inspect':
            return '{"Running": true, "OOMKilled": false}'
        if args[0] == 'logs':
            return self.logs
        if args[0] == 'cp':
            if args[1].endswith(':/app/data/.'):
                (Path(args[2]) / 'audit.log').write_text(self.file_content)
            return ''
        if args[0] in ('create', 'start', 'rm') or args[:2] in (
                ('network', 'create'), ('network', 'rm')):
            return ''
        raise AssertionError(f'Unexpected Docker command: {args}')


class BifrostPilotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'reports' / 'result.json'

    def run_pilot(self, docker, cases=None):
        argv = ['pilot.py', '--output', str(self.output)]
        if cases is not None:
            argv += ['--cases', *cases]
        with patch.object(pilot, 'docker', docker), patch.object(sys, 'argv', argv), \
                patch.object(pilot.platform, 'machine', return_value='arm64'), \
                contextlib.redirect_stdout(io.StringIO()):
            pilot.main()
        return json.loads(self.output.read_text())

    def assert_owned_resources_removed(self, docker):
        containers = [call[call.index('--name') + 1] for call in docker.calls if call[0] == 'create']
        networks = [call[-1] for call in docker.calls if call[:2] == ('network', 'create')]
        self.assertEqual(len(containers), 2)
        self.assertEqual(len(networks), 1)
        cleanup = [call for call in docker.calls if call[0] == 'rm' or call[:2] == ('network', 'rm')]
        self.assertEqual(cleanup, [('rm', '-f', '-v', name) for name in reversed(containers)]
                         + [('network', 'rm', networks[0])])

    def test_invalid_report_parent_is_rejected_before_resource_creation(self):
        self.output.parent.write_text('This is a file, not a directory')
        docker = FakeDocker()
        with self.assertRaises(FileExistsError):
            self.run_pilot(docker)
        self.assertFalse(any(call[0] == 'create' or call[:2] == ('network', 'create')
                             for call in docker.calls))

    def test_unwritable_report_is_rejected_before_resource_creation(self):
        docker = FakeDocker()
        original_write = Path.write_text
        def deny_report_write(path, *args, **kwargs):
            if path.parent == self.output.parent:
                raise PermissionError('Synthetic report permission failure')
            return original_write(path, *args, **kwargs)
        with patch.object(Path, 'write_text', deny_report_write), self.assertRaises(PermissionError):
            self.run_pilot(docker)
        self.assertFalse(any(call[0] == 'create' or call[:2] == ('network', 'create')
                             for call in docker.calls))

    def test_report_becoming_unwritable_still_removes_owned_resources(self):
        denied = False
        def deny_after_start():
            nonlocal denied
            denied = True
        docker = FakeDocker(before_suite=deny_after_start)
        original_write = Path.write_text
        def deny_report_write(path, *args, **kwargs):
            if denied and path.parent == self.output.parent:
                raise PermissionError('Synthetic report permission failure')
            return original_write(path, *args, **kwargs)
        with patch.object(Path, 'write_text', deny_report_write), self.assertRaises(PermissionError):
            self.run_pilot(docker)
        self.assert_owned_resources_removed(docker)

    def test_report_parent_becoming_invalid_still_removes_owned_resources(self):
        def replace_parent_with_file():
            if self.output.exists():
                self.output.unlink()
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.parent.rmdir()
            self.output.parent.write_text('Synthetic destination change')
        docker = FakeDocker(before_suite=replace_parent_with_file)
        with self.assertRaises(FileExistsError):
            self.run_pilot(docker)
        self.assert_owned_resources_removed(docker)

    def test_complete_matrix_with_verified_content_and_version_passes_synthetic_gates(self):
        docker = FakeDocker()
        report = self.run_pilot(docker)
        self.assertEqual(len(report['cases']), len(pilot.CASES))
        self.assertTrue(report['matrix_complete'])
        self.assertTrue(report['all_synthetic_gates_passed'])
        self.assertTrue(report['production_decision'].startswith('no-go'))
        self.assert_owned_resources_removed(docker)

    def test_partial_matrix_does_not_pass_all_synthetic_gates(self):
        report = self.run_pilot(FakeDocker(), cases=['basic'])
        self.assertTrue(report['cases'][0]['passed'])
        self.assertFalse(report['matrix_complete'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_missing_requested_case_does_not_complete_matrix_or_pass_gates(self):
        report = self.run_pilot(FakeDocker(omit_last_case=True))
        self.assertEqual(len(report['cases']), len(pilot.CASES) - 1)
        self.assertFalse(report['requested_cases_complete'])
        self.assertFalse(report['matrix_complete'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_console_content_leak_does_not_pass_all_synthetic_gates(self):
        report = self.run_pilot(FakeDocker(logs='v2.2.0 SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG'))
        self.assertTrue(report['synthetic_content_in_console_logs'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_runtime_content_leak_does_not_pass_all_synthetic_gates(self):
        report = self.run_pilot(FakeDocker(file_content='SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG'))
        self.assertTrue(report['synthetic_content_in_runtime_files'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_unverified_version_does_not_pass_all_synthetic_gates(self):
        report = self.run_pilot(FakeDocker(logs='Bifrost unknown version'))
        self.assertFalse(report['transport_version_banner_verified'])
        self.assertFalse(report['all_synthetic_gates_passed'])


@contextlib.contextmanager
def loopback_server(handler):
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise AssertionError('Synthetic HTTP server did not stop')


class BifrostContractTests(unittest.TestCase):
    def response(self, opaque=False):
        output = [{'type': 'message', 'id': 'msg_pilot', 'role': 'assistant', 'status': 'completed',
                   'content': [{'type': 'output_text', 'text': 'pilot-ok', 'annotations': []}]}]
        if opaque:
            output[0].update(author={'role': 'assistant', 'name': 'pilot-coder'}, recipient='pilot-architect',
                             harbor_opaque={'nested': ['synthetic', 17]})
            output.insert(0, {'type': 'reasoning', 'id': 'rs_opaque', 'summary': [],
                              'encrypted_content': 'synthetic-encrypted'})
        return {'id': 'resp_pilot', 'object': 'response', 'status': 'completed', 'model': 'pilot-deployment',
                'output': output, 'error': None,
                'usage': {'input_tokens': 11, 'input_tokens_details': {'cached_tokens': 3},
                          'output_tokens': 7, 'output_tokens_details': {'reasoning_tokens': 2},
                          'total_tokens': 18}}

    def stream_events(self, response):
        created = copy.deepcopy(response)
        created.update(status='in_progress', output=[])
        events = [{'type': 'response.created', 'response': created}]
        for index, item in enumerate(response['output']):
            initial = copy.deepcopy(item)
            initial['status'] = 'in_progress'
            if item['type'] == 'message':
                initial['content'] = []
            elif item['type'] == 'reasoning':
                initial.pop('encrypted_content', None)
            events.append({'type': 'response.output_item.added', 'output_index': index, 'item': initial})
            if item['type'] == 'message':
                events.append({'type': 'response.content_part.added', 'item_id': item['id'],
                               'output_index': index, 'content_index': 0,
                               'part': {'type': 'output_text', 'text': '', 'annotations': []}})
                events.append({'type': 'response.output_text.delta', 'item_id': item['id'],
                               'output_index': index, 'content_index': 0, 'delta': 'pilot-ok'})
            events.append({'type': 'response.output_item.done', 'output_index': index,
                           'item': copy.deepcopy(item)})
        events.append({'type': 'response.completed', 'response': copy.deepcopy(response)})
        for sequence, event in enumerate(events):
            event['sequence_number'] = sequence
        return events

    def evaluate(self, case, response=None, *, surface='azure-passthrough', stream=False,
                 status=200, attempts=1, headers=None, alter_wire=None, alter_events=None):
        body = pilot.request(case, stream=stream, surface=surface)
        wire = copy.deepcopy(body)
        wire['model'] = 'pilot-deployment'
        if alter_wire:
            alter_wire(wire)
        response = self.response() if response is None else response
        events = self.stream_events(response) if stream else []
        if alter_events:
            alter_events(events)
        data = (''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n'
                        for event in events) if stream else json.dumps(response))
        result = {'status': status, 'headers': headers or {}, 'data': data,
                  'events': events, 'elapsed_ms': 1, 'first_output_ms': 1 if stream else None}
        seen = [{'case': case, 'path': '/openai/v1/responses', 'body': copy.deepcopy(wire),
                 'synthetic_key_correct': case != 'invalid_auth', 'at': index, 'attempt': index + 1}
                for index in range(attempts)]
        return pilot.evaluate(case, body, result, seen, surface=surface)

    def test_surface_config_selects_zero_or_one_gateway_retry(self):
        for surface, retries in [('converted', 1), ('azure-passthrough', 0)]:
            with self.subTest(surface=surface):
                configuration = pilot.config('http://synthetic-azure:8081', surface=surface)
                self.assertEqual(configuration['providers']['azure']['network_config']['max_retries'], retries)

    def test_surface_request_selects_native_deployment_or_converted_model(self):
        self.assertEqual(pilot.request('basic', surface='converted')['model'], 'azure/pilot-model')
        self.assertEqual(pilot.request('basic', surface='azure-passthrough')['model'], 'pilot-deployment')

    def test_passthrough_status_failure_passes_only_without_gateway_retry(self):
        for code in (429, 500, 502, 503, 529):
            with self.subTest(code=code):
                headers = {'Retry-After': '1'} if code == 429 else {}
                row = self.evaluate(f'error{code}', status=code, headers=headers)
                self.assertTrue(row['passed'], row)
                self.assertFalse(self.evaluate(f'error{code}', status=code, headers=headers, attempts=2)['passed'])

    def test_passthrough_429_requires_forwarded_retry_after(self):
        self.assertFalse(self.evaluate('error429', status=429)['passed'])

    def test_opaque_request_contains_author_recipient_and_encrypted_history(self):
        for surface in ('converted', 'azure-passthrough'):
            with self.subTest(surface=surface):
                body = pilot.request('opaque_history', surface=surface)
                self.assertEqual(body['input'][1]['author'], {'role': 'assistant', 'name': 'pilot-architect'})
                self.assertEqual(body['input'][1]['recipient'], 'pilot-coder')
                self.assertEqual(body['input'][1]['harbor_opaque'], {'nested': ['synthetic', 17]})
                self.assertEqual(body['input'][2]['encrypted_content'], 'synthetic-encrypted')
                self.assertEqual(body['include'], ['reasoning.encrypted_content'])
                self.assertTrue(self.evaluate('opaque_history', surface=surface)['passed'])
                for index, field in ((1, 'author'), (1, 'recipient'), (1, 'harbor_opaque'), (2, 'encrypted_content')):
                    with self.subTest(field=field):
                        row = self.evaluate('opaque_history', surface=surface,
                                            alter_wire=lambda wire: wire['input'][index].pop(field))
                        self.assertFalse(row['passed'], row)

    def test_opaque_response_fields_are_required_for_json_and_stream(self):
        for case, stream in (('opaque_response', False), ('opaque_stream', True)):
            with self.subTest(case=case):
                complete = self.response(opaque=True)
                row = self.evaluate(case, complete, stream=stream)
                self.assertTrue(row['passed'], row)
                for index, field in ((0, 'encrypted_content'), (1, 'author'), (1, 'recipient'), (1, 'harbor_opaque')):
                    with self.subTest(field=field):
                        stripped = copy.deepcopy(complete)
                        del stripped['output'][index][field]
                        row = self.evaluate(case, stripped, stream=stream)
                        self.assertFalse(row['passed'], row)

    def test_opaque_stream_rejects_corrupt_delta_with_correct_final_snapshot(self):
        response = self.response(opaque=True)
        self.assertTrue(self.evaluate('opaque_stream', response, stream=True)['passed'])
        def corrupt_delta(events):
            delta = next(event for event in events if event['type'] == 'response.output_text.delta')
            delta['delta'] = 'corrupted-text'
        row = self.evaluate('opaque_stream', response, stream=True, alter_events=corrupt_delta)
        self.assertFalse(row['passed'], row)

    def test_opaque_stream_requires_encrypted_reasoning_done_and_added_author(self):
        response = self.response(opaque=True)
        self.assertTrue(self.evaluate('opaque_stream', response, stream=True)['passed'])
        for event_type, item_type, field in (
                ('response.output_item.done', 'reasoning', 'encrypted_content'),
                ('response.output_item.added', 'message', 'author')):
            with self.subTest(event_type=event_type, field=field):
                def remove_field(events):
                    event = next(event for event in events if event['type'] == event_type
                                 and event['item']['type'] == item_type)
                    del event['item'][field]
                row = self.evaluate('opaque_stream', response, stream=True, alter_events=remove_field)
                self.assertFalse(row['passed'], row)

    def test_exact_usage_is_required_for_json_and_terminal_stream_response(self):
        for case, stream in (('usage', False), ('usage_stream', True)):
            with self.subTest(case=case):
                expected = self.response()
                row = self.evaluate(case, expected, stream=stream)
                self.assertTrue(row['passed'], row)
                paths = [('input_tokens',), ('output_tokens',), ('total_tokens',),
                         ('input_tokens_details', 'cached_tokens'), ('output_tokens_details', 'reasoning_tokens')]
                for path in paths:
                    with self.subTest(path=path):
                        wrong = copy.deepcopy(expected)
                        target = wrong['usage']
                        for key in path[:-1]:
                            target = target[key]
                        target[path[-1]] += 1
                        row = self.evaluate(case, wrong, stream=stream)
                        self.assertFalse(row['passed'], row)

    def test_completed_word_in_failed_body_does_not_count_as_success(self):
        failed = self.response()
        failed.update(status='failed', error={'message': 'not completed', 'code': 'server_error'})
        failed['output'] = []
        for stream in (False, True):
            with self.subTest(stream=stream):
                self.assertFalse(self.evaluate('stream' if stream else 'basic', failed, stream=stream)['passed'])

    def test_invalid_auth_requires_401_and_no_retry(self):
        response = {'error': {'code': 'invalid_api_key', 'message': 'Synthetic invalid key'}}
        self.assertTrue(self.evaluate('invalid_auth', response, status=401)['passed'])
        self.assertFalse(self.evaluate('invalid_auth', response, status=401, attempts=2)['passed'])
        self.assertFalse(self.evaluate('invalid_auth', response, status=200)['passed'])

    def test_invalid_auth_request_selects_a_dedicated_wrong_key(self):
        body = pilot.request('invalid_auth', surface='azure-passthrough')
        self.assertEqual(body['model'], 'pilot-invalid-deployment')
        keys = pilot.config('http://synthetic-azure:8081', surface='azure-passthrough')['providers']['azure']['keys']
        matching = [key for key in keys if body['model'] in key.get('models', []) or '*' in key.get('models', [])]
        self.assertEqual(len(matching), 1)
        self.assertNotEqual(matching[0]['value'], 'synthetic-pilot-key')


class BifrostStreamTests(unittest.TestCase):
    def test_perform_posts_each_surface_to_its_exact_route(self):
        observed = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                observed.append((self.path, body['model']))
                data = b'{"status":"completed","output":[]}'
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        with loopback_server(Handler) as server:
            for surface in ('converted', 'azure-passthrough'):
                result = pilot.perform(server.server_port, pilot.request('basic', surface=surface), surface=surface)
                self.assertEqual(result['status'], 200)
        self.assertEqual(observed, [('/openai/v1/responses', 'azure/pilot-model'),
                                    ('/azure_passthrough/openai/v1/responses', 'pilot-deployment')])

    def test_perform_reads_and_rejects_duplicate_or_postterminal_events(self):
        response = {'id': 'resp_pilot', 'status': 'completed', 'output': [], 'error': None,
                    'usage': {'input_tokens': 11, 'input_tokens_details': {'cached_tokens': 3},
                              'output_tokens': 7, 'output_tokens_details': {'reasoning_tokens': 2},
                              'total_tokens': 18}}
        completed = {'type': 'response.completed', 'response': response}
        for tail in ({'type': 'response.failed', 'response': {'status': 'failed', 'error': {'code': 'server_error'}}},
                     completed,
                     {'type': 'response.output_text.delta', 'delta': 'after-completion'}):
            with self.subTest(tail=tail['type']):
                events = [{'type': 'response.output_text.delta', 'delta': 'pilot-ok'}, completed, tail]
                payload = ''.join('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n'
                                  for event in events).encode()
                class Handler(http.server.BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Content-Length', str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                with loopback_server(Handler) as server:
                    body = pilot.request('stream', stream=True, surface='azure-passthrough')
                    result = pilot.perform(server.server_port, body, surface='azure-passthrough')
                self.assertEqual(result['events'], events)
                seen = [{'case': 'stream', 'path': '/openai/v1/responses', 'body': body,
                         'synthetic_key_correct': True, 'at': 0, 'attempt': 1}]
                row = pilot.evaluate('stream', body, result, seen, surface='azure-passthrough')
                self.assertFalse(row['passed'], row)

    def test_cancel_waits_for_complete_nonempty_delta_then_closes_socket(self):
        header_sent, data_sent = threading.Event(), threading.Event()
        allow_data, allow_delimiter = threading.Event(), threading.Event()
        disconnected = threading.Event()
        delta = {'type': 'response.output_text.delta', 'delta': 'synthetic-output'}
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.write(b'event: response.output_text.delta\n')
                    self.wfile.flush()
                    header_sent.set()
                    allow_data.wait(2)
                    self.wfile.write(('data: ' + json.dumps(delta) + '\n').encode())
                    self.wfile.flush()
                    data_sent.set()
                    allow_delimiter.wait(2)
                    self.wfile.write(b'\n')
                    self.wfile.flush()
                    self.connection.settimeout(2)
                    if self.connection.recv(1) == b'':
                        disconnected.set()
                except (BrokenPipeError, ConnectionResetError):
                    disconnected.set()
        with loopback_server(Handler) as server, concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(pilot.perform, server.server_port, pilot.request('cancel', stream=True), True)
            try:
                self.assertTrue(header_sent.wait(1))
                with self.assertRaises(concurrent.futures.TimeoutError):
                    future.result(timeout=.1)
                allow_data.set()
                self.assertTrue(data_sent.wait(1))
                with self.assertRaises(concurrent.futures.TimeoutError):
                    future.result(timeout=.1)
                allow_delimiter.set()
                result = future.result(timeout=2)
                self.assertEqual(result['events'], [delta])
                self.assertIsNotNone(result['first_output_ms'])
                self.assertTrue(disconnected.wait(1))
            finally:
                allow_data.set()
                allow_delimiter.set()

    def test_truncated_or_empty_delta_does_not_count_as_output(self):
        for payload in (b'event: response.output_text.delta\n',
                        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"text"}\n',
                        b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":""}\n\n'):
            with self.subTest(payload=payload):
                class Handler(http.server.BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Content-Length', str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                with loopback_server(Handler) as server:
                    result = pilot.perform(server.server_port, pilot.request('stream', stream=True), cancel=True)
                self.assertIsNone(result['first_output_ms'])
                if not payload.endswith(b'\n\n'):
                    self.assertEqual(result['events'], [])

    def test_mock_azure_rejects_wrong_key_independently_of_case_name(self):
        with pilot.MockAzure() as server:
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
            thread.start()
            try:
                for key, expected in (('synthetic-wrong-key', 401), ('synthetic-pilot-key', 200)):
                    with self.subTest(key=key):
                        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
                        try:
                            connection.request('POST', '/openai/v1/responses', json.dumps(pilot.request('basic')),
                                               {'Content-Type': 'application/json', 'api-key': key})
                            with connection.getresponse() as response:
                                self.assertEqual(response.status, expected)
                                response.read()
                        finally:
                            connection.close()
            finally:
                server.shutdown()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
