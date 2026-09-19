#!/usr/bin/env python3
"""Opt-in Azure proof: interrupt one stored stream, then resume it with one GET."""
import argparse
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.parse
import urllib.request


class VerificationError(Exception):
    pass


def endpoint(value):
    parts = urllib.parse.urlsplit(value.strip())
    host = (parts.hostname or '').lower()
    if (parts.scheme != 'https' or parts.username or parts.password or parts.port
            or parts.query or parts.fragment
            or not host.endswith(('.openai.azure.com', '.services.ai.azure.com'))):
        raise VerificationError('Saved Azure endpoint is not a direct HTTPS resource endpoint.')
    return urllib.parse.urlunsplit(('https', host, '/openai/v1', '', ''))


def frame_events(response):
    block = []
    for line in response:
        if line.strip():
            block.append(line.rstrip(b'\r\n'))
            continue
        if block:
            payload = b'\n'.join(item[5:].lstrip() for item in block if item.startswith(b'data:'))
            block = []
            if payload and payload != b'[DONE]':
                yield json.loads(payload)


def open_no_redirect(request, timeout=90):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    return urllib.request.build_opener(NoRedirect).open(request, timeout=timeout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--deployment', default='gpt-5.6-sol')
    parser.add_argument('--endpoint')
    parser.add_argument('--key-stdin', action='store_true')
    parser.add_argument('--close-after', type=int, default=4)
    args = parser.parse_args()
    if args.close_after < 0:
        parser.error('--close-after must be nonnegative')

    if args.endpoint:
        base = endpoint(args.endpoint)
    else:
        saved = json.loads((Path.home() / '.codex/model-switcher.json').read_text())
        base = endpoint(next(service['baseURL'] for service in saved['services'] if service['id'] == 'azure'))
    if args.key_stdin:
        import sys
        key = sys.stdin.buffer.read(4097).decode().strip()
    else:
        key = subprocess.run(['security', 'find-generic-password', '-s', 'dev.napier.ModelHarbor',
            '-a', 'provider:azure', '-w'], capture_output=True, text=True, check=True, timeout=45).stdout.strip()
    if not key or len(key) > 4096 or any(character.isspace() for character in key):
        raise VerificationError('A valid Azure key is required.')

    headers = {'api-key': key, 'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
    body = json.dumps({'model': args.deployment, 'background': True, 'store': True, 'stream': True,
        'max_output_tokens': 256, 'input': 'Reply with only HARBOR_RESUME_OK.'}).encode()
    response_id = None
    sequence = None
    seen = 0
    initial = urllib.request.Request(base + '/responses', data=body, headers=headers, method='POST')
    with open_no_redirect(initial) as response:
        for event in frame_events(response):
            seen += 1
            provider_response = event.get('response') if isinstance(event.get('response'), dict) else {}
            response_id = provider_response.get('id') or event.get('response_id') or response_id
            if type(event.get('sequence_number')) is int:
                sequence = event['sequence_number']
            if seen > args.close_after and response_id and sequence is not None:
                break
    if not response_id or sequence is None:
        raise VerificationError('Azure did not provide a resumable response cursor before interruption.')

    query = urllib.parse.urlencode({'stream': 'true', 'starting_after': sequence})
    resume = urllib.request.Request(base + '/responses/' + urllib.parse.quote(response_id, safe='') + '?' + query,
                                    headers={'api-key': key, 'Accept': 'text/event-stream'}, method='GET')
    first_resumed = None
    terminal = None
    try:
        with open_no_redirect(resume) as response:
            for event in frame_events(response):
                if type(event.get('sequence_number')) is int and first_resumed is None:
                    first_resumed = event['sequence_number']
                if event.get('type') in ('response.completed', 'response.failed', 'response.incomplete'):
                    terminal = event['type']
                    break
    finally:
        delete = urllib.request.Request(base + '/responses/' + urllib.parse.quote(response_id, safe=''),
                                        headers={'api-key': key}, method='DELETE')
        try:
            with open_no_redirect(delete, timeout=15) as response:
                response.read(1024)
        except Exception:
            pass
    evidence = {'verified': terminal == 'response.completed' and first_resumed is not None and first_resumed > sequence,
                'initial_post_count': 1, 'resume_get_count': 1, 'starting_after': sequence,
                'first_resumed_sequence': first_resumed, 'strictly_after': first_resumed is not None and first_resumed > sequence,
                'terminal': terminal, 'cleanup_attempted': True}
    print(json.dumps(evidence, indent=2))
    if not evidence['verified']:
        raise VerificationError('Azure did not complete through the documented resume contract.')


if __name__ == '__main__':
    try:
        main()
    except (VerificationError, OSError, ValueError, KeyError, subprocess.SubprocessError,
            urllib.error.URLError) as error:
        print(json.dumps({'verified': False, 'failure_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, VerificationError) else 'Probe failed; no private details emitted.'}))
        raise SystemExit(1)
