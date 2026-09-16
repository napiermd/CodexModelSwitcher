import concurrent.futures
import email.message
import http.client
import importlib.util
import io
import json
import pathlib
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
from contextlib import ExitStack
from unittest.mock import patch

path = pathlib.Path(__file__).parents[1] / 'ModelHarbor/Support/grok_adapter.py'
spec = importlib.util.spec_from_file_location('paced_adapter', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def headers(**values):
    result = email.message.Message()
    for key, value in values.items():
        result[key.replace('_', '-')] = str(value)
    return result


def rejected(code, **values):
    return urllib.error.HTTPError('https://inference.baseten.co/v1/responses', code, 'Rejected',
                                  headers(**values), io.BytesIO(json.dumps({'error': {'message': 'busy', 'code': code}}).encode()))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Response(io.BytesIO):
    status = 200

    def __init__(self, body=b'{}', **values):
        super().__init__(body)
        self.headers = headers(**values)


class PacingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.pacer = module.BasetenPacer(clock=self.clock, jitter=lambda a, b: 1)
        self.wait = patch.object(self.pacer.condition, 'wait', side_effect=self.clock.advance)
        self.wait.start()
        self.addCleanup(self.wait.stop)
        self.opener = unittest.mock.Mock()

    @staticmethod
    def raise_overload():
        raise rejected(529)

    def reserve(self, tokens):
        self.pacer.reserve(tokens, self.clock() + 120, lambda: False, lambda _: None)

    def open(self, budget=120, cancelled=lambda: False):
        return module.open_baseten(self.opener, object(), self.pacer, 1000,
                                   self.clock() + budget, cancelled, lambda _: None)

    def test_tool_continuations_count_cached_input_and_actual_output(self):
        self.reserve(300000)
        self.pacer.finish({'input_tokens': 190000, 'output_tokens': 5000,
                           'input_tokens_details': {'cached_tokens': 189000}})
        self.reserve(300000)
        self.assertAlmostEqual(self.clock(), 1029.25)

    def test_provider_limits_and_remaining_capacity_control_next_request(self):
        self.pacer.observe(headers(x_ratelimit_limit_tokens=1000000, x_ratelimit_remaining_tokens=0,
                                   x_ratelimit_limit_requests=2, x_ratelimit_remaining_requests=0), 200)
        self.reserve(100000)
        self.assertAlmostEqual(self.clock(), 1030)
        self.assertEqual(self.pacer.snapshot()['tokens_per_minute'], 1000000)

    def test_lower_provider_limit_reprices_reservation_when_usage_is_missing(self):
        self.reserve(50000)
        self.pacer.observe(headers(x_ratelimit_limit_tokens=100000), 200)
        self.reserve(50000)
        self.assertEqual(self.clock(), 1037.5)

    def test_overload_backoff_then_success(self):
        calls = []
        results = iter([rejected(529), rejected(529), rejected(529), Response()])
        def upstream(*args, **kwargs):
            calls.append(self.clock())
            result = next(results)
            if isinstance(result, Exception):
                raise result
            return result
        self.opener.open.side_effect = upstream
        self.open().close()
        self.assertEqual(calls, [1000, 1010, 1030, 1070])
        self.assertEqual(self.pacer.snapshot()['retries'], 3)

    def test_cooldown_survives_codex_retry_as_new_request(self):
        self.opener.open.side_effect = lambda *a, **k: self.raise_overload()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.open()
        self.assertEqual(json.load(failure.exception), {'error': {'message': 'busy', 'code': 529}})
        failure.exception.close()
        self.assertEqual(self.opener.open.call_count, 4)
        self.opener.open.side_effect = None
        self.opener.open.return_value = Response()
        self.open().close()
        self.assertEqual(self.clock(), 1130)
        self.assertEqual(self.opener.open.call_count, 5)

    def test_retry_after_is_a_minimum_even_beyond_request_wait_budget(self):
        self.opener.open.side_effect = rejected(429, Retry_After=300)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.open()
        failure.exception.close()
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(self.clock(), 1120)
        self.assertEqual(self.pacer.snapshot()['cooldown_seconds'], 180)

    def test_retry_after_supports_dates_and_rejects_invalid_numbers(self):
        self.assertEqual(module.retry_after_seconds(headers(Retry_After='Wed, 16 Sep 2026 12:00:00 GMT'),
                                                   now=1789559940), 60)
        for value in ('NaN', 'inf', '-1', 'nonsense'):
            self.assertEqual(module.retry_after_seconds(headers(Retry_After=value)), 0)

    def test_auth_schema_and_network_failures_are_not_replayed(self):
        for failure in (rejected(401), rejected(400), rejected(500), socket.timeout(),
                        urllib.error.URLError('connection lost')):
            with self.subTest(failure=type(failure).__name__):
                self.opener.reset_mock()
                self.opener.open.side_effect = failure
                with self.assertRaises(type(failure)) as caught:
                    self.open()
                if isinstance(caught.exception, urllib.error.HTTPError):
                    caught.exception.close()
                self.assertEqual(self.opener.open.call_count, 1)

    def test_disconnected_waiter_never_sends_another_request(self):
        self.opener.open.side_effect = rejected(529)
        with self.assertRaises(BrokenPipeError):
            self.open(cancelled=lambda: self.opener.open.call_count > 0)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_estimate_includes_tool_definitions_and_output_allowance(self):
        base = module.estimated_tokens({'input': 'hello'})
        tools = module.estimated_tokens({'input': 'hello', 'tools': [{'description': 'a' * 3000}]})
        self.assertGreaterEqual(tools - base, 1000)
        self.assertEqual(module.estimated_tokens({'input': 'hello', 'max_output_tokens': 8192}) - base, 4096)

    def test_base64_is_not_counted_as_prompt_text(self):
        def request(size):
            return {'input': [{'role': 'user', 'content': [{'type': 'input_image',
                    'image_url': 'data:image/png;base64,' + 'A' * size}]}]}
        self.assertEqual(module.estimated_tokens(request(100)), module.estimated_tokens(request(2_000_000)))
        self.assertGreater(module.estimated_tokens(request(100)), 32768)

    def test_rate_limit_without_headers_waits_a_full_refill_window(self):
        self.opener.open.side_effect = [rejected(429), Response()]
        self.open().close()
        self.assertEqual(self.clock(), 1060)

    def test_recovery_can_outlast_the_old_four_attempt_ceiling(self):
        self.opener.open.side_effect = [rejected(529) for _ in range(5)] + [Response()]
        self.open(budget=600).close()
        self.assertEqual(self.opener.open.call_count, 6)
        self.assertEqual(self.clock(), 1190)

    def test_limit_cooldown_and_debt_survive_a_bridge_restart(self):
        with tempfile.TemporaryDirectory() as root, patch.object(module, 'CONFIG_DIR', pathlib.Path(root)), patch.object(module.time, 'time', return_value=2000):
            first = module.BasetenPacer(clock=self.clock, model='model/test')
            first.reserve(100000, self.clock() + 600, lambda: False, lambda _: None)
            first.observe(headers(x_ratelimit_limit_tokens=1000000, Retry_After=90), 429)
            fresh = module.BasetenPacer(clock=self.clock, model='model/test')
            self.assertEqual(fresh.tpm, 1000000)
            self.assertEqual(fresh.delay(1000), 90)
            self.assertEqual(first.state_path.stat().st_mode & 0o777, 0o600)
            saved = first.state_path.read_text()
            self.assertNotIn('Authorization', saved)
            self.assertNotIn('input', saved)

    def test_large_schema_estimate_learns_from_usage_without_tiny_probe_bias(self):
        self.reserve(450000)
        self.pacer.finish({'input_tokens': 150000, 'output_tokens': 100})
        self.assertEqual(self.pacer.estimation_multiplier, 0.8)
        self.clock.advance(60)
        self.reserve(450000 * self.pacer.estimation_multiplier)
        self.pacer.finish({'input_tokens': 150000, 'output_tokens': 100})
        self.assertAlmostEqual(self.pacer.estimation_multiplier, 0.64)
        before = self.pacer.estimation_multiplier
        self.clock.advance(60)
        self.reserve(4096)
        self.pacer.finish({'input_tokens': 89, 'output_tokens': 15})
        self.assertEqual(self.pacer.estimation_multiplier, before)

    def test_models_have_independent_lanes(self):
        module.BASETEN_PACERS.clear()
        kimi = module.baseten_pacer('kimi')
        kimi.cooldown = time.monotonic() + 60
        deepseek = module.baseten_pacer('deepseek')
        self.assertEqual(deepseek.delay(1000), 0)
        self.assertIs(module.baseten_pacer('kimi'), kimi)


class QueueTests(unittest.TestCase):
    def test_same_model_waits_until_stream_finishes_and_cancel_removes_waiter(self):
        pacer = module.BasetenPacer()
        deadline = time.monotonic() + 5
        pacer.enter(deadline, lambda: False, lambda _: None)
        waiting = threading.Event()
        cancelled = threading.Event()
        def second():
            pacer.enter(deadline, cancelled.is_set, lambda _: waiting.set())
            pacer.leave()
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(second)
            self.assertTrue(waiting.wait(2))
            self.assertEqual(pacer.snapshot()['queued'], 1)
            self.assertFalse(future.done())
            cancelled.set()
            with pacer.condition:
                pacer.condition.notify_all()
            with self.assertRaises(BrokenPipeError):
                future.result(timeout=2)
        self.assertEqual(pacer.snapshot()['queued'], 0)
        pacer.leave()
        pacer.enter(deadline, lambda: False, lambda _: None)
        pacer.leave()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = pathlib.Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (root / 'token').write_text('local-test-token')
        (root / 'model-switcher.json').write_text(json.dumps({'services': [
            {'id': 'baseten', 'models': [{'id': 'test-kimi'}]}]}))
        (root / 'config.toml').write_text('[model_providers.baseten]\nbase_url="https://inference.baseten.co/v1"\n'
                                         '[model_providers.baseten.auth]\ncommand="/fake/op"\nargs=["read","test"]\n')
        self.stack.enter_context(patch.object(module, 'CONFIG_DIR', root))
        self.stack.enter_context(patch.object(module, 'TOKEN_PATH', root / 'token'))
        self.stack.enter_context(patch.object(module, 'BASETEN_CREDENTIALS', module.BasetenCredentials()))
        self.helper = self.stack.enter_context(patch.object(module.subprocess, 'run', return_value=
                         module.subprocess.CompletedProcess([], 0, 'fake-private-key\n')))
        self.clock = Clock()
        self.pacer = module.BasetenPacer(clock=self.clock, jitter=lambda a, b: 1)
        self.stack.enter_context(patch.object(self.pacer.condition, 'wait', side_effect=self.clock.advance))
        self.stack.enter_context(patch.object(module, 'BASETEN_PACERS', {'test-kimi': self.pacer}))
        self.opener = unittest.mock.Mock()
        self.stack.enter_context(patch.object(module.urllib.request, 'build_opener', return_value=self.opener))
        self.server = module.http.server.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def post(self, stream=True):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        connection.request('POST', '/harbor/v1/responses', body=json.dumps({
            'model': 'harbor/baseten/test-kimi', 'stream': stream, 'input': 'test'}),
            headers={'X-Model-Harbor-Token': 'local-test-token', 'Authorization': 'Bearer codex.not.forwarded'})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response, body

    def test_rate_limit_retries_keep_one_unlock_and_forward_success_headers_and_usage(self):
        event = {'type': 'response.completed', 'response': {'status': 'completed', 'output': [],
                 'usage': {'input_tokens': 190000, 'output_tokens': 5000, 'input_tokens_details': {'cached_tokens': 189000}}}}
        data = ('data: ' + json.dumps(event) + '\n\n').encode()
        self.opener.open.side_effect = [rejected(429, Retry_After=45),
            Response(data, Content_Type='text/event-stream', x_ratelimit_limit_tokens=500000,
                     x_ratelimit_remaining_tokens=305000, x_request_id='provider-request')]
        response, body = self.post()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('X-Model-Harbor-Provider'), 'baseten')
        self.assertTrue(body.startswith(b': Model Harbor is waiting'))
        self.assertIn(b'response.completed', body)
        self.assertNotIn(b'fake-private-key', body)
        self.assertEqual(self.clock(), 1060)
        self.assertAlmostEqual(self.pacer.token_due, 1089.25)
        self.assertEqual(self.helper.call_count, 1)
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertFalse(self.pacer.active)

    def test_exhausted_overload_preserves_structured_error_and_retry_headers(self):
        def overload(*args, **kwargs):
            raise rejected(529, x_request_id='overload-id')
        self.opener.open.side_effect = overload
        response, body = self.post(stream=False)
        self.assertEqual(response.status, 529)
        self.assertEqual(json.loads(body), {'error': {'message': 'busy', 'code': 529}})
        self.assertEqual(response.getheader('retry-after'), '60')
        self.assertEqual(response.getheader('x-request-id'), 'overload-id')
        self.assertEqual(self.helper.call_count, 1)
        self.assertEqual(self.opener.open.call_count, 10)
        self.assertEqual(module.LAST_ROUTE['state'], 'failed')
        self.assertFalse(self.pacer.active)

    def test_queued_stream_ends_with_explicit_error_when_recovery_is_exhausted(self):
        self.opener.open.side_effect = lambda *a, **k: (_ for _ in ()).throw(rejected(529))
        response, body = self.post()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Type'), 'text/event-stream')
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith(b'data: ')]
        self.assertEqual(events[-1]['type'], 'response.failed')
        self.assertEqual(events[-1]['response']['error']['message'], 'busy')
        self.assertEqual(self.opener.open.call_count, 10)
        self.assertFalse(self.pacer.active)

    def test_queued_stream_handles_unstructured_provider_error(self):
        failure = urllib.error.HTTPError('https://inference.baseten.co/v1/responses', 400, 'Rejected',
                                        headers(), io.BytesIO(b'["unsupported request"]'))
        self.opener.open.side_effect = [rejected(529), failure]
        response, body = self.post()
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith(b'data: ')]
        self.assertEqual(events[-1]['type'], 'response.failed')
        self.assertIn('unsupported request', events[-1]['response']['error']['message'])
        self.assertTrue(failure.closed)
        self.assertFalse(self.pacer.active)

    def test_local_queue_timeout_is_distinct_from_provider_rate_limit(self):
        self.pacer.cooldown = self.clock() + 700
        response, body = self.post(stream=False)
        self.assertEqual(response.status, 503)
        self.assertIn(b'local queue limit', body)
        self.assertEqual(self.opener.open.call_count, 0)
        self.assertEqual(self.pacer.queue_timeouts, 1)
        self.assertFalse(self.pacer.active)

    def test_partial_stream_failure_is_not_replayed(self):
        class Interrupted(Response):
            def __iter__(self):
                yield b'data: {"type":"response.created"}\n'
                yield b'\n'
                raise ConnectionResetError('stream interrupted')
        self.opener.open.return_value = Interrupted(Content_Type='text/event-stream')
        response, body = self.post()
        self.assertEqual(response.status, 200)
        self.assertIn(b'response.created', body)
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertFalse(self.pacer.active)

    def test_real_client_disconnect_during_backoff_cancels_retry(self):
        self.waiting = threading.Event()
        self.stack.enter_context(patch.object(self.pacer.condition, 'wait',
            side_effect=lambda seconds: self.waiting.wait(0.02)))
        def overload(*args, **kwargs):
            raise rejected(529)
        self.opener.open.side_effect = overload
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        connection.request('POST', '/harbor/v1/responses', body=json.dumps({
            'model': 'harbor/baseten/test-kimi', 'stream': True, 'input': 'cancel'}),
            headers={'X-Model-Harbor-Token': 'local-test-token', 'Authorization': 'Bearer codex.not.forwarded'})
        self.addCleanup(connection.close)
        deadline = time.monotonic() + 2
        while not self.pacer.failures and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.pacer.failures, 1)
        connection.close()
        while self.pacer.active and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.pacer.active)
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(module.LAST_ROUTE['state'], 'disconnected')

    def test_json_response_reconciles_full_usage(self):
        self.opener.open.return_value = Response(json.dumps({'status': 'completed', 'output': [],
            'usage': {'input_tokens': 200000, 'output_tokens': 10000}}).encode(), Content_Type='application/json')
        response, body = self.post(stream=False)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body)['usage']['input_tokens'], 200000)
        self.assertAlmostEqual(self.pacer.token_due, 1031.5)


if __name__ == '__main__':
    unittest.main()
