"""Real Harbor HTTP handlers with isolated credentials and synthetic upstreams."""
import concurrent.futures
import http.client
import io
import json
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch

import test_azure_provider as azure

bridge = azure.bridge


class AzureHandlerAdmissionTests(unittest.TestCase):
    request = azure.AzureConnectionsTests.request
    stop = azure.AzureConnectionsTests.stop
    write_effort = azure.AzureConnectionsTests.write_effort
    configure = azure.AzureConnectionsTests.configure

    def setUp(self):
        azure.AzureConnectionsTests.setUp(self)
        self.runtime = bridge._runtime_module.GatewayRuntime(self.root / 'runtime', 'fixture-runtime', 'fixture-boot')
        self.addCleanup(self.runtime.close)
        for name, value in [('RUNTIME', self.runtime), ('AZURE_ADMISSION', bridge._admission_module.AdmissionQueue())]:
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.configure()

    def source(self, name, stream=False):
        return {'model': 'harbor/azure/coding-prod', 'input': name, 'stream': stream,
                'client_metadata': {'thread_id': 'fixture-task', 'turn_id': name}}

    def inference(self, name, stream=False):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            conn.request('POST', '/harbor/v1/responses', json.dumps(self.source(name, stream)),
                         {'Authorization': 'Bearer synthetic-owner-token'})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail('Expected bounded handler state was not observed')
            time.sleep(.005)

    @staticmethod
    def completed():
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json'}
        return Response(b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}')

    def test_third_request_waits_until_one_complete_stream_closes(self):
        release = [threading.Event(), threading.Event()]
        delivered = [threading.Event(), threading.Event()]
        closing = [threading.Event(), threading.Event()]
        allow_close = [threading.Event(), threading.Event()]
        calls = []
        lock = threading.Lock()

        class Stream:
            status = 200
            headers = {'Content-Type': 'text/event-stream'}
            def __init__(self, index): self.index = index
            def __enter__(self): return self
            def __exit__(self, *_):
                closing[self.index].set()
                if not allow_close[self.index].wait(3):
                    raise TimeoutError('Synthetic upstream close was not released')
                return False
            def __iter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"fixture output"}\n'
                yield b'\n'
                delivered[self.index].set()
                if not release[self.index].wait(3):
                    raise TimeoutError('Synthetic stream was not released')
                yield b'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}}\n'
                yield b'\n'

        def upstream(request, **_):
            name = json.loads(request.data)['input']
            with lock:
                calls.append(name)
            return Stream(int(name[-1])) if name.startswith('stream') else self.completed()

        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = upstream
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                try:
                    first = pool.submit(self.inference, 'stream0', True)
                    second = pool.submit(self.inference, 'stream1', True)
                    self.assertTrue(all(event.wait(2) for event in delivered))
                    third = pool.submit(self.inference, 'queued')
                    self.wait_for(lambda: bridge.AZURE_ADMISSION.snapshot()['waiting'] == 1)
                    self.assertEqual(set(calls), {'stream0', 'stream1'})
                    self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['active'], 2)
                    release[0].set()
                    self.assertTrue(closing[0].wait(2))
                    self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['waiting'], 1)
                    self.assertEqual(set(calls), {'stream0', 'stream1'})
                    allow_close[0].set()
                    self.assertEqual(third.result(timeout=2)[0], 200)
                    self.assertFalse(second.done())
                    self.assertEqual(calls[-1], 'queued')
                    release[1].set()
                    allow_close[1].set()
                    self.assertTrue(all(future.result(timeout=2)[0] == 200 for future in (first, second)))
                finally:
                    for event in release + allow_close: event.set()
            self.assertEqual(opener.return_value.open.call_count, 3)
        self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['active'], 0)
        self.assertEqual(self.runtime.status()['uncertain_turns'], 0)

    def test_terminal_disconnect_during_upstream_close_allows_same_turn_continuation(self):
        closing = threading.Event()
        release = threading.Event()
        original_budget = bridge._transport_module.RequestBudget
        def dispatched_budget(*args, **kwargs):
            budget = original_budget(*args, **kwargs)
            budget.mark_model_bytes_possible()
            return budget

        class Stream:
            status = 200
            headers = {'Content-Type': 'text/event-stream'}
            def __enter__(self): return self
            def __exit__(self, *_):
                closing.set()
                if not release.wait(2):
                    raise TimeoutError('Client did not close')
            def __iter__(self):
                yield b'data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}}\n'
                yield b'\n'

        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            budget_patch = patch.object(bridge._transport_module, 'RequestBudget', side_effect=dispatched_budget)
            budget_patch.start()
            self.addCleanup(budget_patch.stop)
            opener.return_value.open.return_value = Stream()
            client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
            client.request('POST', '/harbor/v1/responses', json.dumps(self.source('compact-close', True)),
                           {'Authorization': 'Bearer synthetic-owner-token'})
            held_socket = client.sock.dup()
            response = client.getresponse()
            try:
                self.assertIn(b'response.completed', response.readline())
                while response.readline().strip():
                    pass
                self.assertTrue(closing.wait(1))
                held_socket.shutdown(socket.SHUT_RDWR)
                held_socket.close()
                response.close()
                client.close()
            finally:
                release.set()
            self.wait_for(lambda: self.runtime.status()['active_requests'] == 0)
            self.assertEqual(self.runtime.status()['uncertain_turns'], 0)
            opener.return_value.open.return_value = self.completed()
            self.assertEqual(self.inference('compact-close')[0], 200)
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_queue_overflow_and_timeout_do_not_dispatch_or_poison_turn(self):
        for max_waiting in (0, 1):
            with self.subTest(max_waiting=max_waiting):
                queue = bridge._admission_module.AdmissionQueue(active_limit=1, max_waiting=max_waiting, wait_seconds=.04)
                with patch.object(bridge, 'AZURE_ADMISSION', queue), \
                     patch.object(bridge.urllib.request, 'build_opener') as opener:
                    held = queue.acquire((self.endpoint, 'coding-prod'))
                    try:
                        status, body = self.inference('blocked-' + str(max_waiting))
                        self.assertEqual(status, 503)
                        self.assertIn(b'Azure', body)
                        opener.return_value.open.assert_not_called()
                        self.wait_for(lambda: self.runtime.status()['active_requests'] == 0)
                        self.assertEqual(self.runtime.status()['uncertain_turns'], 0)
                    finally:
                        held.release()
                    opener.return_value.open.return_value = self.completed()
                    self.assertEqual(self.inference('blocked-' + str(max_waiting))[0], 200)
                    opener.return_value.open.assert_called_once()
                    self.assertEqual(queue.snapshot()['keys'], 0)

    def test_queued_disconnect_does_not_dispatch_and_same_turn_can_resume(self):
        queue = bridge._admission_module.AdmissionQueue(active_limit=1)
        with patch.object(bridge, 'AZURE_ADMISSION', queue), \
             patch.object(bridge.urllib.request, 'build_opener') as opener:
            held = queue.acquire((self.endpoint, 'coding-prod'))
            client = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
            try:
                body = json.dumps(self.source('cancelled')).encode()
                client.sendall(b'POST /harbor/v1/responses HTTP/1.1\r\nHost: localhost\r\n'
                               b'Authorization: Bearer synthetic-owner-token\r\nContent-Type: application/json\r\n'
                               + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
                self.wait_for(lambda: queue.snapshot()['waiting'] == 1)
                client.shutdown(socket.SHUT_RDWR)
                client.close()
                self.wait_for(lambda: self.runtime.status()['active_requests'] == 0)
                self.assertEqual(queue.snapshot()['waiting'], 0)
                self.assertEqual(self.runtime.status()['uncertain_turns'], 0)
                opener.return_value.open.assert_not_called()
            finally:
                client.close()
                held.release()
            opener.return_value.open.return_value = self.completed()
            self.assertEqual(self.inference('cancelled')[0], 200)
            opener.return_value.open.assert_called_once()

    def test_busy_verification_uses_same_queue_and_preserves_provider_proof(self):
        queue = bridge._admission_module.AdmissionQueue(active_limit=1, max_waiting=0)
        route = {'provider': 'azure', 'model': 'coding-prod'}
        bridge.record_readiness(route, bridge.configuration_revision(), 'verified')
        self.assertTrue(bridge.provider_status()['azure_ready'])
        with patch.object(bridge, 'AZURE_ADMISSION', queue), \
             patch.object(bridge.urllib.request, 'build_opener') as opener:
            held = queue.acquire((self.endpoint, 'coding-prod'))
            try:
                status, body = self.request(path='/harbor/verify', body={'model': 'harbor/azure/coding-prod'})
                self.assertEqual(status, 503)
                self.assertEqual(body['result'], 'busy')
                opener.return_value.open.assert_not_called()
                self.assertTrue(bridge.provider_status()['azure_ready'])
            finally:
                held.release()
            opener.return_value.open.return_value = self.completed()
            status, body = self.request(path='/harbor/verify', body={'model': 'harbor/azure/coding-prod'})
            self.assertEqual((status, body['verified']), (200, True))
            self.assertEqual(queue.snapshot()['keys'], 0)

    def test_reset_while_verification_queued_preserves_existing_proof(self):
        queue = bridge._admission_module.AdmissionQueue(active_limit=1)
        route = {'provider': 'azure', 'model': 'coding-prod'}
        bridge.record_readiness(route, bridge.configuration_revision(), 'verified')
        with patch.object(bridge, 'AZURE_ADMISSION', queue), \
             patch.object(bridge.urllib.request, 'build_opener') as opener:
            held = queue.acquire((self.endpoint, 'coding-prod'))
            client = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
            try:
                body = json.dumps({'model': 'harbor/azure/coding-prod'}).encode()
                client.sendall(b'POST /harbor/verify HTTP/1.1\r\nHost: localhost\r\n'
                               b'Authorization: Bearer synthetic-owner-token\r\nContent-Type: application/json\r\n'
                               + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)
                self.wait_for(lambda: queue.snapshot()['waiting'] == 1)
                client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
                client.close()
                self.wait_for(lambda: queue.snapshot()['waiting'] == 0)
                self.assertTrue(bridge.provider_status()['azure_ready'])
                opener.return_value.open.assert_not_called()
            finally:
                client.close()
                held.release()

    def test_upstream_error_releases_slot_and_preserves_original_status(self):
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            error = bridge.urllib.error.HTTPError(self.endpoint + '/responses', 429, 'Limit',
                                                  {'Retry-After': '3'}, io.BytesIO(b'{"error":"fixture limit"}'))
            opener.return_value.open.side_effect = [error, self.completed()]
            self.assertEqual(self.inference('error')[0], 429)
            self.wait_for(lambda: self.runtime.status()['active_requests'] == 0)
            self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['keys'], 0)
            self.assertEqual(self.inference('after-error')[0], 200)
            self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertEqual(self.runtime.status()['uncertain_turns'], 0)


if __name__ == '__main__': unittest.main()
