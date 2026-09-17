"""Incomplete Azure error responses retain ownership and downstream writes share a deadline."""
import http.client
import http.server
import json
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

import test_azure_handler_admission as admission
import test_azure_transport as transport_tests

bridge = admission.bridge
WAIT = 10


class AzureErrorBodyTests(unittest.TestCase):
    request = admission.AzureHandlerAdmissionTests.request
    stop = admission.AzureHandlerAdmissionTests.stop
    write_effort = admission.AzureHandlerAdmissionTests.write_effort
    configure = admission.AzureHandlerAdmissionTests.configure
    source = admission.AzureHandlerAdmissionTests.source

    def setUp(self):
        admission.AzureHandlerAdmissionTests.setUp(self)
        self.body = b'{"error":{"message":"synthetic limit"}}'
        self.declared_length = len(self.body)
        self.attempts = []
        owner = self

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.attempts.append(request['input'])
                self.send_response(429)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(owner.declared_length))
                self.send_header('Retry-After', '2')
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(owner.body)
                    self.wfile.flush()
                except OSError:
                    pass
                self.close_connection = True

        upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        upstream.daemon_threads = True
        runner = threading.Thread(target=lambda: upstream.serve_forever(poll_interval=.01), daemon=True)
        runner.start()

        def cleanup():
            upstream.shutdown()
            upstream.server_close()
            runner.join(WAIT)
        self.addCleanup(cleanup)
        original_builder = urllib.request.build_opener

        def loopback_builder(*handlers):
            opener = original_builder(urllib.request.ProxyHandler({}), *handlers)
            class Loopback:
                def open(self, request, **kwargs):
                    redirected = urllib.request.Request(
                        f'http://127.0.0.1:{upstream.server_port}/responses', data=request.data,
                        headers=dict(request.header_items()), method=request.get_method())
                    return opener.open(redirected, **kwargs)
            return Loopback()

        patcher = patch.object(bridge.urllib.request, 'build_opener', side_effect=loopback_builder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def inference(self, name):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=WAIT)
        try:
            connection.request('POST', '/harbor/v1/responses', json.dumps(self.source(name)),
                               {'Authorization': 'Bearer synthetic-owner-token'})
            with connection.getresponse() as response:
                return response.status, response.read()
        finally:
            connection.close()

    def assert_incomplete_outcome(self, name):
        status, _body = self.inference(name)
        self.assertGreaterEqual(status, 400)
        deadline = time.monotonic() + WAIT
        while self.runtime.status()['active_requests']:
            if time.monotonic() >= deadline:
                self.fail('Incomplete upstream response retained an active request')
            threading.Event().wait(.01)
        self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['keys'], 0)
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.attempts, [name])
        self.assertEqual(self.inference(name)[0], 400)
        self.assertEqual(self.attempts, [name], 'An uncertain request must not be replayed')

    def test_premature_eof_after_valid_json_error_retains_uncertainty(self):
        self.declared_length = len(self.body) + 79
        self.assert_incomplete_outcome('error-premature-eof')

    def test_error_larger_than_body_limit_retains_uncertainty(self):
        self.body += b' ' * (65537 - len(self.body))
        self.declared_length = len(self.body)
        self.assertEqual(len(self.body), 65537)
        self.assert_incomplete_outcome('error-over-limit')


class AzureErrorWriteBudgetTests(unittest.TestCase):
    def test_header_write_consuming_deadline_prevents_body_write(self):
        clock = transport_tests.Clock()
        budget = bridge._transport_module.RequestBudget(1, clock=clock)
        self.addCleanup(budget.finish)
        writes = []
        timeouts = []

        class Connection:
            def settimeout(self, seconds):
                timeouts.append(seconds)

        class Writer:
            def write(self, value):
                writes.append(value)
                if value.endswith(b'\r\n\r\n'):
                    clock.advance(1)
                return len(value)

            def flush(self):
                pass

        handler = bridge.Handler.__new__(bridge.Handler)
        handler.connection = Connection()
        handler.wfile = Writer()
        handler.request_version = 'HTTP/1.1'
        handler.requestline = 'POST /harbor/v1/responses HTTP/1.1'
        handler.command = 'POST'
        handler.close_connection = False
        with self.assertRaises(bridge._transport_module.RequestDeadline):
            handler.error(503, 'synthetic body must not be written', budget=budget)
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0].startswith(b'HTTP/1.1 503 '))
        self.assertTrue(writes[0].endswith(b'\r\n\r\n'))
        self.assertNotIn(b'synthetic body', writes[0])
        self.assertEqual(timeouts, [1])
        self.assertEqual(budget.stop_reason, 'deadline')


if __name__ == '__main__':
    unittest.main()
