"""Append-only, content-free usage ledger.

One JSON object per line. Fields are counts, statuses, IDs, and timings only:
never prompts, tool arguments, response text, paths, thread titles, or
credentials. Telemetry must never break a request: every write failure is
swallowed.
"""
import json
import os
import threading
import time

SCHEMA = 1
MAX_BYTES = 32 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
ALLOWED_USAGE = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')

_lock = threading.Lock()
_path = None
_disabled = False


def configure(path):
    global _path, _disabled
    with _lock:
        _path = path
        _disabled = False


def path():
    return _path


def disable():
    global _disabled
    with _lock:
        _disabled = True


def _rotate(target):
    previous = target.with_name(target.name + '.1')
    try:
        os.replace(target, previous)
    except FileNotFoundError:
        pass


def _usage_fields(usage):
    if not isinstance(usage, dict):
        return {}
    out = {}
    for key in ALLOWED_USAGE:
        value = usage.get(key)
        if type(value) is int and value >= 0:
            out[key] = value
    details = usage.get('input_tokens_details')
    nested = details.get('cached_tokens') if isinstance(details, dict) else None
    if 'cached_input_tokens' not in out and type(nested) is int and nested >= 0:
        out['cached_input_tokens'] = nested
    return out


def record(event):
    """Append one event. Returns True on success; never raises."""
    try:
        if not isinstance(event, dict):
            return False
        entry = {'schema': SCHEMA, 'at': event.get('at') or time.time()}
        for key in ('provider', 'model', 'status', 'failure_class', 'stream'):
            value = event.get(key)
            if value is None:
                continue
            if key == 'stream':
                entry[key] = bool(value)
                continue
            if not isinstance(value, str) or len(value) > 256:
                return False
            entry[key] = value
        if entry.get('provider') is None or entry.get('model') is None or entry.get('status') is None:
            return False
        http_status = event.get('http_status')
        if type(http_status) is int:
            entry['http_status'] = http_status
        for key in ('duration_seconds', 'ttft_seconds'):
            value = event.get(key)
            if type(value) in (int, float) and value >= 0:
                entry[key] = round(float(value), 3)
        usage = _usage_fields(event.get('usage'))
        if usage:
            entry['usage'] = usage
            entry['usage_source'] = 'estimated' if event.get('estimated') else 'provider'
        elif event.get('estimated') and isinstance(event.get('estimated'), int):
            entry['usage'] = {'input_tokens': int(event['estimated']), 'total_tokens': int(event['estimated'])}
            entry['usage_source'] = 'estimated'
        entry['retried'] = False
        rollup = event.get('rollup')
        if isinstance(rollup, dict):
            entry['rollup'] = rollup
        line = json.dumps(entry, separators=(',', ':')).encode()
        if len(line) > MAX_LINE_BYTES:
            return False
        with _lock:
            if _disabled or _path is None:
                return False
            target = _path
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(target.parent, 0o700)
                if target.exists() and target.stat().st_size + len(line) > MAX_BYTES:
                    _rotate(target)
                with open(target, 'ab') as handle:
                    handle.write(line + b'\n')
                os.chmod(target, 0o600)
            except OSError:
                return False
        return True
    except Exception:
        return False


def read(path, *, since=None, until=None, limit=10000):
    """Read events newest-last, windowed before the limit so old days are
    never silently truncated. Malformed lines are skipped."""
    events = []
    try:
        with open(path, 'rb') as handle:
            for raw in handle:
                if len(raw) > MAX_LINE_BYTES:
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get('schema') != SCHEMA:
                    continue
                at = event.get('at')
                if type(at) not in (int, float):
                    continue
                if since is not None and at < since:
                    continue
                if until is not None and at > until:
                    continue
                events.append(event)
    except OSError:
        return []
    if len(events) > limit:
        events = events[-limit:]
    return events
