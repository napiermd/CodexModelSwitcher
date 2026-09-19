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
        for name, value in [('TOKEN_PATH', token), ('CONFIG_DIR', self.root), ('OPENROUTER_KEY', ''), ('PROVIDER_ACTIVITY', {}), ('TURN_ROUTES', {})]:
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
        self.assertTrue(body['providers']['openrouter_ready'])
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
                b'data: {"type":"response.completed","response":{"status":"completed","output":[]}}')
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
            return Response(b'{"status":"completed","output":[]}')
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
            opener.return_value.open.return_value = Response(b'{"status":"completed","output":[]}')
            status, body = self.request(path='/harbor/v1/responses', body={'model': 'harbor/openrouter/fixture/coder', 'input': [], 'stream': False}, headers={'Authorization': 'Bearer synthetic-owner-token'})
        self.assertEqual((status, body['status']), (200, 'completed'))
        activity = bridge.provider_status()['activity']['openrouter']
        self.assertEqual((activity['completed'], activity['active'], activity['failed']), (1, 0, 0))

    def test_router_auth_rejection_clears_cached_key(self):
        bridge.OPENROUTER_KEY = 'synthetic-router-key'
        error = bridge.urllib.error.HTTPError('https://openrouter.ai/api/v1/responses', 401, 'Unauthorized', {}, io.BytesIO(b'{"error":"Unauthorized"}'))
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            status, _ = self.request(path='/harbor/v1/responses', body={'model': 'harbor/openrouter/fixture/coder', 'input': []}, headers={'Authorization': 'Bearer synthetic-owner-token'})
        self.assertEqual(status, 401)
        self.assertFalse(bridge.provider_status()['openrouter_ready'])
        self.assertEqual(bridge.provider_status()['activity']['openrouter']['http_status'], 401)


if __name__ == '__main__': unittest.main()
