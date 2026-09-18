"""Actual loopback HTTP coverage of Azure deadline, cancellation, and replay safety."""
import concurrent.futures
import http.client
import http.server
import json
import socket
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

import test_azure_handler_admission as admission

bridge = admission.bridge


class AzureHandlerDeadlineTests(unittest.TestCase):
    request = admission.AzureHandlerAdmissionTests.request
    stop = admission.AzureHandlerAdmissionTests.stop
    write_effort = admission.AzureHandlerAdmissionTests.write_effort
    configure = admission.AzureHandlerAdmissionTests.configure
    source = admission.AzureHandlerAdmissionTests.source
    inference = admission.AzureHandlerAdmissionTests.inference
    wait_for = admission.AzureHandlerAdmissionTests.wait_for

    def setUp(self):
        admission.AzureHandlerAdmissionTests.setUp(self)
        self.mode = 'headers'
        self.attempts = 0
        self.arrived = threading.Event()
        self.disconnected = threading.Event()
        self.end = threading.Event()
        owner = self

        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                owner.attempts += 1
                owner.arrived.set()
                try:
                    if owner.mode == 'headers':
                        self.wfile.write(b'HTTP/1.1 200 OK\r\nX-Trickle: ')
                    elif owner.mode in ('error', 'retryable'):
                        self.send_response(429)
                        self.send_header('Retry-After', '2')
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        if owner.mode == 'retryable':
                            self.wfile.write(b'{"error":{"message":"synthetic limit"}}')
                            return
                    else:
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream' if owner.mode in ('stream', 'stream-eof', 'backpressure') else 'application/json')
                        if owner.mode == 'verify-truncated':
                            self.send_header('Content-Length', '100')
                        self.end_headers()
                        if owner.mode in ('complete', 'verify-truncated'):
                            self.wfile.write(b'{"status":"completed","output":[]}')
                            return
                        if owner.mode == 'backpressure':
                            event = {'type': 'response.output_text.delta', 'delta': 'x' * (1024 * 1024)}
                            frame = b'data: ' + json.dumps(event).encode() + b'\n\n'
                            for _ in range(32):
                                self.wfile.write(frame)
                        if owner.mode in ('stream', 'stream-eof'):
                            event = {'type': 'response.output_text.delta', 'item_id': 'msg_fixture',
                                     'output_index': 0, 'content_index': 0, 'delta': 'partial-marker'}
                            self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\n')
                            if owner.mode == 'stream-eof':
                                self.wfile.flush()
                                return
                    self.wfile.flush()
                    self.connection.settimeout(.02)
                    while not owner.end.is_set():
                        try:
                            if not self.connection.recv(1):
                                owner.disconnected.set()
                                return
                        except socket.timeout:
                            if owner.mode in ('headers', 'body', 'error'):
                                self.wfile.write(b' ')
                                self.wfile.flush()
                except OSError:
                    owner.disconnected.set()

        upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        upstream.daemon_threads = True
        runner = threading.Thread(target=lambda: upstream.serve_forever(poll_interval=.01), daemon=True)
        runner.start()

        def cleanup():
            self.end.set()
            upstream.shutdown()
            upstream.server_close()
            runner.join(2)
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

        for name, value in [('AZURE_REQUEST_SECONDS', .35), ('AZURE_VERIFY_SECONDS', .35)]:
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(bridge.urllib.request, 'build_opener', side_effect=loopback_builder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def finished(self):
        self.wait_for(lambda: self.runtime.status()['active_requests'] == 0)
        self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['keys'], 0)

    def test_trickling_headers_deadline_closes_socket_and_retains_uncertain_turn(self):
        began = time.monotonic()
        status, body = self.inference('trickle-headers')
        self.assertEqual(status, 504)
        self.assertIn(b'total deadline', body)
        self.assertLess(time.monotonic() - began, 2)
        self.assertTrue(self.disconnected.wait(1))
        self.finished()
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.inference('trickle-headers')[0], 400)
        self.assertEqual(self.attempts, 1)

    def test_json_body_trickle_returns_valid_deadline_error_before_success_headers(self):
        self.mode = 'body'
        status, body = self.inference('json-stall')
        self.assertEqual(status, 504)
        self.assertIn('error', json.loads(body))
        self.assertNotIn(b'event:', body)
        self.assertTrue(self.disconnected.wait(1))
        self.finished()
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.attempts, 1)

    def test_partial_stream_deadline_reports_failure_without_replaying_output(self):
        self.mode = 'stream'
        status, body = self.inference('stream-stall', stream=True)
        self.assertEqual(status, 200)
        self.assertEqual(body.count(b'partial-marker'), 1)
        self.assertIn(b'response.failed', body)
        self.assertIn(b'total deadline', body)
        self.assertTrue(self.disconnected.wait(1))
        self.finished()
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.inference('stream-stall', stream=True)[0], 400)
        self.assertEqual(self.attempts, 1)

    def test_partial_stream_eof_reports_failure_and_preserves_uncertain_delivery(self):
        self.mode = 'stream-eof'
        status, body = self.inference('stream-eof', stream=True)
        self.assertEqual(status, 200)
        self.assertEqual(body.count(b'partial-marker'), 1)
        self.assertIn(b'response.failed', body)
        self.assertIn(b'ended before a terminal response event', body)
        self.assertNotIn(b'response.completed', body)
        self.finished()
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.inference('stream-eof', stream=True)[0], 400)
        self.assertEqual(self.attempts, 1)

    def test_error_body_stall_is_not_a_known_delivered_outcome(self):
        self.mode = 'error'
        status, body = self.inference('error-stall')
        self.assertEqual(status, 504)
        self.assertIn(b'total deadline', body)
        self.assertTrue(self.disconnected.wait(1))
        self.finished()
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.attempts, 1)

    def test_queued_deadline_never_dispatches_and_preserves_existing_readiness(self):
        queue = bridge._admission_module.AdmissionQueue(active_limit=1)
        route = {'provider': 'azure', 'model': 'coding-prod'}
        bridge.record_readiness(route, bridge.configuration_revision(), 'verified')
        with patch.object(bridge, 'AZURE_ADMISSION', queue):
            held = queue.acquire((self.endpoint, 'coding-prod'))
            try:
                status, _body = self.inference('queued-deadline')
                self.assertEqual(status, 504)
                self.wait_for(lambda: self.runtime.status()['active_requests'] == 0)
                self.assertEqual(self.runtime.status()['uncertain_turns'], 0)
                status, body = self.request(path='/harbor/verify', body={'model': 'harbor/azure/coding-prod'})
                self.assertEqual((status, body['result']), (503, 'busy'))
                self.assertTrue(bridge.provider_status()['azure_ready'])
                self.assertEqual(self.attempts, 0)
            finally:
                held.release()
            self.mode = 'complete'
            self.assertEqual(self.inference('queued-deadline')[0], 200)
            self.finished()
            self.assertEqual(self.attempts, 1)

    def test_queue_and_upstream_share_the_same_budget_instance(self):
        queue = bridge._admission_module.AdmissionQueue(active_limit=1)
        budgets = []
        original_budget = bridge._transport_module.RequestBudget
        def tracked_budget(*args, **kwargs):
            budget = original_budget(*args, **kwargs)
            budgets.append(budget)
            return budget
        with patch.object(bridge, 'AZURE_ADMISSION', queue), \
             patch.object(bridge._transport_module, 'RequestBudget', side_effect=tracked_budget):
            held = queue.acquire((self.endpoint, 'coding-prod'))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as workers:
                result = workers.submit(self.inference, 'queue-then-stall')
                try:
                    self.wait_for(lambda: queue.snapshot()['waiting'] == 1)
                    first_deadline = budgets[0].deadline
                    time.sleep(.12)
                finally:
                    held.release()
                self.assertEqual(result.result(timeout=2)[0], 504)
            self.finished()
        self.assertEqual(len(budgets), 2)
        self.assertFalse(budgets[1].dispatch_possible)
        self.assertEqual(budgets[0].deadline, first_deadline)
        self.assertEqual(budgets[0].stop_reason, 'deadline')
        self.assertEqual(self.attempts, 1)

    def test_client_disconnect_interrupts_blocked_upstream_and_releases_slot(self):
        with patch.object(bridge, 'AZURE_REQUEST_SECONDS', 5):
            client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
            try:
                client.request('POST', '/harbor/v1/responses', json.dumps(self.source('cancel-upstream')),
                               {'Authorization': 'Bearer synthetic-owner-token'})
                self.assertTrue(self.arrived.wait(1))
                client.sock.shutdown(socket.SHUT_RDWR)
                client.close()
                self.assertTrue(self.disconnected.wait(1))
                self.finished()
            finally:
                client.close()
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.attempts, 1)

    def test_verification_rejects_completed_json_with_truncated_http_framing(self):
        self.mode = 'verify-truncated'
        route = {'provider': 'azure', 'model': 'coding-prod'}
        bridge.record_readiness(route, bridge.configuration_revision(), 'verified')
        status, body = self.request(path='/harbor/verify', body={'model': 'harbor/azure/coding-prod'})
        self.assertEqual((status, body['result']), (503, 'unavailable'))
        self.assertFalse(bridge.provider_status()['azure_ready'])
        self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['keys'], 0)
        self.assertEqual(self.attempts, 1)

    def test_dispatched_verification_timeout_invalidates_stale_proof(self):
        route = {'provider': 'azure', 'model': 'coding-prod'}
        bridge.record_readiness(route, bridge.configuration_revision(), 'verified')
        status, body = self.request(path='/harbor/verify', body={'model': 'harbor/azure/coding-prod'})
        self.assertEqual((status, body['result']), (503, 'unavailable'))
        self.assertFalse(bridge.provider_status()['azure_ready'])
        self.assertTrue(self.disconnected.wait(1))
        self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['keys'], 0)
        self.assertEqual(self.attempts, 1)

    def test_nonreading_client_cannot_hold_an_upstream_slot_past_deadline(self):
        self.mode = 'backpressure'
        writing = threading.Event()
        original_write = bridge.Handler.write_output
        def small_buffer(handler, value, budget=None, **kwargs):
            if len(value) > 512 * 1024:
                handler.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                writing.set()
            return original_write(handler, value, budget, **kwargs)
        client = socket.socket()
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
        client.settimeout(2)
        client.connect(('127.0.0.1', self.server.server_port))
        with patch.object(bridge.Handler, 'write_output', small_buffer):
            try:
                body = json.dumps(self.source('blocked-delivery', stream=True)).encode()
                client.sendall(b'POST /harbor/v1/responses HTTP/1.1\r\nHost: localhost\r\n'
                               b'Authorization: Bearer synthetic-owner-token\r\n'
                               + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
                self.assertTrue(writing.wait(1))
                self.finished()
                self.assertTrue(self.disconnected.wait(1))
            finally:
                client.close()
        self.assertEqual(self.runtime.status()['uncertain_turns'], 1)
        self.assertEqual(self.attempts, 1)

    def test_completed_error_is_delivered_once_and_is_not_uncertain(self):
        self.mode = 'retryable'
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        held_socket = None
        try:
            connection.request('POST', '/harbor/v1/responses', json.dumps(self.source('retryable-once')),
                               {'Authorization': 'Bearer synthetic-owner-token'})
            # HTTPResponse closes its socket after reading a Connection: close body.
            # Keep this client connected until the handler's delivery checks finish.
            held_socket = connection.sock.dup()
            with connection.getresponse() as response:
                body = response.read()
                self.assertEqual(response.status, 429)
            self.assertEqual(json.loads(body)['error']['message'], 'synthetic limit')
            self.finished()
            self.assertEqual(self.runtime.status()['uncertain_turns'], 0)
            self.assertEqual(self.attempts, 1)
        finally:
            connection.close()
            if held_socket is not None:
                held_socket.close()


if __name__ == '__main__':
    unittest.main()
