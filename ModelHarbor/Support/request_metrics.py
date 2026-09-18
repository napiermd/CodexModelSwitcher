"""Bounded, content-free measurements for provider request composition."""
import copy
import json
import threading
import time
from collections import Counter, deque


def serialized_bytes(value):
    return len(json.dumps(value, separators=(',', ':')).encode())


def _input_shape(items):
    result = {}
    public_kinds = {'message', 'reasoning', 'function_call', 'function_call_output',
                    'custom_tool_call', 'custom_tool_call_output', 'compaction'}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            key = 'other'
        else:
            kind = item.get('type')
            if kind in public_kinds:
                key = kind
            elif not kind and item.get('role') in ('user', 'assistant', 'developer', 'system'):
                key = 'message:' + item['role']
            else:
                key = 'unknown'
        bucket = result.setdefault(key, {'items': 0, 'serialized_bytes': 0})
        bucket['items'] += 1
        bucket['serialized_bytes'] += serialized_bytes(item)
    return result


def _sensitive_shapes(value):
    found = Counter()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            kind = item.get('type')
            if kind in ('input_image', 'image_url'):
                found['images'] += 1
                found['image_serialized_bytes'] += serialized_bytes(item)
                continue
            encrypted = item.get('encrypted_content')
            if isinstance(encrypted, str):
                found['encrypted_items'] += 1
                found['encrypted_utf8_bytes'] += len(encrypted.encode())
            stack.extend(item.values())
    return dict(found)


def measure(source, translated, route, wire_bytes):
    """Measure one already-translated request without retaining its content."""
    input_items = translated.get('input')
    tools = translated.get('tools')
    instructions = translated.get('instructions')
    kinds = {item.get('type') for item in input_items
             if isinstance(input_items, list) and isinstance(item, dict)}
    report = {
        'provider': route.get('provider') if isinstance(route, dict) else None,
        'model': route.get('model') if isinstance(route, dict) else None,
        'request_kind': 'contains_compaction_item' if 'compaction' in kinds else 'normal_or_unknown',
        'wire_bytes': len(wire_bytes),
        'source_serialized_bytes': serialized_bytes(source),
        'instructions_serialized_bytes': serialized_bytes(instructions) if instructions is not None else 0,
        'tools_serialized_bytes': serialized_bytes(tools) if tools is not None else 0,
        'tool_count': len(tools) if isinstance(tools, list) else 0,
        'input_serialized_bytes': serialized_bytes(input_items) if input_items is not None else 0,
        'input_item_count': len(input_items) if isinstance(input_items, list) else 0,
        'input_shape': _input_shape(input_items),
        'reasoning_serialized_bytes': serialized_bytes(translated['reasoning']) if 'reasoning' in translated else 0,
    }
    report.update(_sensitive_shapes(translated))
    return report


class Recorder:
    def __init__(self, limit=32, clock=time.time):
        self._records = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._clock = clock
        self._sequence = 0

    def begin(self, source, translated, route, wire_bytes):
        report = measure(source, translated, route, wire_bytes)
        with self._lock:
            self._sequence += 1
            handle = self._sequence
            report.update({'sequence': handle, 'started_at': self._clock(), 'state': 'started', 'usage': None})
            self._records.append(report)
        return handle

    def finish(self, handle, state, usage=None):
        with self._lock:
            record = next((item for item in reversed(self._records) if item['sequence'] == handle), None)
            if record is None:
                return
            record['state'] = state if state in ('completed', 'failed', 'cancelled', 'disconnected', 'incomplete') else 'incomplete'
            if isinstance(usage, dict):
                fields = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')
                record['usage'] = {key: usage[key] for key in fields
                                   if type(usage.get(key)) is int and usage[key] >= 0}

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(list(self._records))
