#!/usr/bin/env python3
"""Report context-window drift: provider-accepted input above declared windows.

Read-only. Scans the usage ledger and compares against the merged catalog.
Never edits catalogs. Output is counts and ratios only.
"""
import argparse
import json
from pathlib import Path
import sys

IMPLAUSIBLE_MULTIPLE = 64


def load_events(path):
    events = []
    try:
        with open(path, 'rb') as handle:
            for raw in handle:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except OSError:
        pass
    return events


def accepted_input(event):
    """Only a provider-accepted, provider-reported turn is evidence."""
    if event.get('status') != 'completed' or event.get('http_status') != 200:
        return None
    if event.get('usage_source') != 'provider':
        return None
    if event.get('retried'):
        return None
    usage = event.get('usage')
    value = usage.get('input_tokens') if isinstance(usage, dict) else None
    return value if type(value) is int and value > 0 else None


def drift(events, catalog):
    declared = {}
    for entry in catalog.get('models', []):
        if not isinstance(entry, dict):
            continue
        slug = entry.get('slug')
        window = entry.get('context_window')
        if isinstance(slug, str) and type(window) is int and window > 0:
            declared[slug] = {'declared': window,
                              'maximum': entry.get('max_context_window'),
                              'auto_compact': entry.get('auto_compact_token_limit')}
    ceilings = {}
    for event in events:
        accepted = accepted_input(event)
        model = event.get('model')
        if accepted is None or not isinstance(model, str):
            continue
        ceilings[model] = max(ceilings.get(model, 0), accepted)
    rows = []
    for slug, highest in ceilings.items():
        entry = declared.get(slug)
        if entry is None:
            rows.append({'slug': slug, 'status': 'not-in-catalog', 'observed': highest})
            continue
        if highest <= entry['declared']:
            continue
        if highest > entry['declared'] * IMPLAUSIBLE_MULTIPLE:
            rows.append({'slug': slug, 'status': 'implausible', 'declared': entry['declared'],
                         'observed': highest})
            continue
        rows.append({'slug': slug, 'status': 'drift', 'declared': entry['declared'],
                     'maximum': entry['maximum'], 'auto_compact': entry['auto_compact'],
                     'observed': highest, 'ratio': round(highest / entry['declared'], 2)})
    rows.sort(key=lambda row: row.get('ratio', 0), reverse=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', required=True, type=Path)
    parser.add_argument('--catalog', required=True, type=Path)
    args = parser.parse_args()
    try:
        catalog = json.loads(args.catalog.read_bytes())
    except (OSError, ValueError):
        print(json.dumps({'status': 'catalog-unavailable', 'drift': []}, indent=2))
        return 2
    if not args.ledger.exists():
        print(json.dumps({'status': 'ledger-unavailable', 'drift': []}, indent=2))
        return 2
    rows = drift(load_events(args.ledger), catalog)
    print(json.dumps({'status': 'ok', 'drift': rows}, indent=2))
    return 0 if not any(row['status'] == 'drift' for row in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
