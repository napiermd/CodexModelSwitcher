#!/usr/bin/env python3
"""Verify an explicit context limit against one exact Responses deployment.

This does not discover credentials or change a Harbor catalog. The caller
supplies an endpoint, deployment, credential environment variable and a local
synthetic payload file. A successful response proves only that exact payload.
"""
import argparse
import json
import os
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def validate_payload(payload, requested_tokens):
    if not isinstance(payload, dict):
        raise ValueError('Synthetic payload must be a JSON object.')
    if payload.get('store') is not False or payload.get('stream') is not False:
        raise ValueError('Synthetic payload must set store=false and stream=false.')
    if payload.get('previous_response_id') is not None:
        raise ValueError('Synthetic payload cannot reference provider history.')
    declared = payload.get('harbor_synthetic_input_tokens')
    if type(declared) is not int or declared <= 0 or declared > requested_tokens:
        raise ValueError('Payload must declare a positive expected token count no larger than the requested limit.')
    if any(key in payload for key in ('model', 'metadata')):
        raise ValueError('Model and metadata are supplied by the verifier, not the payload.')
    return declared


def verification_request(endpoint, deployment, key, payload):
    endpoint = azure_endpoint(endpoint)
    if not deployment or any(character.isspace() for character in deployment):
        raise ValueError('Supply one exact deployment name.')
    body = dict(payload)
    body.pop('harbor_synthetic_input_tokens', None)
    body['model'] = deployment
    data = json.dumps(body, separators=(',', ':')).encode()
    return urllib.request.Request(endpoint + '/responses', data=data,
        headers={'api-key': key, 'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')


def azure_endpoint(value):
    if not isinstance(value, str):
        raise ValueError('Use a direct HTTPS Azure OpenAI resource endpoint.')
    parts = urllib.parse.urlsplit(value.strip())
    hostname = (parts.hostname or '').lower()
    suffixes = ('.openai.azure.com', '.services.ai.azure.com')
    if (parts.scheme != 'https' or not any(hostname.endswith(suffix) and hostname != suffix[1:] for suffix in suffixes)
            or parts.username is not None or parts.password is not None or parts.port is not None
            or parts.query or parts.fragment or parts.path.rstrip('/') != '/openai/v1'):
        raise ValueError('Use a direct HTTPS Azure OpenAI resource endpoint ending in /openai/v1.')
    return 'https://' + hostname + '/openai/v1'


def run(endpoint, deployment, key, payload, requested_tokens, opener=None):
    declared = validate_payload(payload, requested_tokens)
    request = verification_request(endpoint, deployment, key, payload)
    started = time.monotonic()
    opener = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=180) as response:
            result = json.load(response)
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
        result = None
        error.close()
    usage = result.get('usage') if isinstance(result, dict) else None
    observed = usage.get('input_tokens') if isinstance(usage, dict) else None
    completed = isinstance(result, dict) and result.get('status') == 'completed'
    tolerance = max(256, requested_tokens // 100)
    verified = (completed and type(observed) is int
                and observed >= requested_tokens - tolerance
                and observed <= requested_tokens)
    return {'verified': verified,
            'http_status': status, 'response_status': result.get('status') if isinstance(result, dict) else None,
            'declared_expected_input_tokens': declared,
            'observed_input_tokens': observed if type(observed) is int else None,
            'requested_context_limit': requested_tokens,
            'verification_floor': requested_tokens - tolerance,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'scope': 'exact_deployment_and_payload_only'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--deployment', required=True)
    parser.add_argument('--credential-env', required=True)
    parser.add_argument('--payload', required=True, type=Path)
    parser.add_argument('--requested-context-limit', required=True, type=int)
    args = parser.parse_args()
    key = os.environ.get(args.credential_env)
    if not key:
        print('Credential environment variable is unavailable.', file=sys.stderr)
        return 2
    try:
        payload = json.loads(args.payload.read_bytes())
        result = run(args.endpoint, args.deployment, key, payload, args.requested_context_limit)
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
        print('Capacity verification input is invalid.', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['verified'] else 1


if __name__ == '__main__':
    sys.exit(main())
