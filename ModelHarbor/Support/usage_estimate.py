"""Byte-based input-token estimates for responses with zero or missing usage.

Estimates exist so context accounting never treats an unmeasured request as
free. They are always labeled, always err high, and never modify request or
response bytes.
"""
BYTES_PER_TOKEN = 3.3
DEFAULT_IMAGE_TOKEN_BOUND = 4096
DEEPSEEK_IMAGE_TOKEN_BOUND = 1024


def _image_bound(model):
    if isinstance(model, str) and 'deepseek' in model.lower() and 'flash' in model.lower():
        return DEEPSEEK_IMAGE_TOKEN_BOUND
    return DEFAULT_IMAGE_TOKEN_BOUND


def _visible_bytes(value, images):
    """Serialized bytes of model-visible content. Well-formed image
    references are replaced by a bounded token count at the caller; malformed
    references keep their full serialized size. Encrypted ciphertext
    contributes nothing. Returns bytes."""
    if isinstance(value, dict):
        kind = value.get('type')
        if kind in ('input_image', 'image_url'):
            url = value.get('image_url') or value.get('file_id')
            if isinstance(url, dict):
                url = url.get('url')
            both = value.get('image_url') is not None and value.get('file_id') is not None
            if not both and isinstance(url, str) and (url.startswith('http') or url.startswith('data:')):
                images.append(True)
                return 0
        total = 0
        for key, item in value.items():
            if key == 'encrypted_content':
                continue
            total += _visible_bytes(item, images)
        return total
    if isinstance(value, list):
        return sum(_visible_bytes(item, images) for item in value)
    if isinstance(value, str):
        return len(value.encode('utf-8'))
    if value is None or isinstance(value, bool):
        return 0
    return len(str(value).encode('utf-8'))


def estimate_input_tokens(request, model=None, context_window=None):
    """Estimate input tokens for one translated request. Never raises."""
    try:
        if not isinstance(request, dict):
            return None
        images = []
        total = 0
        for key in ('instructions', 'input', 'tools'):
            if key in request:
                total += _visible_bytes(request[key], images)
        bound = _image_bound(model)
        tokens = int(total / BYTES_PER_TOKEN + 0.5) + len(images) * bound
        if tokens <= 0:
            return None
        if type(context_window) is int and context_window > 0:
            tokens = min(tokens, context_window)
        return tokens
    except Exception:
        return None
