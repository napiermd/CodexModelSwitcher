#!/usr/bin/env python3
"""Read a Codex JSONL rollout and emit content-free compaction measurements.

Memory is bounded by one JSONL record, the retained cycles, and a 256-ID
replay cache. Token-count snapshots are estimates, not provider requests.
"""
import argparse
from collections import deque
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


_AGING_SPEC = importlib.util.spec_from_file_location(
    'harbor_tool_result_aging',
    Path(__file__).resolve().parents[1] / 'ModelHarbor/Support/tool_result_aging.py')
_AGING = importlib.util.module_from_spec(_AGING_SPEC)
_AGING_SPEC.loader.exec_module(_AGING)


def number(value):
    return value if type(value) is int and value >= 0 else None


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _sha256(value):
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def _read_evidence(source):
    """Return (state, bytes) without exposing an explicit source path."""
    if source is None:
        return 'unknown', None
    try:
        if isinstance(source, dict):
            return 'observed', json.dumps(source, sort_keys=True, ensure_ascii=False,
                                          separators=(',', ':')).encode('utf-8')
        if isinstance(source, bytes):
            return 'observed', source
        if isinstance(source, Path):
            return 'observed', source.read_bytes()
        if isinstance(source, str):
            if source.lstrip().startswith(('{', '[')):
                return 'observed', source.encode('utf-8')
            return 'observed', Path(source).read_bytes()
        data = source.read()
        if isinstance(data, str):
            data = data.encode('utf-8')
        if isinstance(data, bytes):
            return 'observed', data
    except (OSError, UnicodeError, TypeError, ValueError, AttributeError):
        pass
    return 'unavailable', None


def _valid_digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in '0123456789abcdefABCDEF' for character in value))


def _window(value):
    return value if type(value) is int and value > 0 else None


