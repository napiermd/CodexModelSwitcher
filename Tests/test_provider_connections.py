from contextlib import contextmanager
import time
import http.client
import http.server
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('provider_adapter', Path(__file__).resolve().parents[1] / 'ModelHarbor/Support/grok_adapter.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ProviderConnectionsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        token = self.root / 'token'
        token.write_text('synthetic-owner-token')
        (self.root / 'model-switcher.json').write_text(json.dumps({'services': [{'id': 'openrouter', 'models': [{'id': 'fixture/coder'}]}]}))
        for name, value in [('TOKEN_PATH', token), ('CONFIG_DIR', self.root), ('OPENROUTER_KEY', ''), ('PROVIDER_ACTIVITY', {}), ('TURN_ROUTES', {}), ('READINESS', bridge._runtime_module.RouteReadiness()), ('REQUEST_METRICS', bridge._metrics_module.Recorder()), ('REQUEST_METRICS_ENABLED', True)]:
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), bridge.Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method='POST', path='/harbor/providers/openrouter', body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            conn.request(method, path, body=json.dumps(body or {}).encode() if method == 'POST' else None,
                         headers=headers if headers is not None else {'X-Model-Harbor-Token': 'synthetic-owner-token'})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_only_owner_can_set_key_and_status_never_returns_it(self):
        key = 'synthetic-private-provider-key'
        for headers in [{}, {'X-Model-Harbor-Token': 'wrong'}, {'X-Model-Harbor-Token': 'synthetic-owner-token', 'Origin': 'https://example.org'}]:
            self.assertEqual(self.request(body={'key': key}, headers=headers)[0], 401)
            self.assertEqual(bridge.OPENROUTER_KEY, '')
        self.assertEqual(self.request(body={'key': key}), (200, {'configured': True}))
        status, body = self.request('GET', '/harbor/status')
        self.assertEqual(status, 200)
        self.assertFalse(body['providers']['openrouter_ready'])
        self.assertTrue(body['providers']['credentials_available']['openrouter'])
        self.assertNotIn(key, json.dumps(body))
        self.assertEqual(self.request(body={'key': ''})[0], 200)
        self.assertFalse(bridge.provider_status()['openrouter_ready'])

    def test_partial_stream_failures_are_explicit_and_never_retried(self):
        import socket
        self.request(body={'key': 'synthetic-key'})
        prefix = b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
        for failure in [None, socket.timeout(), OSError('private upstream detail')]:
            with self.subTest(failure=type(failure).__name__):
                class Response(io.BytesIO):
                    status = 200
                    headers = {'Content-Type': 'text/event-stream'}
                    def __next__(self):
                        line = self.readline()
                        if not line and failure:
                            raise failure
                        if not line:
                            raise StopIteration
                        return line
                with patch.object(bridge.urllib.request, 'build_opener') as opener:
                    opener.return_value.open.return_value = Response(prefix)
                    conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
                    try:
                        conn.request('POST', '/harbor/v1/responses', body=json.dumps({
                            'model': 'harbor/openrouter/fixture/coder', 'stream': True, 'input': []}),
                            headers={'Authorization': 'Bearer synthetic-owner-token'})
                        response = conn.getresponse()
                        payload = response.read()
                    finally:
                        conn.close()
                    self.assertEqual(response.status, 200)
                    self.assertIn(b'"delta":"partial"', payload)
                    self.assertIn(b'response.failed', payload)
                    self.assertNotIn(b'response.completed', payload)
                    self.assertNotIn(b'private upstream detail', payload)
                    self.assertEqual(opener.return_value.open.call_count, 1)

    def test_terminal_event_without_trailing_blank_line_is_preserved(self):
        self.request(body={'key': 'synthetic-key'})
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'text/event-stream'}
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = Response(
                b'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}}')
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
            try:
                conn.request('POST', '/harbor/v1/responses', body=json.dumps({
                    'model': 'harbor/openrouter/fixture/coder', 'stream': True}),
                    headers={'Authorization': 'Bearer synthetic-owner-token'})
                payload = conn.getresponse().read()
            finally:
                conn.close()
        self.assertIn(b'response.completed', payload)
        self.assertNotIn(b'response.failed', payload)

    def test_malformed_or_header_injecting_keys_are_rejected(self):
        for key in [None, 123, 'bad\r\nAuthorization: injected', 'x' * 4097]:
            self.assertEqual(self.request(body={'key': key})[0], 400)
        self.assertEqual(bridge.OPENROUTER_KEY, '')

    def test_exact_openrouter_route_and_credentials_never_use_grok(self):
        bridge.OPENROUTER_KEY = 'synthetic-router-key'
        with patch.object(bridge, 'oauth_headers', side_effect=AssertionError('Wrong provider')):
            translation, headers, base, route = bridge.routed_request({'model': 'harbor/openrouter/fixture/coder', 'input': [], 'reasoning': {'effort': 'high'}}, {})
        self.assertEqual(base, 'https://openrouter.ai/api/v1')
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-router-key')
        self.assertEqual(route, {'provider': 'openrouter', 'model': 'fixture/coder'})
        self.assertEqual(translation.request['model'], 'fixture/coder')
        self.assertFalse(translation.request['provider']['require_parameters'])
        self.assertEqual(translation.request['reasoning']['effort'], 'high')
        with self.assertRaises(ValueError):
            bridge.requested_route('harbor/openrouter/unknown/model')

    def test_openrouter_bounds_only_an_omitted_output_allowance(self):
        bridge.OPENROUTER_KEY = 'synthetic-router-key'
        for supplied, expected in ((None, 32768), (512, 512), (64000, 64000)):
            source = {'model': 'harbor/openrouter/fixture/coder', 'input': []}
            if supplied is not None:
                source['max_output_tokens'] = supplied
            translated, _, _, _ = bridge.routed_request(source, {})
            self.assertEqual(translated.request['max_output_tokens'], expected)

    def test_unconnected_router_fails_without_fallback(self):
        with patch.object(bridge, 'oauth_headers', side_effect=AssertionError('Wrong provider')):
            with self.assertRaisesRegex(ValueError, 'Connect OpenRouter'):
                bridge.routed_request({'model': 'harbor/openrouter/fixture/coder', 'input': []}, {})

    def test_openrouter_keeps_tool_settings_without_strict_endpoint_filter(self):
        bridge.OPENROUTER_KEY = 'synthetic-router-key'
        source = {'model': 'harbor/openrouter/fixture/coder', 'input': [],
                  'tools': [{'type': 'function', 'name': 'check',
                             'parameters': {'type': 'object', 'properties': {}}}],
                  'tool_choice': 'auto', 'parallel_tool_calls': False}
        translation, _, _, _ = bridge.routed_request(source, {})
        self.assertEqual(translation.request['tool_choice'], 'auto')
        self.assertEqual(translation.request['tools'], source['tools'])
        self.assertEqual(translation.request['provider'], {'require_parameters': False})
        self.assertFalse(translation.request['parallel_tool_calls'])
        self.assertEqual(source['tool_choice'], 'auto')
        for choice in ('required', 'none', {'type': 'function', 'name': 'check'}):
            with self.subTest(choice=choice):
                source['tool_choice'] = choice
                translated, _, _, _ = bridge.routed_request(source, {})
                self.assertEqual(translated.request['tool_choice'], choice)

    def test_tool_request_reaches_openrouter_without_strict_endpoint_filter(self):
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json'}
        bridge.OPENROUTER_KEY = 'synthetic-router-key'
        def upstream(request, **kwargs):
            body = json.loads(request.data)
            self.assertEqual(body['model'], 'fixture/coder')
            self.assertEqual(body['provider'], {'require_parameters': False})
            self.assertEqual(body['tool_choice'], 'auto')
            self.assertEqual(body['tools'][0]['name'], 'check')
            return Response(b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}')
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = upstream
            status, body = self.request(path='/harbor/v1/responses', body={
                'model': 'harbor/openrouter/fixture/coder', 'input': [], 'stream': False,
                'tool_choice': 'auto', 'tools': [{'type': 'function', 'name': 'check',
                'parameters': {'type': 'object', 'properties': {}}}]},
                headers={'Authorization': 'Bearer synthetic-owner-token'})
        self.assertEqual((status, body['status']), (200, 'completed'))

    def test_concurrent_provider_activity_counts_success_and_incomplete_separately(self):
        a = {'provider': 'baseten', 'model': 'a'}
        b = {'provider': 'baseten', 'model': 'b'}
        c = {'provider': 'openrouter', 'model': 'c'}
        for route in [a, b, c]: bridge.provider_activity_start(route)
        bridge.provider_activity_finish(a, 'completed')
        status = bridge.provider_status()['activity']
        self.assertEqual(status['baseten']['active'], 1)
        self.assertEqual(status['baseten']['model'], 'b')
        self.assertEqual(status['baseten']['state'], 'working')
        self.assertEqual(status['openrouter']['active'], 1)
        bridge.provider_activity_finish(b, 'incomplete')
        bridge.provider_activity_finish(c, 'failed', 429)
        status = bridge.provider_status()['activity']
        self.assertEqual(status['baseten']['completed'], 1)
        self.assertEqual(status['baseten']['failed'], 1)
        self.assertEqual(status['baseten']['active'], 0)
        self.assertEqual(status['openrouter']['completed'], 0)
        self.assertEqual(status['openrouter']['http_status'], 429)
        self.assertIn('last_success', status['baseten'])

    def test_auth_failure_survives_rate_limit_until_another_success(self):
        route = {'provider': 'codex-subscription', 'model': 'fixture/coder'}
        for auth_status in (401, 403):
            with self.subTest(auth_status=auth_status):
                bridge.PROVIDER_ACTIVITY.clear()
                with patch.object(bridge.time, 'time', side_effect=[100.0, 101.0, 102.0, 103.0]):
                    for status, code in [('completed', None), ('failed', auth_status), ('failed', 429)]:
                        bridge.provider_activity_start(route)
                        bridge.provider_activity_finish(route, status, code)
                    activity = bridge.provider_status()['activity']['codex-subscription']
                    self.assertEqual(activity['last_success'], 100.0)
                    self.assertEqual(activity['last_auth_failure'], 101.0)
                    self.assertEqual(activity['last_failure'], 102.0)
                    self.assertEqual(activity['http_status'], 429)
                    bridge.provider_activity_start(route)
                    bridge.provider_activity_finish(route, 'completed')
                    activity = bridge.provider_status()['activity']['codex-subscription']
                    self.assertEqual(activity['last_success'], 103.0)
                    self.assertEqual(activity['last_auth_failure'], 101.0)
                    self.assertNotIn('http_status', activity)

    def test_completed_response_traverses_http_bridge_and_records_success(self):
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json'}
        bridge.OPENROUTER_KEY = 'synthetic-router-key'
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = Response(b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}')
            status, body = self.request(path='/harbor/v1/responses', body={'model': 'harbor/openrouter/fixture/coder', 'input': [], 'stream': False}, headers={'Authorization': 'Bearer synthetic-owner-token'})
        self.assertEqual((status, body['status']), (200, 'completed'))
        activity = bridge.provider_status()['activity']['openrouter']
        self.assertEqual((activity['completed'], activity['active'], activity['failed']), (1, 0, 0))
        metric = bridge.REQUEST_METRICS.snapshot()[-1]
        self.assertEqual(metric['state'], 'completed')
        self.assertEqual(metric['wire_bytes'], len(opener.return_value.open.call_args.args[0].data))
        self.assertEqual(metric['provider'], 'openrouter')
        status, reported = self.request('GET', '/harbor/status')
        self.assertEqual(status, 200)
        self.assertTrue(reported['request_metrics']['enabled'])
        self.assertEqual(reported['request_metrics']['records'][-1], metric)

    def test_router_auth_rejection_clears_cached_key(self):
        bridge.OPENROUTER_KEY = 'synthetic-router-key'
        error = bridge.urllib.error.HTTPError('https://openrouter.ai/api/v1/responses', 401, 'Unauthorized', {}, io.BytesIO(b'{"error":"Unauthorized"}'))
        finished = threading.Event()
        original_finish = bridge.provider_activity_finish
        def record_finished(*args):
            original_finish(*args)
            finished.set()
        with patch.object(bridge.urllib.request, 'build_opener') as opener, \
             patch.object(bridge, 'provider_activity_finish', side_effect=record_finished):
            opener.return_value.open.side_effect = error
            status, _ = self.request(path='/harbor/v1/responses', body={'model': 'harbor/openrouter/fixture/coder', 'input': []}, headers={'Authorization': 'Bearer synthetic-owner-token'})
            self.assertTrue(finished.wait(2), 'Provider activity did not finish after the HTTP error')
        self.assertEqual(status, 401)
        self.assertFalse(bridge.provider_status()['openrouter_ready'])
        self.assertEqual(bridge.provider_status()['activity']['openrouter']['http_status'], 401)

    class CompletedResponse(io.BytesIO):
        status = 200
        headers = {'Content-Type': 'application/json'}

        def __init__(self):
            super().__init__(b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}')

    def inference(self):
        return self.request(path='/harbor/v1/responses',
                            body={'model': 'harbor/openrouter/fixture/coder', 'input': [], 'stream': False},
                            headers={'Authorization': 'Bearer synthetic-owner-token'})

    def probe(self):
        return self.request(path='/harbor/verify', body={'model': 'harbor/openrouter/fixture/coder'})

    def configure(self, key):
        self.assertEqual(self.request(body={'key': key}), (200, {'configured': True}))

    @contextmanager
    def delayed_probe_server(self, delay, delay_body=False):
        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body = b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}'
                try:
                    if not delay_body:
                        time.sleep(delay)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.flush()
                    if delay_body:
                        time.sleep(delay)
                    self.wfile.write(body)
                except OSError:
                    pass

        upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        upstream.daemon_threads = True
        runner = threading.Thread(target=lambda: upstream.serve_forever(poll_interval=.01), daemon=True)
        runner.start()
        build = bridge.urllib.request.build_opener

        def loopback(*handlers):
            opener = build(bridge.urllib.request.ProxyHandler({}), *handlers)
            class LocalOpener:
                def open(self, request, **kwargs):
                    local = bridge.urllib.request.Request(
                        f'http://127.0.0.1:{upstream.server_port}/responses', data=request.data,
                        headers=dict(request.header_items()), method=request.get_method())
                    return opener.open(local, **kwargs)
            return LocalOpener()
        try:
            with patch.object(bridge.urllib.request, 'build_opener', side_effect=loopback):
                yield
        finally:
            upstream.shutdown()
            upstream.server_close()
            runner.join(2)

    def test_openrouter_probe_accepts_headers_and_body_after_two_seconds(self):
        self.configure('synthetic-key')
        for delay_body in (False, True):
            with self.subTest(delay_body=delay_body), patch.object(bridge, 'AZURE_VERIFY_SECONDS', 3), self.delayed_probe_server(2.15, delay_body):
                status, body = self.probe()
            self.assertEqual((status, body['verified']), (200, True))
            self.assertTrue(bridge.provider_status()['openrouter_ready'])

    def test_openrouter_probe_still_enforces_its_absolute_deadline(self):
        self.configure('synthetic-key')
        with patch.object(bridge, 'AZURE_VERIFY_SECONDS', .2), self.delayed_probe_server(.6, True):
            started = time.monotonic()
            status, body = self.probe()
            elapsed = time.monotonic() - started
        self.assertEqual((status, body['verified']), (503, False))
        self.assertLess(elapsed, .5)
        self.assertFalse(bridge.provider_status()['openrouter_ready'])

    def test_stale_auth_failure_preserves_replacement_credential(self):
        self.configure('synthetic-old-key')

        def old_request_fails(request, **_):
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-old-key')
            self.configure('synthetic-new-key')
            raise bridge.urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {},
                                                io.BytesIO(b'{"error":"old credential rejected"}'))

        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = old_request_fails
            status, _ = self.inference()
        self.assertEqual(status, 401)
        self.assertEqual(bridge.OPENROUTER_KEY, 'synthetic-new-key')
        self.assertTrue(bridge.provider_status()['credentials_available']['openrouter'])
        self.assertFalse(bridge.provider_status()['openrouter_ready'])

    def test_completed_request_cannot_verify_key_changed_after_header_capture(self):
        self.configure('synthetic-old-key')
        original_headers = bridge.openrouter_headers

        def rotate_after_capture():
            selected = original_headers()
            self.configure('synthetic-unproven-key')
            return selected

        with patch.object(bridge, 'openrouter_headers', side_effect=rotate_after_capture), \
                patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = self.CompletedResponse()
            status, body = self.inference()
        sent = opener.return_value.open.call_args.args[0]
        self.assertEqual(sent.get_header('Authorization'), 'Bearer synthetic-old-key')
        self.assertEqual((status, body['status']), (200, 'completed'))
        self.assertEqual(bridge.OPENROUTER_KEY, 'synthetic-unproven-key')
        self.assertFalse(bridge.provider_status()['openrouter_ready'])
        self.assertFalse(any(proof['verified'] for proof in bridge.provider_status()['route_verification']))

    def test_probe_cannot_verify_key_changed_after_header_capture(self):
        self.configure('synthetic-old-key')
        original_headers = bridge.openrouter_headers

        def rotate_after_capture():
            selected = original_headers()
            self.configure('synthetic-unproven-key')
            return selected

        with patch.object(bridge, 'openrouter_headers', side_effect=rotate_after_capture), \
                patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = self.CompletedResponse()
            status, body = self.probe()
        sent = opener.return_value.open.call_args.args[0]
        self.assertEqual(sent.get_header('Authorization'), 'Bearer synthetic-old-key')
        self.assertEqual((status, body['verified']), (503, False))
        self.assertEqual(body['result'], 'configuration_changed')
        self.assertFalse(bridge.provider_status()['openrouter_ready'])

    def test_completed_request_does_not_verify_configuration_changed_during_upstream(self):
        self.configure('synthetic-old-key')

        def response_after_rotation(request, **_):
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-old-key')
            self.configure('synthetic-unproven-key')
            return self.CompletedResponse()

        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = response_after_rotation
            status, body = self.inference()
        self.assertEqual((status, body['status']), (200, 'completed'))
        self.assertFalse(bridge.provider_status()['openrouter_ready'])

    def test_probe_rejects_configuration_changed_during_upstream(self):
        self.configure('synthetic-old-key')

        def response_after_rotation(request, **_):
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-old-key')
            self.configure('synthetic-unproven-key')
            return self.CompletedResponse()

        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = response_after_rotation
            status, body = self.probe()
        self.assertEqual((status, body['verified']), (503, False))
        self.assertEqual(body['result'], 'configuration_changed')
        self.assertFalse(bridge.provider_status()['openrouter_ready'])

    def test_late_completed_request_cannot_replace_fresh_current_proof(self):
        self.configure('synthetic-old-key')
        fresh = {}

        def reordered_responses(request, **_):
            if request.get_header('Authorization') == 'Bearer synthetic-new-key':
                return self.CompletedResponse()
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-old-key')
            self.configure('synthetic-new-key')
            status, body = self.probe()
            self.assertEqual((status, body['verified']), (200, True))
            fresh.update(body)
            return self.CompletedResponse()

        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = reordered_responses
            status, body = self.inference()
        self.assertEqual((status, body['status']), (200, 'completed'))
        providers = bridge.provider_status()
        self.assertTrue(providers['openrouter_ready'])
        self.assertEqual(len(providers['route_verification']), 1)
        self.assertEqual(providers['route_verification'][0]['configuration_revision'], fresh['configuration_revision'])
        self.assertEqual(providers['route_verification'][0]['result'], 'verified')

    def test_late_failed_request_cannot_replace_fresh_current_proof(self):
        self.configure('synthetic-old-key')
        fresh = {}

        def reordered_responses(request, **_):
            if request.get_header('Authorization') == 'Bearer synthetic-new-key':
                return self.CompletedResponse()
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-old-key')
            self.configure('synthetic-new-key')
            status, body = self.probe()
            self.assertEqual((status, body['verified']), (200, True))
            fresh.update(body)
            raise bridge.urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {},
                                                io.BytesIO(b'{"error":"old credential rejected"}'))

        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = reordered_responses
            status, _ = self.inference()
        self.assertEqual(status, 401)
        self.assertEqual(bridge.OPENROUTER_KEY, 'synthetic-new-key')
        providers = bridge.provider_status()
        self.assertTrue(providers['openrouter_ready'])
        self.assertEqual(providers['route_verification'][0]['configuration_revision'], fresh['configuration_revision'])
        self.assertEqual(providers['route_verification'][0]['result'], 'verified')

    def test_late_probe_cannot_replace_fresh_current_proof(self):
        for old_result in ('completed', 'auth_failed'):
            with self.subTest(old_result=old_result):
                self.configure('synthetic-old-key')
                fresh = {}

                def reordered_responses(request, **_):
                    if request.get_header('Authorization') == 'Bearer synthetic-new-key':
                        return self.CompletedResponse()
                    self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-old-key')
                    self.configure('synthetic-new-key')
                    status, body = self.probe()
                    self.assertEqual((status, body['verified']), (200, True))
                    fresh.update(body)
                    if old_result == 'auth_failed':
                        raise bridge.urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {},
                                                            io.BytesIO(b'{"error":"old credential rejected"}'))
                    return self.CompletedResponse()

                with patch.object(bridge.urllib.request, 'build_opener') as opener:
                    opener.return_value.open.side_effect = reordered_responses
                    status, body = self.probe()
                self.assertEqual((status, body['verified']), (503, False))
                self.assertEqual(body['result'], 'configuration_changed')
                providers = bridge.provider_status()
                self.assertTrue(providers['openrouter_ready'])
                self.assertEqual(providers['route_verification'][0]['configuration_revision'], fresh['configuration_revision'])
                self.assertEqual(providers['route_verification'][0]['result'], 'verified')


if __name__ == '__main__': unittest.main()
