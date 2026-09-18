#!/usr/bin/env python3
"""Read a Codex JSONL rollout and emit content-free compaction measurements.

Memory is bounded by one JSONL record, the retained cycles, and a 256-ID
replay cache. Token-count snapshots are estimates, not provider requests.
"""
import argparse
from collections import deque
from datetime import datetime
import json
import sys


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


def audit(stream, limit=10):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('limit must be between 1 and 1000')
    cycles = deque(maxlen=limit)
    recent_ids = deque(maxlen=256)
    cycle = pending = model = window = None
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
                cycle['first_request_effective_window'] = observed_window
                cycle['first_request_model_epoch'] = pending['model_epoch']
                estimate = pending['estimate']
                cycle['post_compaction_history_estimate'] = estimate
                if estimate is not None:
                    cycle['unattributed_input_minus_history_estimate'] = tokens - estimate
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
                     'first_provider_input_tokens': None, 'last_provider_input_tokens': None,
                     'unattributed_input_minus_history_estimate': None,
                     'first_request_exceeds_effective_window': None,
                     'provider_requests': 0, 'compactor_usage': 'not_observed',
                     'tool_outputs': 0, 'tool_output_serialized_bytes': 0}
            cycles.append(cycle)
        elif kind == 'turn_context':
            new_model = payload.get('model')
            if isinstance(new_model, str) and new_model != model:
                if model is not None:
                    window = None
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
    result['cycles'] = list(cycles)
    result['limitations'] = [
        'Provider usage is reported, not independently tokenized; cached input is already included.',
        'The input-minus-estimate difference is unattributed, not measured tool or instruction tokens.',
        'An open cycle may include compactor usage until its compacted record arrives.',
        'Compactor exclusion requires the last distinct usage response ID to match the compacted record.',
        'Usage deduplication covers the most recent 256 response IDs.',
        'Tool output bytes use compact ASCII JSON serialization, not provider tokens or wire bytes.',
        'This is a streaming observation, not an atomic snapshot of a file being appended.'
    ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rollout', help='Explicit local JSONL path; opened read-only.')
    parser.add_argument('--limit', type=int, default=10, help='Recent cycles to retain, 1–1000.')
    args = parser.parse_args()
    try:
        with open(args.rollout, 'rb') as source:
            report = audit(source, args.limit)
    except (OSError, ValueError):
        print('Cannot audit input. Check that it is readable and limit is between 1 and 1000.', file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
