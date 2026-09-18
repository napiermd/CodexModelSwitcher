#!/usr/bin/env python3
"""Prove the local server's identity before sending owner controls on that socket."""

import base64
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
import time
import tempfile
import uuid

PROOF_PREFIX = b'ModelHarbor server proof v1\n'
MAX_RESPONSE = 2 * 1024 * 1024
COMMAND_TIMEOUTS = {
    ('GET', '/harbor/status'): 2,
    ('GET', '/harbor/usage'): 120,
    ('GET', '/oauth/status'): 60,
    ('POST', '/harbor/verify'): 15,
    ('POST', '/harbor/providers/azure'): 5,
    ('POST', '/harbor/providers/restore'): 43,
    ('POST', '/harbor/providers/openrouter'): 5,
    ('POST', '/harbor/baseten/reconnect'): 70,
    ('POST', '/harbor/repairs/enable'): 5,
    ('POST', '/harbor/repairs/disable'): 5,
}


class ControlError(Exception):
    pass


def connection_binding(connection, server=False):
    local, peer = connection.getsockname(), connection.getpeername()
    server_address, client_address = (local, peer) if server else (peer, local)
    return {'server_host': server_address[0], 'server_port': server_address[1],
            'client_host': client_address[0], 'client_port': client_address[1]}