def _version(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith('codex-cli '):
        value = value.removeprefix('codex-cli ').strip()
    return value or None


def _published_origins(document, model):
    origins = document.get('harbor_sources')
    origins = [origin for origin in origins if isinstance(origin, dict)] \
        if isinstance(origins, list) else []
    if isinstance(model, str) and model.startswith('harbor/'):
        provider = model.split('/', 2)[1]
        matching = [origin for origin in origins
                    if origin.get('provider') in (provider, provider + '_native_context')]
        if matching:
            return matching
    return origins


def _version_from_published(document, entry, origins):
    candidates = [entry.get('harbor_native_client_version'), entry.get('client_version'),
                  document.get('client_version')]
    candidates.extend(origin.get('client_version') for origin in origins)
    versions = {_version(candidate) for candidate in candidates if _version(candidate) is not None}
    if len(versions) == 1:
        return versions.pop(), False
    return None, len(versions) > 1


def _catalog_evidence(source, model, model_epoch, published):
    report = {'status': 'unknown', 'model_epoch': model_epoch, 'catalog_sha256': None,
              'captured_at': None, 'default_window': None, 'maximum_window': None,
              'captured_client_version_sha256': None}
    if published:
        report['source_catalog_sha256s'] = []
    state, raw = _read_evidence(source)
    if state != 'observed':
        report['status'] = state
        return report, None, False, None
    report['catalog_sha256'] = _sha256(raw)
    try:
        document = json.loads(raw)
        models = document.get('models')
        if not isinstance(document, dict) or not isinstance(models, list):
            raise ValueError
    except (ValueError, UnicodeError, TypeError, AttributeError, RecursionError):
        report['status'] = 'unavailable'
        return report, None, False, None

    origins = _published_origins(document, model) if published else []
    if published:
        report['source_catalog_sha256s'] = sorted({origin['sha256'].lower()
            for origin in origins if _valid_digest(origin.get('sha256'))})
    if model is None:
        return report, None, False, None
    entry = next((candidate for candidate in models if isinstance(candidate, dict)
                  and candidate.get('slug', candidate.get('id')) == model), None)
    if entry is None and not published and isinstance(model, str) and model.startswith('harbor/'):
        native_model = model.split('/', 2)[-1]
        entry = next((candidate for candidate in models if isinstance(candidate, dict)
                      and candidate.get('slug', candidate.get('id')) == native_model), None)
    if entry is None:
        report['status'] = 'model-not-found'
        return report, None, False, None

    report['default_window'] = _window(entry.get('context_window'))
    report['maximum_window'] = _window(entry.get('max_context_window'))
    captured_at = (entry.get('harbor_native_fetched_at') if published else None)
    captured_at = captured_at or document.get('fetched_at')
    origin_times = {origin.get('fetched_at') for origin in origins
                    if timestamp(origin.get('fetched_at')) is not None}
    if captured_at is None and len(origin_times) == 1:
        captured_at = origin_times.pop()
    parsed_at = timestamp(captured_at)
    report['captured_at'] = parsed_at.isoformat() if parsed_at else None
    if (report['default_window'] is None or report['maximum_window'] is None
            or report['maximum_window'] < report['default_window']):
        report['status'] = 'unavailable'
        return report, None, False, None

    if published:
        version, conflict = _version_from_published(document, entry, origins)
    else:
        version = _version(document.get('client_version'))
        conflict = False
    if version is not None:
        report['captured_client_version_sha256'] = _sha256(version)
    report['status'] = 'source-version-mismatch' if conflict else 'observed'
    return report, version, conflict, report['catalog_sha256']


def _current_client_evidence(source):
    report = {'status': 'unknown', 'version_sha256': None}
    if source is None:
        return report, None
    try:
        if isinstance(source, Path):
            value = source.read_text(encoding='utf-8')
        elif isinstance(source, bytes):
            value = source.decode('utf-8')
        elif isinstance(source, str):
            value = source
        else:
            value = source.read()
            if isinstance(value, bytes):
                value = value.decode('utf-8')
        value = _version(value)
        if (not value or len(value) > 4096 or '\n' in value or '\r' in value
                or any(ord(character) < 32 for character in value)):
            raise ValueError
    except (OSError, UnicodeError, TypeError, ValueError, AttributeError):
        report['status'] = 'unavailable'
        return report, None
    report.update(status='observed', version_sha256=_sha256(value))
    return report, value


def _comparison(task, published):
    comparison = {'status': 'unknown', 'ratio': None,
                  'explanation': ('No ratio is reported without an exactly observed task effective '
                                  'window and an available published default.')}
    if task['status'] == 'model-changed':
        comparison['status'] = 'model-changed'
        return comparison
    if task['status'] != 'observed':
        comparison['status'] = task['status']
        return comparison
    if published['status'] != 'observed':
        comparison['status'] = published['status']
        return comparison
    observed, default = task['effective_window'], published['default_window']
    ratio = observed / default
    comparison.update(status='observed', ratio=round(ratio, 8))
    if observed == 258400 and default == 272000:
        comparison['explanation'] = (
            'When the task effective window is exactly observed as 258400 and the published '
            'default is exactly 272000, 258400 is 95% of 272000. This does not establish a '
            'universal conversion.')
    else:
        percent = format(ratio * 100, '.8g')
        comparison['explanation'] = (
            f'The exactly observed task effective window is {percent}% of the supplied published '
            'default. This comparison does not establish a universal conversion.')
    return comparison


def audit(stream, limit=10, published_catalog=None, native_catalog=None,
          current_client_version=None):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('limit must be between 1 and 1000')
    cycles = deque(maxlen=limit)
    recent_ids = deque(maxlen=256)
    cycle = pending = model = window = window_at = None
    model_changed_at = None
    previous_model_epoch = None
    model_epoch = 0
    result = {'schema_version': 1, 'records': 0, 'invalid_records': 0,
              'incomplete_final_line': False, 'compactions': 0,
              'duplicate_usage_records': 0, 'unidentified_usage_records': 0}

    def commit_pending():
        nonlocal pending
        if pending is not None and cycle is not None:
            tokens, observed_window = pending['input'], pending['window']
            if cycle['provider_requests'] == 0:
                cycle['first_provider_input_tokens'] = tokens
                cycle['first_provider_cached_input_tokens'] = pending['cached']
                cycle['first_provider_uncached_input_tokens'] = (
                    tokens - pending['cached'] if pending['cached'] is not None else None)
                cycle['first_request_effective_window'] = observed_window
                cycle['first_request_model_epoch'] = pending['model_epoch']
                estimate = pending['estimate']
                cycle['post_compaction_history_estimate'] = estimate
                if estimate is not None:
                    cycle['unattributed_input_minus_history_estimate'] = tokens - estimate
                    if pending['cached'] is not None:
                        cycle['uncached_input_minus_history_estimate'] = (
                            tokens - pending['cached'] - estimate)
                if observed_window is not None:
                    cycle['first_request_exceeds_effective_window'] = tokens > observed_window
            cycle['provider_requests'] += 1
            cycle['last_provider_input_tokens'] = tokens
        pending = None

    for line_number, raw in enumerate(stream, 1):
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            if not raw.endswith(b'\n'):
                result['incomplete_final_line'] = True
            else:
                result['invalid_records'] += 1
            continue
        if not isinstance(record, dict) or not isinstance(record.get('payload'), dict):
            result['invalid_records'] += 1
            continue
        result['records'] += 1
        kind, payload = record.get('type'), record['payload']
        if kind == 'compacted':
            response_id = payload.get('compaction_response_id')
            if not isinstance(response_id, str) or not response_id:
                response_id = None
            if cycle is not None:
                if pending is not None and response_id is not None and pending['id'] == response_id:
                    pending = None
                    cycle['compactor_usage'] = 'excluded_by_response_id'
                else:
                    # Never infer a compactor from a drop in input size.
                    # A non-adjacent usage cannot be removed from aggregates.
                    if response_id in recent_ids:
                        cycle['compactor_usage'] = 'unresolved_nonadjacent_usage'
                    commit_pending()
                end = timestamp(record.get('timestamp'))
                start = timestamp(cycle['started_at'])
                cycle['end_line'] = line_number
                cycle['ended_at'] = end.isoformat() if end else None
                if start and end and end >= start:
                    cycle['elapsed_seconds'] = (end - start).total_seconds()
            result['compactions'] += 1
            start = timestamp(record.get('timestamp'))
            cycle = {'start_line': line_number, 'end_line': None,
                     'started_at': start.isoformat() if start else None, 'ended_at': None,
                     'elapsed_seconds': None, 'model_changed': False, 'start_model_epoch': model_epoch,
                     'post_compaction_history_estimate': None,
                     'first_request_effective_window': None, 'first_request_model_epoch': None,
                     'first_provider_input_tokens': None, 'first_provider_cached_input_tokens': None,
                     'first_provider_uncached_input_tokens': None, 'last_provider_input_tokens': None,
                     'unattributed_input_minus_history_estimate': None,
                     'uncached_input_minus_history_estimate': None,
                     'first_request_exceeds_effective_window': None,
                     'provider_requests': 0, 'compactor_usage': 'not_observed',
                     'tool_outputs': 0, 'tool_output_serialized_bytes': 0}
            cycles.append(cycle)
        elif kind == 'turn_context':
            new_model = payload.get('model')
            if isinstance(new_model, str) and new_model != model:
                if model is not None:
                    window = None
                    window_at = None
                    previous_model_epoch = model_epoch
                    changed = timestamp(record.get('timestamp'))
                    model_changed_at = changed.isoformat() if changed else None
                    if cycle is not None:
                        cycle['model_changed'] = True
                        if cycle['provider_requests'] == 0 and pending is None:
                            cycle['post_compaction_history_estimate'] = None
                model = new_model
                model_epoch += 1
        elif kind == 'event_msg' and payload.get('type') == 'token_count':
            info = payload.get('info')
            if not isinstance(info, dict):
                continue
            observed = number(info.get('model_context_window'))
            if observed is not None and observed > 0:
                window = observed
                observed_at = timestamp(record.get('timestamp'))
                window_at = observed_at.isoformat() if observed_at else None
            usage = info.get('last_token_usage')
            if (cycle is not None and cycle['provider_requests'] == 0 and pending is None
                    and isinstance(usage, dict) and number(usage.get('input_tokens')) == 0
                    and cycle['post_compaction_history_estimate'] is None):
                cycle['post_compaction_history_estimate'] = number(usage.get('total_tokens'))
        elif kind == 'token_usage_record':
            usage = payload.get('usage')
            if not isinstance(usage, dict):
                continue
            tokens = number(usage.get('input_tokens'))
            if tokens is None:
                continue
            response_id = payload.get('response_id')
            if not isinstance(response_id, str) or not response_id:
                result['unidentified_usage_records'] += 1
                continue
            if response_id in recent_ids:
                result['duplicate_usage_records'] += 1
                continue
            recent_ids.append(response_id)
            commit_pending()
            if cycle is not None:
                pending = {'id': response_id, 'input': tokens, 'window': window,
                           'cached': number(usage.get('cached_input_tokens')),
                           'estimate': cycle['post_compaction_history_estimate'], 'model_epoch': model_epoch}
        elif kind == 'response_item' and cycle is not None:
            if payload.get('type') in ('function_call_output', 'custom_tool_call_output'):
                output = payload.get('output')
                if isinstance(output, (str, list, dict)):
                    cycle['tool_outputs'] += 1
                    cycle['tool_output_serialized_bytes'] += len(json.dumps(
                        output, ensure_ascii=True, separators=(',', ':')).encode('utf-8'))
    commit_pending()
    result['latest_observed_context'] = {'model_epoch': model_epoch, 'effective_window': window}
    if window is not None:
        task_context = {'status': 'observed', 'model_epoch': model_epoch,
                        'effective_window': window, 'observed_at': window_at,
                        'changed_at': None, 'previous_model_epoch': None}
    elif previous_model_epoch is not None:
        task_context = {'status': 'model-changed', 'model_epoch': model_epoch,
                        'effective_window': None, 'observed_at': None,
                        'changed_at': model_changed_at,
                        'previous_model_epoch': previous_model_epoch}
    else:
        task_context = {'status': 'unknown', 'model_epoch': model_epoch,
                        'effective_window': None, 'observed_at': None,
                        'changed_at': None, 'previous_model_epoch': None}
    current_client, current_version = _current_client_evidence(current_client_version)
    published, published_version, published_conflict, _ = _catalog_evidence(
        published_catalog, model, model_epoch, True)
    native, native_version, _, native_digest = _catalog_evidence(
        native_catalog, model, model_epoch, False)
    if (published['status'] == 'observed' and published_version is not None
            and ((current_version is not None and published_version != current_version)
                 or (current_version is None and native_version is not None
                     and published_version != native_version))):
        published['status'] = 'source-version-mismatch'
    if native['status'] == 'observed' and native_version is not None \
            and current_version is not None and native_version != current_version:
        native['status'] = 'stale'
    if (published['status'] == 'observed' and native['status'] in ('observed', 'stale')
            and published['source_catalog_sha256s']
            and native_digest not in published['source_catalog_sha256s']):
        published['status'] = 'source-version-mismatch'
    if published_conflict:
        published['status'] = 'source-version-mismatch'
    result['context_evidence'] = {
        'task_observed_effective_window': task_context,
        'published_catalog': published,
        'native_capture': native,
        'current_client': current_client,
        'desktop_adoption': {'status': 'unknown'},
        'task_to_published_default': _comparison(task_context, published),
    }
    result['cycles'] = list(cycles)
    result['limitations'] = [
        'Provider usage is reported, not independently tokenized; cached input is already included.',
        'The input-minus-estimate difference is unattributed, not measured tool or instruction tokens.',
        'Cached input is a subset of provider input; uncached input subtracts it exactly once.',
        'An open cycle may include compactor usage until its compacted record arrives.',
        'Compactor exclusion requires the last distinct usage response ID to match the compacted record.',
        'Usage deduplication covers the most recent 256 response IDs.',
        'Tool output bytes use compact ASCII JSON serialization, not provider tokens or wire bytes.',
        'This is a streaming observation, not an atomic snapshot of a file being appended.',
        'Published and native maximum windows are metadata, not evidence of task adoption.',
        'Client version values are represented only by SHA-256 digests.'
    ]
    return result


def audit_tool_result_aging(stream, limit=10):
    """Estimate aging at compaction boundaries and the latest history.

    The report is advisory and content-free. Input history is rebuilt from
    explicit rollout records in memory because eligibility depends on the
    order of prior model actions and tool results. Nothing is rewritten.
    """
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('limit must be between 1 and 1000')
    history = []
    compactions = invalid_records = 0
    incomplete_final_line = False
    samples = deque(maxlen=limit)

    def sample(label):
        report = _AGING.estimate(history)
        samples.append({'label': label, 'items': len(history), **report})

    for raw in stream:
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            if not raw.endswith(b'\n'):
                incomplete_final_line = True
            else:
                invalid_records += 1
            continue
        if not isinstance(record, dict) or not isinstance(record.get('payload'), dict):
            invalid_records += 1
            continue
        payload = record['payload']
        if record.get('type') == 'compacted':
            if history:
                sample(f'before_compaction_{compactions + 1}')
            history = payload.get('replacement_history')
            history = list(history) if isinstance(history, list) else []
            compactions += 1
        elif record.get('type') == 'response_item':
            history.append(payload)
    latest = _AGING.estimate(history)
    latest_sample = {'label': 'latest_history', 'items': len(history), **latest}
    samples.append(latest_sample)
    retained = list(samples)
    saving_samples = [entry for entry in retained if entry['receipt_savings'] > 0]
    return {
        'schema_version': 1,
        'mode': 'tool_result_aging',
        'compactions': compactions,
        'sampled_histories': len(retained),
        'histories_with_savings': len(saving_samples),
        'sampled_receipt_bytes_saved': sum(entry['receipt_savings'] for entry in saving_samples),
        'sampled_shaping_bytes_saved': sum(entry['shaping_savings'] for entry in saving_samples),
        'invalid_records': invalid_records,
        'incomplete_final_line': incomplete_final_line,
        'latest': latest_sample,
        'samples': retained,
        'limitations': [
            'Byte savings are deterministic estimates, not provider-reported token or billing data.',
            'Only retained samples contribute to aggregate savings when limit truncates older samples.',
            'The mode reads replacement history and response items without rewriting the rollout.',
            'Result indexes and SHA-256 digests identify candidates without exposing content or call IDs.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rollout', help='Explicit local JSONL path; opened read-only.')
    parser.add_argument('--limit', type=int, default=10, help='Recent cycles to retain, 1–1000.')
    parser.add_argument('--published-catalog', type=Path, metavar='PATH',
                        help='Optional explicit published catalog JSON path; opened read-only.')
    parser.add_argument('--native-catalog', type=Path, metavar='PATH',
                        help='Optional explicit native catalog JSON path; opened read-only.')
    parser.add_argument('--current-client-version-file', type=Path, metavar='PATH',
                        help='Optional explicit path containing the current client version.')
    parser.add_argument('--tool-result-aging', action='store_true',
                        help='Estimate reclaimable tool-result bytes at compaction boundaries.')
    args = parser.parse_args()
    try:
        with open(args.rollout, 'rb') as source:
            report = audit_tool_result_aging(source, args.limit) if args.tool_result_aging else audit(
                source, args.limit, published_catalog=args.published_catalog,
                native_catalog=args.native_catalog,
                current_client_version=args.current_client_version_file)
    except (OSError, ValueError):
        print('Cannot audit input. Check that it is readable and limit is between 1 and 1000.', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
