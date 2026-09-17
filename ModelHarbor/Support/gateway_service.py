#!/usr/bin/env python3
"""Attach to Harbor or register one retained, independent per-user runtime."""

import argparse
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid


_control_spec = importlib.util.spec_from_file_location('harbor_gateway_control', Path(__file__).with_name('gateway_control.py'))
_control = importlib.util.module_from_spec(_control_spec)
_control_spec.loader.exec_module(_control)

class ServiceError(Exception):
    pass


def private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise ServiceError('The gateway state directory must not be a symlink.')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ServiceError('The gateway state directory must be private to this user.')
    return path.resolve()


@contextlib.contextmanager
def ownership_lock(state):
    descriptor = os.open(state / 'ensure.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
            raise ServiceError('The gateway ownership lock is not private.')
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ServiceError('Another gateway startup is still in progress.') from None
                time.sleep(0.05)
        yield
    finally:
        os.close(descriptor)


def inventory(source):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ServiceError('The runtime source must be a real directory.')
    result = {}
    for directory, names, files in os.walk(source, followlinks=False):
        for name in names:
            if (Path(directory) / name).is_symlink():
                raise ServiceError('Runtime directory symlinks are not supported.')
        for name in sorted(files):
            if not name.endswith('.py'):
                continue
            path = Path(directory) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ServiceError('Runtime Python files must be regular files without symlinks.')
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ServiceError('Runtime Python files must be regular files.')
                data = handle.read()
                after = os.fstat(handle.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode):
                raise ServiceError('The runtime changed during verification.')
            relative = path.relative_to(source).as_posix()
            try:
                compile(data, relative, 'exec')
            except (SyntaxError, ValueError):
                raise ServiceError('A runtime Python file failed syntax verification.') from None
            result[relative] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'mode': stat.S_IMODE(before.st_mode)}
    if 'grok_adapter.py' not in result:
        raise ServiceError('The runtime is missing grok_adapter.py.')
    return dict(sorted(result.items()))


