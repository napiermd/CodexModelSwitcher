import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import test_provider_connections as connections
bridge = connections.bridge


class LifecycleTests(unittest.TestCase):
    def test_ticks_require_identity_stop_at_terminal_and_resequence(self):
        now = [0]
        lifecycle = bridge._stream_module.Lifecycle(interval=1, clock=lambda: now[0])
        now[0] = 2
        self.assertEqual(lifecycle.heartbeat(), b'')
        lifecycle.observe({'type': 'response.created', 'sequence_number': 0,
                           'response': {'id': 'resp_fixture', 'object': 'response'}})
        now[0] = 4
        beat = lifecycle.heartbeat()
        self.assertIn(b'resp_fixture', beat)
        self.assertNotIn(b'output', beat)
        self.assertEqual(lifecycle.observe({'type': 'response.output_text.delta', 'sequence_number': 1})['sequence_number'], 2)
        lifecycle.observe({'type': 'response.completed'})
        now[0] = 6
        self.assertEqual(lifecycle.heartbeat(), b'')

    def test_reader_ticks_during_silence_and_preserves_error(self):
        ready = threading.Event()
        def upstream():
            yield b'first\n'
            ready.wait(1)
            raise ConnectionResetError('fixture')
        reader = bridge._stream_module.lines_with_ticks(upstream(), interval=.01)
        try:
            self.assertEqual(next(reader), b'first\n')
            self.assertIsNone(next(reader))
            ready.set()
            with self.assertRaises(ConnectionResetError):
                next(reader)
        finally:
            ready.set()
            reader.close()

    def test_empty_success_fails_but_tool_calls_and_streamed_text_pass(self):
        empty = bridge.Translation({'input': []})
        frame = empty.event(b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"fixture","status":"completed","output":[]}}')
        self.assertEqual(empty.response_status, 'failed')
        self.assertNotIn(b'response.completed', frame)
        self.assertIn(b'empty_response', frame)
        for item in [{'type': 'function_call', 'name': 'fixture', 'arguments': '{}'},
                     {'type': 'message', 'content': [{'type': 'output_text', 'text': 'OK'}]}]:
            self.assertEqual(empty.validate_completion({'status': 'completed', 'output': [item]})['status'], 'completed')
        streamed = bridge.Translation({'input': []})
        streamed.event(b'data: {"type":"response.output_text.delta","delta":"OK"}')
        self.assertEqual(streamed.validate_completion({'status': 'completed', 'output': []})['status'], 'completed')

    def test_named_sse_events_without_type_are_compatible(self):
        translation = bridge.Translation({'input': []})
        result = translation.event(b'event: response.completed\ndata: {"response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}}')
        self.assertEqual(translation.response_status, 'completed')
        self.assertIn(b'"type":"response.completed"', result)

    def test_freshness_reports_version_and_publication_drift_without_claiming_loaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'model-catalogs').mkdir()
            source = root / 'models_cache.json'
            source.write_text(json.dumps({'client_version': 'old', 'models': []}))
            (root / 'model-catalogs/model-harbor.json').write_text(json.dumps({'models': [], 'harbor_sources': [
                {'provider': 'codex-subscription', 'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}))
            self.assertEqual(bridge.inspect_catalog(root, 'old')['warnings'], [])
            source.write_text(json.dumps({'client_version': 'new', 'models': []}))
            report = bridge.inspect_catalog(root, 'current')
            self.assertEqual(report['loaded_catalog'], 'unknown')
            self.assertEqual(report['changed_sources'], ['codex-subscription'])
            self.assertIn('native_client_version_mismatch', report['warnings'])
            self.assertIn('published_sources_changed', report['warnings'])

    def test_merged_catalog_refresh_does_not_change_gateway_binding_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalogs = root / 'model-catalogs'
            catalogs.mkdir()
            (root / 'model-switcher.json').write_text('{"services":[]}')
            source = catalogs / 'azure.json'
            source.write_text('{"models":[]}')
            merged = catalogs / 'model-harbor.json'
            merged.write_text('{"models":[]}')
            token = root / 'token'
            token.write_text('synthetic-token')
            with patch.object(bridge, 'CONFIG_DIR', root), patch.object(bridge, 'TOKEN_PATH', token):
                before = bridge.configuration_revision([None, ''])
                merged.write_text('{"models":[],"harbor_sources":[]}')
                self.assertEqual(bridge.configuration_revision([None, '']), before)
                source.write_text('{"models":[{"slug":"changed"}]}')
                self.assertNotEqual(bridge.configuration_revision([None, '']), before)


class NativeImagesTests(unittest.TestCase):
    setUp = connections.ProviderConnectionsTests.setUp
    stop = connections.ProviderConnectionsTests.stop

    def test_stream_emits_heartbeat_before_real_completion(self):
        bridge.OPENROUTER_KEY = 'synthetic-key'
        release = threading.Event()
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'text/event-stream'}
            def __iter__(self):
                yield b'data: {"type":"response.created","response":{"id":"fixture-response"}}\n'
                yield b'\n'
                release.wait(2)
                yield b'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"function_call","name":"fixture","arguments":"{}"}]}}\n'
                yield b'\n'
        lifecycle = bridge._stream_module.Lifecycle
        reader = bridge._stream_module.lines_with_ticks
        with patch.object(bridge._stream_module, 'Lifecycle', side_effect=lambda: lifecycle(interval=.01)), \
             patch.object(bridge.Handler, 'response_lines', side_effect=lambda upstream, budget: reader(upstream, budget, interval=.02)), \
             patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = Response()
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
            try:
                conn.request('POST', '/harbor/v1/responses', body=json.dumps({
                    'model': 'harbor/openrouter/fixture/coder', 'input': [], 'stream': True}),
                    headers={'Authorization': 'Bearer synthetic-owner-token'})
                response = conn.getresponse()
                frames = b''
                while b'event: response.in_progress' not in frames:
                    frames += response.readline()
                self.assertIn(b'fixture-response', frames)
                self.assertNotIn(b'response.completed', frames)
                release.set()
                rest = response.read()
                self.assertIn(b'response.completed', rest)
                self.assertNotIn(b'response.failed', rest)
                self.assertEqual(opener.return_value.open.call_count, 1)
            finally:
                release.set()
                conn.close()

    def test_image_routes_preserve_bytes_account_and_response_without_model_rewrite(self):
        for suffix, content_type, raw in [
            ('generations', 'application/json', b'{"prompt":"fixture","model":"native-image-model"}'),
            ('edits', 'multipart/form-data; boundary=fixture', b'--fixture\r\nopaque-image-bytes\r\n--fixture--')]:
            class Response(io.BytesIO):
                status = 200
                headers = {'Content-Type': 'application/json'}
            with patch.object(bridge.urllib.request, 'build_opener') as opener:
                opener.return_value.open.return_value = Response(b'{"data":[{"b64_json":"fixture"}]}')
                conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
                conn.request('POST', '/harbor/v1/images/' + suffix, body=raw, headers={
                    'X-Model-Harbor-Token': 'synthetic-owner-token',
                    'Authorization': 'Bearer synthetic.jwt.token', 'ChatGPT-Account-ID': 'fixture-account',
                    'Content-Type': content_type, 'x-codex-imagegen-request-id': 'fixture-request'})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())['data'][0]['b64_json'], 'fixture')
                conn.close()
                request = opener.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, 'https://chatgpt.com/backend-api/codex/images/' + suffix)
                self.assertEqual(request.data, raw)
                self.assertEqual(request.get_header('Chatgpt-account-id'), 'fixture-account')
                self.assertEqual(request.get_header('Content-type'), content_type)
                self.assertNotIn('X-model-harbor-token', request.headers)
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_image_request_requires_both_local_and_subscription_auth(self):
        for headers in [ {}, {'Authorization': 'Bearer synthetic-owner-token'},
                         {'X-Model-Harbor-Token': 'synthetic-owner-token', 'Authorization': 'Bearer sk-fixture'} ]:
            with patch.object(bridge.urllib.request, 'build_opener') as opener:
                conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
                conn.request('POST', '/harbor/v1/images/generations', body=b'{}', headers=headers)
                response = conn.getresponse()
                self.assertIn(response.status, (400, 401))
                response.read()
                conn.close()
                opener.assert_not_called()
