"""Read-only provider usage. No credential helper, token refresh, or inference calls.

Grok's billing response shape follows CodexBar (MIT); see NOTICE.md.
"""
import concurrent.futures
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class UsageError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, headers):
    request = urllib.request.Request(url, headers={**headers, 'Accept': 'application/json', 'User-Agent': 'ModelHarbor/1.0'})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise UsageError('Usage response was too large. Open the provider dashboard.')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise UsageError('The provider returned an unrecognized usage response.')
        return result


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def pages(url, params, headers, fetch):
    items, cursors = [], set()
    for _ in range(12):
        result = fetch(url + '?' + urllib.parse.urlencode(params, doseq=True), headers)
        if not isinstance(result.get('items'), list) or not isinstance(result.get('pagination'), dict):
            raise UsageError('The provider returned an unrecognized usage response.')
        items.extend(result['items'])
        page = result['pagination']
        if page.get('has_more') is False:
            return items
        cursor = page.get('cursor')
        if page.get('has_more') is not True or not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise UsageError('Usage pagination was incomplete. No partial total is shown.')
        cursors.add(cursor)
        params = {**params, 'cursor': cursor}
    raise UsageError('Usage pagination was incomplete. No partial total is shown.')


def baseten(key, fetch=request_json, now=None):
    today = (now or dt.datetime.now(dt.timezone.utc)).date()
    start = max(today - dt.timedelta(days=29), dt.date(2026, 8, 5))
    end = today + dt.timedelta(days=1)
    headers = {'Authorization': 'Bearer ' + key}
    costs = pages('https://api.baseten.co/v1/billing/model_apis',
                  {'start_date': start.isoformat(), 'end_date': end.isoformat(), 'limit': 31}, headers, fetch)
    daily = {}
    try:
        for bucket in costs:
            day = dt.date.fromisoformat(bucket['date'])
            if not start <= day < end or day.isoformat() in daily:
                raise ValueError()
            total = sum((Decimal(row['subtotal']) for row in bucket.get('results', [])), Decimal(0))
            if not total.is_finite() or total < 0:
                raise ValueError()
            daily[day.isoformat()] = {'date': day.isoformat(), 'costUSD': float(total)}
    except (KeyError, TypeError, InvalidOperation, ValueError):
        raise UsageError('Baseten returned an unrecognized cost bucket. No partial total is shown.') from None
    if len(daily) != (end - start).days:
        raise UsageError('Baseten cost history is incomplete. No partial total is shown.')
    result = {'id': 'baseten', 'providerID': 'baseten', 'accountLabel': 'Organization',
              'source': 'Baseten billing API', 'scope': 'All Model API keys in this organization · UTC days',
              'note': 'Provider-reported costs may differ from the final invoice. Today is a partial day.',
              'daily': sorted(daily.values(), key=lambda x: x['date']), 'windows': [],
              'costKind': 'reported', 'costToday': daily[today.isoformat()]['costUSD'],
              'cost30Days': float(sum(Decimal(str(x['costUSD'])) for x in daily.values()))}
    if (end - start).days < 30:
        result['note'] += ' Available history begins August 5, 2026.'
    try:
        usage = pages('https://api.baseten.co/v1/model_apis/usage',
                      {'start_time': start.isoformat() + 'T00:00:00Z', 'end_time': end.isoformat() + 'T00:00:00Z',
                       'bucket_width': '1d', 'limit': 31}, headers, fetch)
        token_days = {}
        for bucket in usage:
            day = str(bucket['start_time'])[:10]
            if day not in daily or day in token_days:
                raise ValueError()
            counts = []
            for row in bucket.get('results', []):
                pair = [number(row.get('input_tokens')), number(row.get('output_tokens'))]
                if None in pair:
                    raise ValueError()
                counts.append(sum(pair))
            token_days[day] = int(sum(counts))
        if len(token_days) != len(daily):
            raise ValueError()
        for day, tokens in token_days.items():
            daily[day]['tokens'] = tokens
        result['tokens30Days'] = sum(token_days.values())
        result['tokensToday'] = token_days[today.isoformat()]
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        result['note'] += ' Token totals are unavailable; the billing totals above are complete.'
    return result


