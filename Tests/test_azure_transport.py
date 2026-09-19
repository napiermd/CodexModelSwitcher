"""Absolute deadlines exercised with synthetic resolver and loopback socket peers."""
import concurrent.futures
import errno
import http.client
from pathlib import Path
import socket
import socketserver
import ssl
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

SUPPORT = Path(__file__).resolve().parents[1] / 'ModelHarbor' / 'Support'
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import azure_transport as transport

WAIT = 10


class Clock:
    def __init__(self):
        self.now = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.now

    def advance(self, seconds):
        with self.lock:
            self.now += seconds


class LoopbackServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, behavior):
        self.behavior = behavior
        self.started = threading.Event()
        self.progress = threading.Event()
        self.stop = threading.Event()
        self.requests = []
        super().__init__(('127.0.0.1', 0), LoopbackHandler)


class LoopbackHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(WAIT)
        try:
            self.server.behavior(self)
        except (OSError, ssl.SSLError):
            pass

    def read_request(self):
        data = bytearray()
        while b'\r\n\r\n' not in data:
            part = self.request.recv(4096)
            if not part:
                return bytes(data)
            data.extend(part)
        self.server.requests.append(bytes(data))
        return bytes(data)

    def trickle(self):
        for _ in range(2):
            self.request.sendall(b' ')
        self.server.progress.set()
        while not self.server.stop.wait(.005):
            self.request.sendall(b' ')