def runtime_digest(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def publish_directory(source, destination):
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
    elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        raise ServiceError('Exclusive runtime publication is unavailable on this platform.')
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def verify_retained(directory, digest, expected=None):
    if directory.is_symlink() or not directory.is_dir():
        raise ServiceError('The retained runtime directory is invalid.')
    manifest_path = directory / 'manifest.json'
    if manifest_path.is_symlink():
        raise ServiceError('The retained runtime manifest is invalid.')
    try:
        manifest = json.loads(manifest_path.read_text())
        actual = inventory(directory)
        for root, _, files in os.walk(directory, followlinks=False):
            for name in files:
                file = Path(root) / name
                relative = file.relative_to(directory).as_posix()
                if file.is_symlink() or (relative != 'manifest.json' and relative not in actual):
                    raise ServiceError('The retained runtime contains an unverified file.')
    except (OSError, ValueError):
        raise ServiceError('The retained runtime could not be verified.') from None
    if manifest != {'runtime_id': digest, 'files': actual} or runtime_digest(actual) != digest or (expected is not None and actual != expected):
        raise ServiceError('The retained runtime integrity check failed.')
    return directory


def retain_runtime(source, state):
    entries = inventory(source)
    digest = runtime_digest(entries)
    versions = private_directory(state / 'runtimes')
    destination = versions / digest
    if destination.exists() or destination.is_symlink():
        return verify_retained(destination, digest, entries), digest
    temporary = Path(tempfile.mkdtemp(prefix='.preparing-', dir=versions))
    try:
        for relative, entry in entries.items():
            source_path, target = Path(source) / relative, temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as incoming, target.open('xb') as outgoing:
                if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                    raise ServiceError('The runtime changed during copying.')
                shutil.copyfileobj(incoming, outgoing)
            target.chmod(entry['mode'])
        if inventory(temporary) != entries or inventory(source) != entries:
            raise ServiceError('The runtime changed during copying.')
        (temporary / 'manifest.json').write_text(json.dumps({'runtime_id': digest, 'files': entries}, sort_keys=True))
        try:
            publish_directory(temporary, destination)
        except OSError as error:
            if error.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
        return verify_retained(destination, digest, entries), digest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def listener_status(port, token_path):
    # Check occupancy without disclosing the bridge token to an unknown listener.
    try:
        connection = socket.create_connection(('127.0.0.1', port), timeout=0.5)
    except ConnectionRefusedError:
        return None
    except OSError:
        raise ServiceError('The gateway endpoint could not be inspected.') from None
    connection.close()
    try:
        _, runtime = _control.owner_request('GET', '/harbor/status', b'', port, token_path)
    except _control.ControlError as error:
        raise ServiceError(str(error)) from None
    result = {'state': 'attached', 'mode': runtime['mode'],
              'maintenance_required': runtime['mode'] == 'legacy'}
    if runtime['mode'] == 'independent':
        result['runtime_id'] = runtime['runtime_id']
    return result


class Launchctl:
    def run(self, arguments):
        try:
            return subprocess.run(['/bin/launchctl', *arguments], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        except (OSError, subprocess.SubprocessError):
            raise ServiceError('The per-user gateway service manager is unavailable.') from None

    def registered(self, target):
        result = self.run(['print', target])
        if result.returncode == 0:
            return True
        if result.returncode == 113:
            return False
        raise ServiceError('The existing gateway job could not be inspected safely.')

    def bootstrap(self, domain, plist):
        if self.run(['bootstrap', domain, str(plist)]).returncode:
            raise ServiceError('The independent gateway could not be registered. No fallback process was started.')


def ensure_service(source, state, config, token, port, python, home, launcher=None, probe=listener_status):
    if not 1 <= port <= 65535:
        raise ServiceError('The gateway port is invalid.')
    existing = probe(port, token)
    if existing is not None:
        return existing
    state = private_directory(state)
    launcher = launcher or Launchctl()
    with ownership_lock(state):
        existing = probe(port, token)
        if existing is not None:
            return existing
        suffix = hashlib.sha256(f'{state}\0{port}'.encode()).hexdigest()[:24]
        label = 'dev.napier.ModelHarbor.gateway.' + suffix
        domain = f'gui/{os.getuid()}'
        if launcher.registered(domain + '/' + label):
            return {'state': 'registered', 'mode': 'independent', 'maintenance_required': False}
        job = state / ('service-' + str(port) + '.plist')
        if job.exists() or job.is_symlink():
            # Reuse an earlier registration attempt's exact runtime and settings.
            if job.is_symlink():
                raise ServiceError('The existing gateway job file is invalid.')
            try:
                settings = plistlib.loads(job.read_bytes())
                environment = settings['EnvironmentVariables']
                runtime = Path(settings['ProgramArguments'][3]).parent
                digest = environment['MODEL_HARBOR_RUNTIME_DIGEST']
                expected_environment = {'HOME': str(home), 'MODEL_HARBOR_INDEPENDENT': '1',
                    'MODEL_HARBOR_STATE_DIR': str(state), 'MODEL_HARBOR_CONFIG_DIR': str(config),
                    'MODEL_HARBOR_TOKEN_PATH': str(token), 'MODEL_HARBOR_PORT': str(port),
                    'MODEL_HARBOR_RUNTIME_DIGEST': digest, 'PYTHONDONTWRITEBYTECODE': '1'}
                if (settings.get('Label') != label or environment != expected_environment
                        or settings['ProgramArguments'][0] != str(python)
                        or settings.get('RunAtLoad') is not True or settings.get('KeepAlive') is not True
                        or settings.get('ProcessType') != 'Background'
                        or settings.get('StandardOutPath') != '/dev/null' or settings.get('StandardErrorPath') != '/dev/null'
                        or set(settings) != {'Label', 'ProgramArguments', 'EnvironmentVariables', 'RunAtLoad', 'KeepAlive', 'ProcessType', 'StandardOutPath', 'StandardErrorPath'}
                        or not re.fullmatch('[0-9a-f]{64}', digest)
                        or runtime != state / 'runtimes' / digest
                        or settings['ProgramArguments'][1:] != ['-B', '-u', str(runtime / 'grok_adapter.py')]):
                    raise ValueError('Job mismatch')
                verify_retained(runtime, digest)
            except (KeyError, IndexError, TypeError, ValueError, OSError):
                raise ServiceError('The retained gateway job differs from these settings. Coordinated maintenance is required.') from None
        else:
            runtime, digest = retain_runtime(source, state)
            environment = {'HOME': str(home), 'MODEL_HARBOR_INDEPENDENT': '1',
                'MODEL_HARBOR_STATE_DIR': str(state), 'MODEL_HARBOR_CONFIG_DIR': str(config),
                'MODEL_HARBOR_TOKEN_PATH': str(token), 'MODEL_HARBOR_PORT': str(port),
                'MODEL_HARBOR_RUNTIME_DIGEST': digest, 'PYTHONDONTWRITEBYTECODE': '1'}
            settings = {'Label': label, 'ProgramArguments': [str(python), '-B', '-u', str(runtime / 'grok_adapter.py')],
                'EnvironmentVariables': environment, 'RunAtLoad': True, 'KeepAlive': True,
                'ProcessType': 'Background', 'StandardOutPath': '/dev/null', 'StandardErrorPath': '/dev/null'}
            descriptor = os.open(job, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                output.write(plistlib.dumps(settings, sort_keys=True))
                output.flush()
                os.fsync(output.fileno())
        launcher.bootstrap(domain, job)
        return {'state': 'registered', 'mode': 'independent', 'runtime_id': digest, 'maintenance_required': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    args = parser.parse_args()
    home = Path.home()
    state = Path(os.environ.get('MODEL_HARBOR_STATE_DIR', str(home / 'Library/Application Support/Model Harbor/gateway')))
    config = Path(os.environ.get('MODEL_HARBOR_CONFIG_DIR', str(home / '.codex')))
    token = Path(os.environ.get('MODEL_HARBOR_TOKEN_PATH', str(config / 'model-harbor-bridge-token')))
    try:
        result = ensure_service(args.source, state, config, token, int(os.environ.get('MODEL_HARBOR_PORT', '48118')), Path(sys.executable), home)
    except (ServiceError, OSError, ValueError) as error:
        # Operating-system errors can contain paths. Never emit raw exception text.
        message = str(error) if isinstance(error, ServiceError) else 'Gateway preparation failed without replacing any service.'
        print(json.dumps({'state': 'error', 'error': message}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
