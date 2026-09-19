"""Reasoning replay contract for Chat-bridged thinking models.

A thinking model reached over Chat Completions emits reasoning_content and
expects it back on the assistant turn that produced it. Replayed as visible
text, the model reads its own thinking as prose and loops. Dropping it where
the vendor requires replay returns HTTP 400. Entries require live or vendor
documentation evidence; unlisted routes are byte-identical passthrough.
"""
import re

# Evidence per family: DeepSeek returns HTTP 400 when reasoning_content is not
# passed back in thinking mode (codex-router #703). GLM-5.x vendors require the
# full historical replay. Kimi K3's platform guide requires it in multi-turn
# conversations and tool-call loops; K2.x does not preserve thinking and is
# excluded. MiniMax M3's interleaved thinking depends on prior-round replay.
_FAMILIES = (
    ('deepseek', re.compile(r'(^|/)deepseek-(v\d|reasoner|flash)', re.I)),
    ('glm', re.compile(r'(^|/)glm-5', re.I)),
    ('kimi-k3', re.compile(r'(^|/)kimi-k3$', re.I)),
    ('minimax-m3', re.compile(r'(^|/)minimax-m3$', re.I)),
)


def reasoning_family(upstream_model):
    """Return the replay family for an upstream model id, or None."""
    if not isinstance(upstream_model, str):
        return None
    for family, pattern in _FAMILIES:
        if pattern.search(upstream_model):
            return family
    return None


def replay_reasoning(history, upstream_model):
    """Return assistant-history messages with reasoning carried as
    reasoning_content fields. Unlisted models and non-assistant items pass
    through unchanged. Never emits reasoning as visible text."""
    if reasoning_family(upstream_model) is None or not isinstance(history, list):
        return history
    result = []
    for item in history:
        if not isinstance(item, dict) or item.get('type') != 'reasoning':
            result.append(item)
            continue
        content = item.get('content')
        if isinstance(content, list):
            text = ''.join(part.get('text', '') for part in content
                           if isinstance(part, dict) and part.get('type') == 'reasoning_text')
        else:
            text = ''
        if not text and isinstance(item.get('summary'), list):
            text = ''.join(part.get('text', '') for part in item['summary']
                           if isinstance(part, dict) and part.get('type') == 'summary_text')
        if not text:
            continue
        result.append((item, text))
    return result


def assistant_messages(history, upstream_model):
    """Return Chat-style assistant messages carrying reasoning_content for
    replay-contract families. Each entry is (reasoning_text, item); callers
    merge them with their own message assembly. Unlisted models return []."""
    replayed = replay_reasoning(history, upstream_model)
    if replayed is history:
        return []
    return [(text, item) for item, text in replayed]