def grok(key, label, fetch=request_json):
    raw = fetch('https://cli-chat-proxy.grok.com/v1/billing?format=credits',
                {'Authorization': 'Bearer ' + key, 'x-xai-token-auth': 'xai-grok-cli'})
    config = raw.get('config')
    if not isinstance(config, dict):
        raise UsageError('Grok returned an unrecognized usage response.')
    used = number(config.get('creditUsagePercent'))
    period = config.get('currentPeriod') or {}
    reset = period.get('end') or config.get('billingPeriodEnd')
    title = 'Plan usage'
    try:
        start = dt.datetime.fromisoformat((period.get('start') if period.get('end') else config.get('billingPeriodStart')).replace('Z', '+00:00'))
        end = dt.datetime.fromisoformat(reset.replace('Z', '+00:00'))
        days = (end - start).total_seconds() / 86400
        title = 'Weekly' if 6 <= days <= 8 else 'Monthly' if 27 <= days <= 32 else 'Plan usage'
    except (ValueError, TypeError, AttributeError):
        pass
    if used is None and not reset:
        raise UsageError('Grok did not report quota or reset information for this account.')
    window = {'id': 'plan', 'title': title}
    if used is not None:
        window['remainingPercent'] = max(0, 100 - used)
    if isinstance(reset, str):
        window['resetsAt'] = reset
    return {'id': 'grok-oauth', 'providerID': 'grok-oauth', 'accountLabel': label,
            'plan': config.get('subscriptionTier') or raw.get('subscriptionTier'),
            'source': 'Grok account billing', 'scope': 'Signed-in Grok account', 'windows': [window], 'daily': [],
            'note': 'Subscription quota. Grok does not report a dollar bill in this response.'}


def openrouter(key, fetch=request_json):
    raw = fetch('https://openrouter.ai/api/v1/key', {'Authorization': 'Bearer ' + key}).get('data')
    if not isinstance(raw, dict) or not any(number(raw.get(k)) is not None for k in ('usage', 'usage_daily', 'usage_monthly')):
        raise UsageError('OpenRouter did not report spend for this key.')
    result = {'id': 'openrouter', 'providerID': 'openrouter', 'accountLabel': (raw.get('label') if isinstance(raw.get('label'), str) and not raw['label'].startswith('sk-') else 'Connected API key'),
              'source': 'OpenRouter key API', 'scope': 'This API key · provider billing periods',
              'note': 'Provider-reported usage for this key. Calendar-month spend is separate from rolling 30-day history.',
              'costKind': 'reported', 'windows': [], 'daily': []}
    for source, target in [('usage_daily', 'costToday'), ('usage_monthly', 'costMonth'), ('usage', 'costAllTime'), ('limit_remaining', 'balance')]:
        if number(raw.get(source)) is not None:
            result[target] = number(raw[source])
    limit, remaining = number(raw.get('limit')), number(raw.get('limit_remaining'))
    if limit and remaining is not None:
        result['windows'] = [{'id': 'key-limit', 'title': 'Key spending limit', 'remainingPercent': min(100, remaining / limit * 100)}]
    return result


class UsageCollector:
    """Coalesces refreshes and retains explicitly stale data after transient failures."""
    def __init__(self, credentials, clock=time.time):
        self.credentials, self.clock = credentials, clock
        self.lock = threading.Lock()
        self.cache, self.identities, self.next_refresh = {}, {}, {}

    def read(self):
        with self.lock:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                entries = list(pool.map(self.refresh, ('baseten', 'grok-oauth', 'openrouter')))
            return {'entries': entries}

    def refresh(self, provider):
        key, label, reason = self.credentials(provider)
        identity = hashlib.sha256((key + '\0' + label).encode()).hexdigest()
        now = self.clock()
        if identity != self.identities.get(provider):
            self.cache.pop(provider, None)
            self.next_refresh.pop(provider, None)
            self.identities[provider] = identity
        if now < self.next_refresh.get(provider, 0):
            return copy.deepcopy(self.cache[provider])
        entry = {'id': provider, 'providerID': provider, 'accountLabel': label,
                 'source': 'Provider account API', 'scope': '', 'windows': [], 'daily': []}
        if not key:
            entry['error'] = reason or 'Connect this provider to read usage.'
        else:
            delay = 60
            try:
                entry = baseten(key) if provider == 'baseten' else grok(key, label) if provider == 'grok-oauth' else openrouter(key)
                entry['updatedAt'] = now
            except Exception as error:
                delay = 300
                entry = copy.deepcopy(self.cache.get(provider, entry))
                if isinstance(error, urllib.error.HTTPError):
                    code = error.code
                    message = ('This credential cannot read usage. Check account access in the provider dashboard.' if code in (401, 403)
                               else f'The usage service returned HTTP {code}. Harbor will retry later.')
                    if code == 429:
                        delay = min(3600, max(300, number(error.headers.get('Retry-After')) or 300))
                    error.close()
                elif isinstance(error, UsageError):
                    message = str(error)
                else:
                    message = 'Usage could not be refreshed. Harbor will retry later.'
                entry['error'] = message
            self.next_refresh[provider] = now + delay
        self.cache[provider] = entry
        return copy.deepcopy(entry)
