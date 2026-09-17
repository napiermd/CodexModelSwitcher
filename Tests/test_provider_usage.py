import concurrent.futures
import datetime as dt
import importlib.util
import io
import json
import pathlib
import sys
import threading
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

support = pathlib.Path(__file__).parents[1] / 'ModelHarbor/Support'
sys.path.insert(0, str(support))
import provider_usage as usage
spec = importlib.util.spec_from_file_location('usage_adapter', support / 'grok_adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class UsageTests(unittest.TestCase):
    now = dt.datetime(2026, 9, 16, 12, tzinfo=dt.timezone.utc)

    def buckets(self):
        first = self.now.date() - dt.timedelta(days=29)
        return [{'date': (first + dt.timedelta(days=i)).isoformat(), 'results': [{'subtotal': '0.0001'}, {'subtotal': '1.4999'}]} for i in range(30)]

    def fetch(self, url, headers):
        self.assertEqual(headers['Authorization'], 'Bearer test-key')
        if '/billing/' in url:
            return {'items': self.buckets(), 'pagination': {'has_more': False}}
        return {'items': [{'start_time': b['date'] + 'T00:00:00Z', 'results': [{'input_tokens': 100, 'output_tokens': 10, 'cached_input_tokens': 90}]} for b in self.buckets()], 'pagination': {'has_more': False}}

    def test_baseten_reports_complete_organization_costs_and_counts_cached_tokens_once(self):
        result = usage.baseten('test-key', fetch=self.fetch, now=self.now)
        self.assertEqual(result['costToday'], 1.5)
        self.assertEqual(result['cost30Days'], 45)
        self.assertEqual(result['tokens30Days'], 3300)
        self.assertEqual(result['tokensToday'], 110)
        self.assertEqual(len(result['daily']), 30)
        self.assertEqual(result['scope'], 'All Model API keys in this organization · UTC days')
        self.assertEqual(result['costKind'], 'reported')

    def test_baseten_keeps_billing_when_token_permission_is_denied(self):
        def fetch(url, headers):
            if '/usage?' in url:
                raise urllib.error.HTTPError(url, 403, '', {}, io.BytesIO(b'private details'))
            return self.fetch(url, headers)
        result = usage.baseten('test-key', fetch=fetch, now=self.now)
        self.assertEqual(result['cost30Days'], 45)
        self.assertNotIn('tokens30Days', result)
        self.assertIn('Token totals are unavailable', result['note'])

    def test_baseten_follows_cursor_and_rejects_missing_or_duplicate_days(self):
        for corrupted in [self.buckets()[:-1], self.buckets() + [self.buckets()[0]]]:
            with self.assertRaises(usage.UsageError):
                usage.baseten('key', now=self.now, fetch=lambda *_: {'items': corrupted, 'pagination': {'has_more': False}})
        calls = []
        def fetch(url, headers):
            calls.append(url)
            return {'items': [1] if len(calls) == 1 else [2], 'pagination': {'has_more': len(calls) == 1, 'cursor': 'opaque ?&=='}}
        self.assertEqual(usage.pages('https://example.com', {'limit': 31}, {}, fetch), [1, 2])
        self.assertIn('cursor=opaque+%3F%26%3D%3D', calls[1])
        with self.assertRaises(usage.UsageError):
            usage.pages('https://example.com', {}, {}, lambda *_: {'items': [], 'pagination': {'has_more': True, 'cursor': 'same'}})

    def test_empty_baseten_buckets_are_zero_and_invalid_money_is_not(self):
        buckets = self.buckets()
        for bucket in buckets:
            bucket['results'] = []
        def fetch(*_): return {'items': buckets, 'pagination': {'has_more': False}}
        result = usage.baseten('key', fetch=fetch, now=self.now)
        self.assertEqual(result['cost30Days'], 0)
        buckets[0]['results'] = [{'subtotal': 'NaN'}]
        with self.assertRaises(usage.UsageError): usage.baseten('key', fetch=fetch, now=self.now)

    def test_grok_parses_fractional_quota_and_does_not_invent_spend(self):
        result = usage.grok('key', 'Account', fetch=lambda *_: {'config': {'creditUsagePercent': 7.25, 'currentPeriod': {'start': '2026-09-10T00:00:00Z', 'end': '2026-09-17T00:00:00Z'}}})
        self.assertEqual(result['windows'][0], {'id': 'plan', 'title': 'Weekly', 'remainingPercent': 92.75, 'resetsAt': '2026-09-17T00:00:00Z'})
        self.assertNotIn('costToday', result)
        unknown = usage.grok('key', 'Account', fetch=lambda *_: {'config': {'billingPeriodEnd': '2026-09-17T00:00:00Z'}})
        self.assertNotIn('remainingPercent', unknown['windows'][0])

    def test_openrouter_calendar_month_is_not_rolling_thirty_days(self):
        result = usage.openrouter('key', fetch=lambda *_: {'data': {'usage': 25, 'usage_daily': 0, 'usage_monthly': 19, 'limit': 100, 'limit_remaining': 75}})
        self.assertEqual(result['costToday'], 0)
        self.assertEqual(result['costMonth'], 19)
        self.assertEqual(result['balance'], 75)
        self.assertNotIn('cost30Days', result)
        self.assertEqual(result['windows'][0]['remainingPercent'], 75)

    def test_collector_coalesces_reads_and_preserves_stale_data_on_failure(self):
        clock = [1000]
        collector = usage.UsageCollector(lambda p: ('key', 'Account', ''), clock=lambda: clock[0])
        sample = {'id': 'baseten', 'costToday': 9}
        with patch.object(usage, 'baseten', return_value=sample.copy()) as fetch, patch.object(usage, 'grok', return_value={}), patch.object(usage, 'openrouter', return_value={}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: collector.read(), range(4)))
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(results[0]['entries'][0]['updatedAt'], 1000)
        clock[0] += 301
        with patch.object(usage, 'baseten', side_effect=urllib.error.HTTPError('x', 429, '', {'Retry-After': '900'}, io.BytesIO(b'secret'))):
            stale = collector.refresh('baseten')
        self.assertEqual(stale['costToday'], 9)
        self.assertEqual(stale['updatedAt'], 1000)
        self.assertIn('429', stale['error'])
        self.assertNotIn('secret', json.dumps(stale))
        self.assertEqual(collector.next_refresh['baseten'], clock[0] + 900)

    def test_changing_key_cannot_relabel_old_spend(self):
        key = ['first']
        collector = usage.UsageCollector(lambda _: (key[0], 'Account', ''), clock=lambda: 1000)
        with patch.object(usage, 'baseten', return_value={'costToday': 99}): collector.refresh('baseten')
        key[0] = 'second'
        with patch.object(usage, 'baseten', side_effect=ValueError('private')): result = collector.refresh('baseten')
        self.assertNotIn('costToday', result)
        self.assertNotIn('private', json.dumps(result))

    def test_usage_credentials_never_execute_helper_or_refresh_oauth(self):
        credentials = adapter.BasetenCredentials()
        with patch.object(adapter, 'BASETEN_CREDENTIALS', credentials), patch.object(adapter, 'baseten_headers', side_effect=AssertionError('helper')), patch.object(adapter.subprocess, 'run', side_effect=AssertionError('process')):
            key, _, _ = adapter.usage_credentials('baseten')
            self.assertEqual(key, '')
            credentials.key = 'already-unlocked'
            self.assertEqual(adapter.usage_credentials('baseten')[0], 'already-unlocked')
            self.assertEqual(credentials.helper_reads, 0)
            with patch.object(adapter, 'session', return_value={'key': 'expired', 'expires_at': '2020-01-01T00:00:00Z'}):
                self.assertEqual(adapter.usage_credentials('grok-oauth')[0], '')

    def test_usage_endpoint_requires_local_token_rejects_browser_origins_and_disables_http_cache(self):
        import http.client
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            token = pathlib.Path(temporary) / 'token'
            token.write_text('test-local-token')
            collector = Mock()
            collector.read.return_value = {'entries': []}
            with patch.object(adapter, 'TOKEN_PATH', token), patch.object(adapter, 'USAGE_COLLECTOR', collector):
                server = adapter.http.server.ThreadingHTTPServer(('127.0.0.1', 0), adapter.Handler)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    def request(headers):
                        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                        connection.request('GET', '/harbor/usage', headers=headers)
                        response = connection.getresponse()
                        result = (response.status, response.getheader('Cache-Control'), response.read())
                        connection.close()
                        return result
                    self.assertEqual(request({})[0], 401)
                    auth = {'X-Model-Harbor-Token': 'test-local-token'}
                    self.assertEqual(request(dict(auth, Origin='https://example.com'))[0], 401)
                    collector.read.assert_not_called()
                    status, cache, body = request(auth)
                    self.assertEqual((status, cache), (200, 'no-store'))
                    self.assertEqual(json.loads(body), {'entries': []})
                    collector.read.assert_called_once()
                finally:
                    server.shutdown()
                    server.server_close()
                    worker.join()

    def test_nonfinite_and_bool_are_unknown(self):
        for value in (None, True, False, 'NaN', 'inf', -2): self.assertIsNone(usage.number(value))
        self.assertEqual(usage.number(0), 0)

if __name__ == '__main__': unittest.main()