class AzureTransportTests(unittest.TestCase):
    def budget(self, *args, **kwargs):
        budget = transport.RequestBudget(*args, **kwargs)
        self.addCleanup(budget.finish)
        return budget

    def pool(self):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
        self.addCleanup(pool.shutdown, wait=True, cancel_futures=True)
        return pool

    def server(self, behavior):
        server = LoopbackServer(behavior)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
        thread.start()
        def close():
            server.stop.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=WAIT)
        self.addCleanup(close)
        return server

    def loopback_resolver(self):
        def resolve(host, port, *_):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', port))]
        return patch.object(transport, '_RESOLVER', transport._ResolverPool(resolver=resolve))

    def opener(self, budget):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), *budget.http_handlers())

    def test_budget_uses_one_monotonic_deadline_and_keeps_reason_after_finish(self):
        clock = Clock()
        budget = self.budget(10, clock=clock)
        clock.advance(3)
        self.assertEqual(budget.remaining(), 7)
        clock.advance(7)
        self.assertTrue(budget.cancelled())
        with self.assertRaises(transport.RequestDeadline):
            budget.check()
        budget.finish()
        budget.finish()
        self.assertEqual(budget.stop_reason, 'deadline')
        self.assertFalse(budget.dispatch_possible)
        self.assertFalse(budget._watcher.is_alive())

    def test_cancelled_and_deadline_have_distinct_errors(self):
        cancellation = threading.Event()
        budget = self.budget(cancelled=cancellation.is_set)
        cancellation.set()
        self.assertTrue(budget.cancelled())
        with self.assertRaises(transport.RequestCancelled):
            budget.io(lambda: b'never')
        self.assertEqual(budget.stop_reason, 'cancelled')
        self.assertFalse(budget.dispatch_possible)

    def test_io_normalizes_interrupted_io_and_checks_success_afterward(self):
        clock = Clock()
        budget = self.budget(1, clock=clock)
        def interrupted():
            clock.advance(1)
            raise OSError('Synthetic interrupted socket')
        with self.assertRaises(transport.RequestDeadline):
            budget.io(interrupted)
        second_clock = Clock()
        second = self.budget(1, clock=second_clock)
        def late_result():
            second_clock.advance(1)
            return b'late'
        with self.assertRaises(transport.RequestDeadline):
            second.io(late_result)

    def test_cancellation_callback_failure_fails_closed(self):
        def failed_callback():
            raise OSError('Synthetic disconnected socket')
        budget = self.budget(cancelled=failed_callback)
        with self.assertRaises(transport.RequestCancelled):
            budget.check()
        self.assertFalse(budget.dispatch_possible)

    def test_registration_after_abort_interrupts_socket_without_closing_it(self):
        cancellation = threading.Event()
        budget = self.budget(cancelled=cancellation.is_set)
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        cancellation.set()
        with self.assertRaises(transport.RequestCancelled):
            budget.register(left)
        self.assertNotEqual(left.fileno(), -1)
        right.settimeout(WAIT)
        self.assertEqual(right.recv(1), b'')
        budget.unregister(left)

    def test_unregistered_socket_is_not_interrupted(self):
        cancellation = threading.Event()
        budget = self.budget(cancelled=cancellation.is_set)
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        budget.register(left)
        budget.unregister(left)
        cancellation.set()
        with self.assertRaises(transport.RequestCancelled):
            budget.check()
        left.sendall(b'open')
        self.assertEqual(right.recv(4), b'open')

    def test_delayed_dns_result_is_discarded_and_slot_retained_until_resolution_ends(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def resolve(*args):
            calls.append(args)
            entered.set()
            if not release.wait(WAIT):
                raise TimeoutError('Synthetic DNS was not released')
            finished.set()
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 9))]
        resolver = transport._ResolverPool(limit=1, resolver=resolve)
        pool = self.pool()
        self.addCleanup(release.set)
        clock = Clock()
        budget = self.budget(1, clock=clock)
        with patch.object(transport, '_RESOLVER', resolver), patch.object(transport.socket, 'socket') as sockets:
            future = pool.submit(transport._connect, 'synthetic.invalid', 443, None, budget)
            self.assertTrue(entered.wait(WAIT))
            clock.advance(1)
            with self.assertRaises(transport.RequestDeadline):
                future.result(timeout=WAIT)
            self.assertFalse(resolver._slots.acquire(blocking=False))
            second_clock = Clock()
            second = self.budget(1, clock=second_clock)
            second_future = pool.submit(resolver.resolve, 'second.invalid', 443, second)
            second_clock.advance(1)
            with self.assertRaises(transport.RequestDeadline):
                second_future.result(timeout=WAIT)
            self.assertEqual(len(calls), 1)
            release.set()
            self.assertTrue(finished.wait(WAIT))
            third = self.budget()
            self.assertEqual(resolver.resolve('third.invalid', 443, third)[0][-1], ('127.0.0.1', 9))
            self.assertEqual(len(calls), 2)
            sockets.assert_not_called()
        self.assertFalse(budget.dispatch_possible)

    def test_cancellation_during_nonblocking_connect_closes_owned_socket_without_dispatch(self):
        entered, cancellation = threading.Event(), threading.Event()
        class ConnectingSocket:
            closed = False
            interrupted = False
            def setblocking(self, _): pass
            def connect_ex(self, _): return errno.EINPROGRESS
            def shutdown(self, _): self.interrupted = True
            def close(self): self.closed = True
        sock = ConnectingSocket()
        def select_connect(*args):
            entered.set()
            cancellation.wait(WAIT)
            return [], [], []
        budget, pool = self.budget(cancelled=cancellation.is_set), self.pool()
        with self.loopback_resolver(), patch.object(transport.socket, 'socket', return_value=sock), \
                patch.object(transport.select, 'select', side_effect=select_connect):
            future = pool.submit(transport._connect, 'fixture.invalid', 443, None, budget)
            self.assertTrue(entered.wait(WAIT))
            cancellation.set()
            with self.assertRaises(transport.RequestCancelled):
                future.result(timeout=WAIT)
        self.assertTrue(sock.closed)
        self.assertTrue(sock.interrupted)
        self.assertFalse(budget.dispatch_possible)

    def test_tls_handshake_stall_obeys_deadline_before_http_dispatch(self):
        def behavior(handler):
            if handler.request.recv(65536):
                handler.server.started.set()
            handler.server.stop.wait(WAIT)
        server = self.server(behavior)
        pool, clock = self.pool(), Clock()
        budget = self.budget(1, clock=clock)
        self.addCleanup(server.stop.set)
        opener = self.opener(budget)
        with self.loopback_resolver():
            future = pool.submit(opener.open, f'https://localhost:{server.server_address[1]}/responses')
            self.assertTrue(server.started.wait(WAIT))
            clock.advance(1)
            with self.assertRaises(transport.RequestDeadline):
                future.result(timeout=WAIT)
        self.assertFalse(budget.dispatch_possible)
        self.assertEqual(server.requests, [])

    def test_trickling_headers_cannot_extend_absolute_deadline(self):
        def behavior(handler):
            handler.read_request()
            handler.request.sendall(b'HTTP/1.1 200 OK\r\nX-Synthetic: ')
            handler.trickle()
        server, pool, clock = self.server(behavior), self.pool(), Clock()
        budget = self.budget(1, clock=clock)
        self.addCleanup(server.stop.set)
        opener = self.opener(budget)
        def open_and_check():
            with opener.open(f'http://fixture.invalid:{server.server_address[1]}/responses', b'{}') as response:
                budget.check()
                return budget.io(response.read)
        with self.loopback_resolver():
            future = pool.submit(open_and_check)
            self.assertTrue(server.progress.wait(WAIT))
            clock.advance(1)
            with self.assertRaises(transport.RequestDeadline):
                future.result(timeout=WAIT)
        self.assertTrue(budget.dispatch_possible)
        self.assertEqual(len(server.requests), 1)

    def test_trickling_body_is_interrupted_after_headers_without_replay(self):
        def behavior(handler):
            handler.read_request()
            handler.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 1000000\r\nConnection: close\r\n\r\n{')
            handler.trickle()
        server, pool, clock = self.server(behavior), self.pool(), Clock()
        budget = self.budget(1, clock=clock)
        self.addCleanup(server.stop.set)
        with self.loopback_resolver():
            with self.opener(budget).open(f'http://fixture.invalid:{server.server_address[1]}/responses', b'{}') as response:
                future = pool.submit(budget.io, response.read)
                self.assertTrue(server.progress.wait(WAIT))
                clock.advance(1)
                with self.assertRaises(transport.RequestDeadline):
                    future.result(timeout=WAIT)
        self.assertTrue(response.closed)
        self.assertTrue(budget.dispatch_possible)
        self.assertEqual(len(server.requests), 1)

    def test_normal_response_uses_exact_payload_once_and_retains_owner_close(self):
        def behavior(handler):
            wire = handler.read_request()
            content_length = int(next(line.split(b':', 1)[1] for line in wire.split(b'\r\n')
                                      if line.lower().startswith(b'content-length:')))
            payload = wire.partition(b'\r\n\r\n')[2]
            while len(payload) < content_length:
                payload += handler.request.recv(content_length - len(payload))
            handler.server.payload = payload
            handler.request.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
        server, budget = self.server(behavior), self.budget()
        payload = b'{"model":"exact-deployment","input":"synthetic"}'
        with self.loopback_resolver():
            response = self.opener(budget).open(f'http://fixture.invalid:{server.server_address[1]}/responses', payload)
            try:
                self.assertEqual(budget.io(response.read), b'{}')
                self.assertEqual(server.payload, payload)
                self.assertTrue(budget.dispatch_possible)
                self.assertIsNone(budget.stop_reason)
            finally:
                response.close()
        budget.finish()
        self.assertTrue(response.closed)
        self.assertEqual(len(server.requests), 1)

    def test_proxy_connect_is_not_model_dispatch(self):
        def behavior(handler):
            wire = handler.read_request()
            if wire.startswith(b'CONNECT '):
                handler.server.started.set()
                handler.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                handler.request.recv(65536)
                handler.server.progress.set()
                handler.server.stop.wait(WAIT)
        server, pool, clock = self.server(behavior), self.pool(), Clock()
        budget = self.budget(1, clock=clock)
        self.addCleanup(server.stop.set)
        context = ssl.create_default_context()
        connection = transport._HTTPSConnection('127.0.0.1', server.server_address[1], budget=budget, context=context)
        self.addCleanup(connection.close)
        connection.set_tunnel('synthetic.invalid', 443)
        with self.loopback_resolver():
            future = pool.submit(connection.request, 'POST', '/responses', b'{}')
            self.assertTrue(server.progress.wait(WAIT))
            clock.advance(1)
            with self.assertRaises(transport.RequestDeadline):
                future.result(timeout=WAIT)
        self.assertFalse(budget.dispatch_possible)
        self.assertEqual(len(server.requests), 1)
        self.assertTrue(server.requests[0].startswith(b'CONNECT synthetic.invalid:443 '))

    def test_handlers_require_normal_certificate_and_hostname_verification(self):
        budget = self.budget()
        handler = next(handler for handler in budget.http_handlers() if isinstance(handler, urllib.request.HTTPSHandler))
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode, ssl.CERT_REQUIRED)

    def test_invalid_policy_rejected_before_watcher_creation(self):
        for options in ({'seconds': 0}, {'seconds': 181}, {'seconds': float('nan')},
                        {'poll_seconds': 0}, {'poll_seconds': .101}, {'poll_seconds': True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                transport.RequestBudget(**options)


if __name__ == '__main__':
    unittest.main()
