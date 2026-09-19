"""Estimate reclaimable tool-result bytes. Advisory only: nothing here ever
modifies a request or a rollout.

Eligibility mirrors the proven reference rules: textual function/custom tool
results over 32 KiB that the model has already acted on, with the newest 4
results always protected.
"""
import hashlib

MIN_BYTES = 32 * 1024
FRONTIER = 4
DENSE_MIN_BYTES = 8 * 1024
DENSE_MIN_SAVED = 1024
PREVIEW = 1024
OUTPUT_TYPES = ('function_call_output', 'custom_tool_call_output')
ACTION_TYPES = ('function_call', 'custom_tool_call', 'reasoning')

IMPORTANT = ('error', 'fail', 'exception', 'fatal', 'panic', 'traceback', 'warning', 'denied', 'invalid', 'security')


def _textual(item):
    if not isinstance(item, dict) or item.get('type') not in OUTPUT_TYPES:
        return None
    output = item.get('output')
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        parts = []
        for part in output:
            if not isinstance(part, dict) or part.get('type') not in ('input_text', 'text'):
                return None
            if not isinstance(part.get('text'), str):
                return None
            parts.append(part['text'])
        return ''.join(parts)
    return None


def _acted_on(items):
    acted = [False] * len(items)
    later = False
    for index in range(len(items) - 1, -1, -1):
        acted[index] = later
        item = items[index]
        if isinstance(item, dict) and (item.get('type') in ACTION_TYPES or
                (item.get('type') == 'message' and item.get('role') == 'assistant')):
            later = True
    return acted


def _dense_shaped(value):
    """Deterministic shaping estimate: collapse terminal rewrites, repeated
    lines, and deeply indented boilerplate while preserving error lines."""
    lines = value.replace('\r\n', '\n').split('\n')
    collapsed = []
    for line in lines:
        if '\r' in line:
            rewrites = line.split('\r')
            final = next((entry for entry in reversed(rewrites) if entry), '')
            important = [entry for entry in rewrites if entry != final and _important(entry)]
            line = '\n'.join(important + [final])
        collapsed.append(line)
    out = []
    index = 0
    while index < len(collapsed):
        line = collapsed[index]
        end = index + 1
        while end < len(collapsed) and collapsed[end] == line:
            end += 1
        count = end - index
        if count >= 3 and line.strip():
            out.extend([line, f'[same line repeated {count - 1} more times]'])
        elif not line.strip() and count > 1:
            out.append(line)
        else:
            out.extend(collapsed[index:end])
        index = end
    return '\n'.join(out)


def _important(line):
    lowered = line.lower()
    return any(word in lowered for word in IMPORTANT)


def estimate(items):
    """Compute advisory savings for one input list. Never mutates input."""
    if not isinstance(items, list):
        return {'eligible': 0, 'bytes_before': 0, 'bytes_after_receipt': 0,
                'bytes_after_shaping': 0, 'largest': 0, 'results': []}
    output_indexes = [i for i, item in enumerate(items)
                      if isinstance(item, dict) and item.get('type') in OUTPUT_TYPES]
    protected = set(output_indexes[-FRONTIER:])
    acted = _acted_on(items)
    result = {'eligible': 0, 'bytes_before': 0, 'bytes_after_receipt': 0,
              'bytes_after_shaping': 0, 'largest': 0, 'results': []}
    for index, item in enumerate(items):
        if index in protected:
            continue
        value = _textual(item)
        if value is None:
            continue
        size = len(value.encode('utf-8'))
        result['largest'] = max(result['largest'], size)
        if size <= MIN_BYTES or not acted[index]:
            continue
        digest = hashlib.sha256(value.encode('utf-8')).hexdigest()
        receipt_size = min(size, 2 * PREVIEW + 512)
        shaped = _dense_shaped(value)
        shaped_size = len(shaped.encode('utf-8')) + 512
        result['eligible'] += 1
        result['bytes_before'] += size
        result['bytes_after_receipt'] += receipt_size
        result['bytes_after_shaping'] += shaped_size if size - shaped_size >= DENSE_MIN_SAVED else size
        result['results'].append({'index': index, 'bytes': size, 'sha256': digest,
                                  'call_id': item.get('call_id') if isinstance(item.get('call_id'), str) else None})
    result['receipt_savings'] = result['bytes_before'] - result['bytes_after_receipt']
    result['shaping_savings'] = result['bytes_before'] - result['bytes_after_shaping']
    return result