def proof_digest(secret, nonce, runtime, connection):
    """Server contract. Connection fields must come from its accepted TCP socket."""
    payload = json.dumps({'runtime': runtime, 'connection': connection},
                         sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    return hmac.new(secret.encode('utf-8'), PROOF_PREFIX + nonce.encode('ascii') + b'\n' + payload,
                    hashlib.sha256).hexdigest()


def read_token(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor) as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ControlError('The gateway token file is not private.')
            token = handle.read(8193).strip()
        if not token or len(token) > 8192 or any(c.isspace() for c in token):
            raise ControlError('The gateway token file is invalid.')
        return token
    except (OSError, ValueError):
        raise ControlError('The gateway token is unavailable. Existing listeners were preserved.') from None



def proof_secret_path(token_path):
    path = Path(token_path)
    return path.with_name(path.name + '.server-proof')


def read_proof_secret(token_path):
    try:
        secret = read_token(proof_secret_path(token_path))
        if not re.fullmatch('[0-9a-f]{64}', secret):
            raise ControlError('Invalid proof secret.')
        return secret
    except ControlError:
        raise ControlError('The private Harbor server-proof secret is unavailable or invalid. Coordinated maintenance is required; existing listeners were preserved.') from None


def ensure_proof_secret(token_path):
    """Server startup only. Publish one complete private secret without replacing it."""
    destination = proof_secret_path(token_path)
    if destination.exists() or destination.is_symlink():
        return read_proof_secret(token_path)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.' + destination.name + '.', dir=destination.parent)
        with os.fdopen(descriptor, 'w') as output:
            output.write(secrets.token_hex(32))
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            pass
        return read_proof_secret(token_path)
    except (OSError, ValueError):
        raise ControlError('The private Harbor server-proof secret could not be prepared safely.') from None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def validate_runtime(runtime):
    if not isinstance(runtime, dict) or type(runtime.get('protocol_version')) is not int or runtime['protocol_version'] != 1:
        raise ControlError('The gateway proof has an incompatible runtime protocol.')
    if runtime.get('mode') not in ('independent', 'legacy'):
        raise ControlError('The gateway proof has an invalid runtime mode.')
    if runtime['mode'] == 'independent' and not re.fullmatch('[0-9a-f]{64}', str(runtime.get('runtime_id', ''))):
        raise ControlError('The gateway proof has an invalid runtime identity.')
    try:
        uuid.UUID(runtime['boot_id'])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ControlError('The gateway proof has an invalid boot identity.') from None


def read_response(client, connection, deadline, maximum):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ControlError('The gateway control deadline expired.')
    connection.settimeout(remaining)
    response = client.getresponse()
    try:
        length = int(response.getheader('Content-Length', '-1'))
    except ValueError:
        raise ControlError('The gateway returned an invalid response length.') from None
    if not 0 <= length <= maximum or response.getheader('Transfer-Encoding'):
        raise ControlError('The gateway returned an unsupported response boundary.')
    body = bytearray()
    while len(body) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ControlError('The gateway control deadline expired.')
        connection.settimeout(remaining)
        chunk = response.read1(min(65536, length - len(body)))
        if not chunk:
            raise ControlError('The gateway closed an incomplete response.')
        body.extend(chunk)
    # Complete HTTPResponse's framing state even for zero-length bodies.
    response.read()
    return response, bytes(body)


def owner_request(method, path, body, port, token_path, expected_runtime=None):
    started = time.monotonic()
    if (method, path) not in COMMAND_TIMEOUTS or type(port) is not int or not 1 <= port <= 65535:
        raise ControlError('Unsupported gateway control command.')
    if not isinstance(body, bytes) or len(body) > 32768:
        raise ControlError('The gateway control payload is invalid.')
    if path == '/harbor/providers/restore':
        try:
            declared_runtime = json.loads(body)['expected_runtime']
        except (ValueError, TypeError, KeyError):
            raise ControlError('Restore requires the expected gateway identity.') from None
        if expected_runtime is not None and expected_runtime != declared_runtime:
            raise ControlError('The restore command contains conflicting gateway identities.')
        expected_runtime = declared_runtime
    if expected_runtime is not None:
        validate_runtime(expected_runtime)
    secret = read_proof_secret(token_path)
    client = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
    # HTTPConnection.send normally reconnects when sock is None. Disable that
    # behavior before proof so a changed listener can never receive the token.
    client.auto_open = 0
    try:
        client.connect()
        connection = client.sock
        binding = connection_binding(connection)
        nonce = secrets.token_hex(32)
        challenge = json.dumps({'nonce': nonce}, separators=(',', ':')).encode()
        deadline = time.monotonic() + 2
        client.request('POST', '/harbor/handshake', body=challenge,
                       headers={'Content-Type': 'application/json'})
        response, raw = read_response(client, connection, deadline, 8192)
        if response.status != 200 or response.will_close or client.sock is not connection:
            raise ControlError('This listener cannot prove Harbor ownership. Coordinated maintenance is required; it was left untouched.')
        try:
            proof = json.loads(raw)
            runtime, declared_connection, signature = proof['runtime'], proof['connection'], proof['proof']
            validate_runtime(runtime)
        except (KeyError, TypeError, ValueError):
            raise ControlError('The listener returned an invalid server proof.') from None
        if (declared_connection != binding or not isinstance(signature, str)
                or not re.fullmatch('[0-9a-f]{64}', signature)
                or not hmac.compare_digest(signature, proof_digest(secret, nonce, runtime, binding))):
            raise ControlError('The listener failed Harbor server authentication. No owner credentials were sent.')
        if expected_runtime is not None and runtime != expected_runtime:
            raise ControlError('The gateway changed. No owner credentials or restore payload were sent. Refresh its status before restoring.')
        if client.sock is not connection or connection_binding(connection) != binding:
            raise ControlError('The authenticated gateway connection changed.')
        token = read_token(token_path)
        deadline = time.monotonic() + COMMAND_TIMEOUTS[(method, path)]
        if path == '/harbor/providers/restore':
            deadline = min(started + 45, deadline)
        connection.settimeout(max(.001, deadline - time.monotonic()))
        client.request(method, path, body=body or None,
                       headers={'X-Model-Harbor-Token': token, 'Content-Type': 'application/json'})
        response, result = read_response(client, connection, deadline, MAX_RESPONSE)
        if response.status != 200:
            if path == '/harbor/providers/restore':
                message = {400: 'The saved connection restore request is invalid.',
                           409: 'The gateway context, connection, or retained ownership changed. Refresh its status; nothing was replaced.',
                           503: 'Required saved connection checks could not complete. Refresh gateway status before retrying.'}.get(response.status)
                raise ControlError(message or 'The authenticated gateway rejected saved connection restoration.')
            raise ControlError('The authenticated gateway rejected the control request.')
        if path == '/harbor/status':
            try:
                value = json.loads(result)
            except ValueError:
                raise ControlError('The authenticated gateway returned invalid status.') from None
            if (not isinstance(value, dict) or value.get('routing') != 'per-task'
                    or not isinstance(value.get('providers'), dict)
                    or not isinstance(value.get('runtime'), dict)
                    or any(value['runtime'].get(key) != runtime.get(key)
                           for key in ('protocol_version', 'runtime_id', 'boot_id', 'mode'))):
                raise ControlError('Gateway status did not match its authenticated runtime identity.')
        return result, runtime
    except ControlError:
        raise
    except (OSError, ValueError, http.client.HTTPException):
        raise ControlError('The authenticated gateway connection failed. No request was replayed.') from None
    finally:
        client.close()


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ControlError('The gateway control payload is too large.')
        command = json.loads(raw)
        if (not isinstance(command, dict) or not {'method', 'path', 'body_base64'} <= set(command)
                or not set(command) <= {'method', 'path', 'body_base64', 'expected_runtime'}):
            raise ControlError('The gateway control command is invalid.')
        body = base64.b64decode(command['body_base64'], validate=True)
        config = Path(os.environ.get('MODEL_HARBOR_CONFIG_DIR', str(Path.home() / '.codex')))
        token = Path(os.environ.get('MODEL_HARBOR_TOKEN_PATH', str(config / 'model-harbor-bridge-token')))
        result, runtime = owner_request(command['method'], command['path'], body,
            int(os.environ.get('MODEL_HARBOR_PORT', '48118')), token, expected_runtime=command.get('expected_runtime'))
        print(json.dumps({'ok': True, 'body_base64': base64.b64encode(result).decode(), 'runtime': runtime}))
        return 0
    except (ControlError, OSError, ValueError, TypeError, KeyError) as error:
        message = str(error) if isinstance(error, ControlError) else 'The gateway control command failed safely.'
        print(json.dumps({'ok': False, 'error': message}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
