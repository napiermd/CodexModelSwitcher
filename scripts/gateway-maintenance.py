#!/usr/bin/env python3
"""Explicit macOS gateway maintenance with a short, recoverable client pause.

Secrets stay in memory. No conversation, model selection, or Codex configuration
is edited. This is a coordinated maintenance transaction, not rolling promotion.
"""
import argparse
import ast
from contextlib import contextmanager, closing
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

SUPPORT = Path(__file__).resolve().parents[1] / 'ModelHarbor/Support'
sys.path.insert(0, str(SUPPORT))
import gateway_control as control
import gateway_service as service


class MaintenanceError(Exception):
    pass


def atomic_json(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.maintenance-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, sort_keys=True, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_bytes(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.maintenance-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def processes():
    result = subprocess.run(['/bin/ps', '-axo', 'uid=,pid=,ppid=,lstart=,stat=,comm='],
                            capture_output=True, text=True, timeout=5, check=True)
    found = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 9)
        if len(fields) != 10 or int(fields[0]) != os.getuid():
            continue
        found[int(fields[1])] = {'pid': int(fields[1]), 'ppid': int(fields[2]),
            'started': ' '.join(fields[3:8]), 'state': fields[8], 'executable': fields[9]}
    return found


def issuer(process):
    name = Path(process['executable']).name
    return name in ('codex', 'Model Harbor') or (
        name in ('ChatGPT', 'Codex') and '.app/Contents/MacOS/' in process['executable'])


def same_process(left, right):
    return right is not None and all(left[key] == right[key] for key in ('pid', 'started', 'executable'))


def resume_owned(items, snapshot=processes, send=os.kill):
    current = snapshot()
    for item in reversed(items):
        live = current.get(item['pid'])
        if same_process(item, live) and 'T' in live['state']:
            send(item['pid'], signal.SIGCONT)


@contextmanager
def journal_lease(state):
    fd = os.open(state / 'gateway.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def journal_snapshot(path):
    with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as db, db:
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise MaintenanceError('Unsupported ownership schema.')
        return {'turns': db.execute('SELECT id,route,runtime,binding,uncertain FROM turns ORDER BY id').fetchall(),
                'requests': db.execute('SELECT id,turn_id,finished FROM requests ORDER BY id').fetchall(),
                'counters': db.execute('SELECT name,value FROM counters ORDER BY name').fetchall()}


def migrate_owners(path, captured, old, new, rollback=False):
    """Transfer only captured ownership; never manufacture terminal completion."""
    captured = [tuple(row) for row in captured if row[2] == old]
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT COUNT(*) FROM requests WHERE finished=0').fetchone()[0]:
            raise MaintenanceError('An inference transport is still active.')
        for row in captured:
            actual = db.execute('SELECT id,route,runtime,binding,uncertain FROM turns WHERE id=?', (row[0],)).fetchone()
            source, destination = (new, old) if rollback else (old, new)
            if actual is None or actual[1] != row[1] or actual[3] != row[3]:
                raise MaintenanceError('An ownership binding changed during maintenance.')
            if actual[2] == destination:
                continue  # Idempotent recovery after an already-committed transaction.
            if actual[2] != source or (not rollback and actual[4] != row[4]):
                raise MaintenanceError('Ownership does not match the captured transaction.')
            db.execute('UPDATE turns SET runtime=? WHERE id=? AND runtime=?', (destination, row[0], source))


def binding_contract(path):
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'request_binding')
    return ast.dump(function, include_attributes=False)


class Maintenance:
    def __init__(self, app, state, config, port=48118):
        self.app, self.state, self.config, self.port = app, state, config, port
        self.token = config / 'model-harbor-bridge-token'
        self.job = state / f'service-{port}.plist'
        self.record = None
        self.record_path = None
        self.connections = {}
        self.pause_limit = 150

    def status(self, port=None):
        body, identity = control.owner_request('GET', '/harbor/status', b'', port or self.port, self.token)
        return json.loads(body), identity

    def post(self, path, value, identity, port=None):
        body, returned = control.owner_request('POST', path, json.dumps(value).encode(),
            port or self.port, self.token, expected_runtime=identity)
        if returned != identity:
            raise MaintenanceError('Gateway identity changed during the control operation.')
        return json.loads(body)

    def save(self, phase=None, **fields):
        if phase:
            self.record['phase'] = phase
        self.record.update(fields)
        self.record['updated_at'] = time.time()
        atomic_json(self.record_path, self.record)

    def shared_files(self):
        paths = [self.config / name for name in ('config.toml', 'auth.json', 'model-switcher.json',
            'model-harbor-bridge-token', 'model-harbor-bridge-token.server-proof')]
        paths += sorted((self.config / 'model-catalogs').glob('*.json'))
        return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for path in paths}

    def keys(self):
        saved = json.loads((self.config / 'model-switcher.json').read_text())
        providers = {item['id']: item for item in saved.get('services', [])}
        available = self.record['credentials_available']
        connections, models = {}, []
        for provider in ('azure', 'openrouter'):
            if not available.get(provider):
                continue
            item = providers.get(provider)
            if not item or not item.get('models'):
                raise MaintenanceError('A connected provider has no saved model to verify.')
            result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s',
                'dev.napier.ModelHarbor', '-a', 'provider:' + provider, '-w'],
                capture_output=True, timeout=45)
            key = result.stdout.decode().strip()
            if result.returncode or not key or any(char.isspace() for char in key):
                raise MaintenanceError('A saved provider key could not be read. Existing service preserved.')
            connections[provider] = {'key': key}
            if provider == 'azure':
                connections[provider]['endpoint'] = item['baseURL']
            models.append('harbor/' + provider + '/' + item['models'][0]['id'])
        for row in self.record['ownership']['turns']:
            route = json.loads(row[1])
            if route['provider'] not in ('azure', 'openrouter', 'codex-subscription'):
                raise MaintenanceError('This maintenance version cannot transfer that provider safely.')
            if route['provider'] in ('azure', 'openrouter'):
                if route['provider'] not in connections:
                    raise MaintenanceError('An owned route has no saved connection.')
                model = 'harbor/' + route['provider'] + '/' + route['model']
                if model not in models:
                    models.append(model)
        if not connections:
            raise MaintenanceError('No saved API connection is available for candidate verification.')
        self.connections = connections
        self.record['required_models'] = models

    def restore(self, port=None):
        status, identity = self.status(port)
        result = self.post('/harbor/providers/restore', {
            'expected_runtime': identity, 'expected_configuration_revision': status['configuration_revision'],
            'connections': self.connections, 'required_models': self.record['required_models']}, identity, port)
        if result.get('configuration_revision') != self.record['configuration_revision']:
            raise MaintenanceError('The saved credentials do not reproduce the existing account configuration.')
        return result, identity

    def prepare(self):
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(self.app)],
                       capture_output=True, check=True, timeout=15)
        source = self.app / 'Contents/Resources'
        old_status, old_identity = self.status()
        old_job = plistlib.loads(self.job.read_bytes())
        if old_identity['mode'] != 'independent' or old_job['EnvironmentVariables']['MODEL_HARBOR_RUNTIME_DIGEST'] != old_identity['runtime_id']:
            raise MaintenanceError('The installed service does not match the authenticated listener.')
        old_runtime = Path(old_job['ProgramArguments'][3]).parent
        service.verify_retained(old_runtime, old_identity['runtime_id'])
        if binding_contract(old_runtime / 'grok_adapter.py') != binding_contract(source / 'grok_adapter.py'):
            raise MaintenanceError('The account-binding algorithm changed; transfer is unsupported.')
        runtime, digest = service.retain_runtime(source, self.state)
        if digest == old_identity['runtime_id']:
            raise MaintenanceError('The installed gateway is already current.')
        transaction = str(uuid.uuid4())
        directory = service.private_directory(self.state.parent / 'updates' / ('gateway-' + transaction))
        self.record_path = directory / 'maintenance.json'
        self.record = {'schema': 1, 'id': transaction, 'phase': 'preflight', 'created_at': time.time(),
            'app': str(self.app), 'state': str(self.state), 'config': str(self.config), 'port': self.port,
            'old_identity': old_identity, 'old_job': old_job, 'new_runtime': str(runtime), 'new_digest': digest,
            'configuration_revision': old_status['configuration_revision'], 'shared_files': self.shared_files(),
            'credentials_available': old_status['providers']['credentials_available'],
            'ownership': journal_snapshot(self.state / 'ownership.sqlite'), 'paused': []}
        marker = self.state / 'maintenance.json'
        if marker.exists():
            previous = json.loads(marker.read_text())
            if previous.get('phase') != 'committed':
                raise MaintenanceError('An earlier maintenance transaction requires recovery.')
            self.record['previous_marker'] = previous
        else:
            self.record['previous_marker'] = None
        self.save()
        self.keys()
        self.preflight()
        self.save('ready')
        return self.record_path

    def preflight(self):
        with tempfile.TemporaryDirectory(prefix='harbor-maintenance-probe-') as directory:
            root = Path(directory)
            config, state = root / 'config', root / 'state'
            config.mkdir(mode=0o700); state.mkdir(mode=0o700)
            (config / 'model-catalogs').mkdir(mode=0o700)
            for path in [self.config / 'model-switcher.json'] + list((self.config / 'model-catalogs').glob('*.json')):
                destination = config / path.relative_to(self.config)
                atomic_bytes(destination, path.read_bytes())
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            environment = dict(self.record['old_job']['EnvironmentVariables'], MODEL_HARBOR_PORT=str(port),
                MODEL_HARBOR_CONFIG_DIR=str(config), MODEL_HARBOR_STATE_DIR=str(state),
                MODEL_HARBOR_RUNTIME_DIGEST=self.record['new_digest'])
            process = subprocess.Popen([self.record['old_job']['ProgramArguments'][0], '-B', '-u',
                str(Path(self.record['new_runtime']) / 'grok_adapter.py')], env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.wait_identity(self.record['new_digest'], port, process)
                receipt, _ = self.restore(port)
                self.record['preflight'] = {'verified': True, 'routes': receipt['routes'],
                    'configuration_matches': True, 'separate_port': port, 'isolated_state': True}
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)

    def wait_identity(self, digest, port=None, process=None):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process is not None and process.poll() is not None:
                raise MaintenanceError('The candidate exited before readiness.')
            try:
                status, identity = self.status(port)
                if identity['runtime_id'] == digest:
                    return status, identity
            except control.ControlError:
                pass
            time.sleep(.15)
        raise MaintenanceError('The expected gateway did not become available.')

    def assert_paused(self):
        current = processes()
        expected = {item['pid']: item for item in self.record['paused']}
        for item in current.values():
            if issuer(item) and (item['pid'] not in expected or not same_process(expected[item['pid']], item) or 'T' not in item['state']):
                raise MaintenanceError('A Codex issuer or Harbor configuration writer is not paused.')
        for item in expected.values():
            if not same_process(item, current.get(item['pid'])) or 'T' not in current[item['pid']]['state']:
                raise MaintenanceError('A paused process changed during the transaction.')

    def freeze(self, max_wait):
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            status, identity = self.status()
            if identity != self.record['old_identity'] or status['configuration_revision'] != self.record['configuration_revision']:
                raise MaintenanceError('The retained gateway changed before maintenance.')
            if status['runtime']['active_requests'] == 0:
                break
            time.sleep(.5)
        else:
            raise MaintenanceError('No transport-free pause point occurred; the old gateway was preserved.')
        items = [item for item in processes().values() if issuer(item)]
        if not items or any('T' in item['state'] for item in items):
            raise MaintenanceError('Cannot take ownership of an absent or already-paused client set.')
        self.save('pausing', paused=items, pause_deadline=time.time() + self.pause_limit)
        for item in items:
            if not same_process(item, processes().get(item['pid'])):
                raise MaintenanceError('A client process changed before pause.')
            os.kill(item['pid'], signal.SIGSTOP)
        deadline = time.monotonic() + 3
        while True:
            try:
                self.assert_paused(); break
            except MaintenanceError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        status, identity = self.status()
        if identity != self.record['old_identity'] or status['runtime']['active_requests'] != 0:
            raise MaintenanceError('A request raced the pause; no service replacement was attempted.')
        if status['configuration_revision'] != self.record['configuration_revision'] or self.shared_files() != self.record['shared_files']:
            raise MaintenanceError('Shared configuration changed before maintenance.')
        snapshot = journal_snapshot(self.state / 'ownership.sqlite')
        for row in snapshot['turns']:
            route = json.loads(row[1])
            if route['provider'] not in ('azure', 'openrouter', 'codex-subscription'):
                raise MaintenanceError('A newly owned provider was not covered by preflight.')
            model = 'harbor/' + route['provider'] + '/' + route['model']
            if route['provider'] != 'codex-subscription' and model not in self.record['required_models']:
                raise MaintenanceError('A newly owned API route was not verified during preflight.')
        self.save('paused', ownership=snapshot)

    def launch(self, action):
        label = self.record['old_job']['Label']
        domain = f'gui/{os.getuid()}'
        args = ['/bin/launchctl', action, domain + '/' + label] if action == 'bootout' else ['/bin/launchctl', 'bootstrap', domain, str(self.job)]
        result = subprocess.run(args, capture_output=True, timeout=10)
        if result.returncode:
            raise MaintenanceError('The gateway service manager rejected ' + action + '.')

    def stop_service(self):
        self.launch('bootout')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with journal_lease(self.state):
                    return
            except BlockingIOError:
                time.sleep(.1)
        raise MaintenanceError('The previous runtime has not released its journal.')

    def switch(self):
        self.assert_paused()
        marker = {'schema': 1, 'id': self.record['id'], 'from_runtime': self.record['old_identity']['runtime_id'],
            'to_runtime': self.record['new_digest'], 'configuration_revision': self.record['configuration_revision'],
            'required_models': self.record['required_models'], 'phase': 'prepared'}
        self.save('stopping')
        atomic_json(self.state / 'maintenance.json', marker)
        self.stop_service()
        with journal_lease(self.state):
            self.save('transferring')
            migrate_owners(self.state / 'ownership.sqlite', self.record['ownership']['turns'],
                           marker['from_runtime'], marker['to_runtime'])
            job = json.loads(json.dumps(self.record['old_job']))
            job['EnvironmentVariables']['MODEL_HARBOR_RUNTIME_DIGEST'] = self.record['new_digest']
            job['ProgramArguments'][3] = str(Path(self.record['new_runtime']) / 'grok_adapter.py')
            atomic_bytes(self.job, plistlib.dumps(job, sort_keys=True))
            self.save('starting')
        self.launch('bootstrap')
        status, identity = self.wait_identity(self.record['new_digest'])
        self.assert_paused()
        self.save('restoring', final_identity=identity)
        receipt, identity = self.restore()
        self.assert_paused()
        self.post('/harbor/maintenance/commit', {'expected_runtime': identity,
            'expected_configuration_revision': self.record['configuration_revision'],
            'maintenance_id': self.record['id']}, identity)
        status, identity = self.status()
        if status['configuration_revision'] != self.record['configuration_revision'] or self.shared_files() != self.record['shared_files']:
            raise MaintenanceError('The promoted gateway did not preserve shared account configuration.')
        after = journal_snapshot(self.state / 'ownership.sqlite')
        expected = dict(self.record['ownership'])
        expected['turns'] = [tuple([*row[:2], self.record['new_digest'] if row[2] == self.record['old_identity']['runtime_id'] else row[2], *row[3:]]) for row in expected['turns']]
        if any([tuple(row) for row in expected[key]] != after[key] for key in expected):
            raise MaintenanceError('Ownership changed beyond the explicit runtime transfer.')
        self.save('committed', final_identity=identity, route_receipt=receipt,
                  ownership_preserved=True, shared_configuration_preserved=True)

    def finalize_committed(self):
        marker_path = self.state / 'maintenance.json'
        if marker_path.exists():
            marker = json.loads(marker_path.read_text())
            if (marker.get('id') != self.record['id'] or marker.get('phase') != 'committed'
                    or marker.get('boot_id') != self.record['final_identity']['boot_id']
                    or marker.get('configuration_revision') != self.record['configuration_revision']):
                raise MaintenanceError('The committed maintenance marker changed before finalization.')
            marker_path.unlink()
            directory = os.open(self.state, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        status, identity = self.status()
        if (identity != self.record['final_identity']
                or status['configuration_revision'] != self.record['configuration_revision']
                or status.get('maintenance', {}).get('blocked', False)):
            raise MaintenanceError('The verified gateway is not ready to resume clients.')

    def recover(self):
        self.record = json.loads(self.record_path.read_text())
        phase = self.record['phase']
        if phase in ('resumed', 'rolled_back', 'aborted'):
            resume_owned(self.record['paused']); return
        if phase == 'committed':
            status, identity = self.status()
            if identity != self.record['final_identity'] or status['configuration_revision'] != self.record['configuration_revision']:
                raise MaintenanceError('Committed gateway identity changed; explicit recovery is required.')
            self.finalize_committed()
            resume_owned(self.record['paused']); self.save('resumed'); return
        if phase in ('preflight', 'ready', 'pausing', 'paused'):
            resume_owned(self.record['paused']); self.save('aborted'); return
        self.assert_paused()
        # Determine whether bootout actually completed when the controller stopped.
        launcher = service.Launchctl()
        target = f"gui/{os.getuid()}/" + self.record['old_job']['Label']
        if launcher.registered(target):
            self.stop_service()
        with journal_lease(self.state):
            migrate_owners(self.state / 'ownership.sqlite', self.record['ownership']['turns'],
                self.record['old_identity']['runtime_id'], self.record['new_digest'], rollback=True)
            atomic_bytes(self.job, plistlib.dumps(self.record['old_job'], sort_keys=True))
            # Any earlier committed transaction is complete. Its old boot ID
            # must not gate the newly restarted rollback process.
            previous = self.record['previous_marker']
            if previous is not None and previous.get('phase') != 'committed':
                raise MaintenanceError('Cannot retire an unfinished previous maintenance marker.')
            (self.state / 'maintenance.json').unlink(missing_ok=True)
        self.launch('bootstrap')
        _, identity = self.wait_identity(self.record['old_identity']['runtime_id'])
        for provider, value in self.connections.items():
            self.post('/harbor/providers/' + provider, value, identity)
        status, identity = self.status()
        if (status['configuration_revision'] != self.record['configuration_revision']
                or status.get('maintenance', {}).get('blocked', False)):
            raise MaintenanceError('Rollback could not restore the original account configuration.')
        self.save('rolled_back', rollback_identity=identity)
        resume_owned(self.record['paused'])

    def watchdog(self, controller_pid):
        controller = processes()[controller_pid]
        child = os.fork()
        if child:
            return child
        try:
            while True:
                time.sleep(.25)
                record = json.loads(self.record_path.read_text())
                if record['phase'] in ('resumed', 'rolled_back', 'aborted'):
                    resume_owned(record['paused'])
                    return os._exit(0)
                alive = same_process(controller, processes().get(controller_pid))
                expired = bool(record.get('pause_deadline') and time.time() > record['pause_deadline'])
                if alive and not expired:
                    continue
                if alive:
                    os.kill(controller_pid, signal.SIGKILL)
                with service.ownership_lock(self.state):
                    self.recover()
                return os._exit(0)
        except BaseException:
            # Never resume into an unverified half-switched gateway.
            atomic_json(self.record_path.parent / 'recovery-required.json',
                {'record': str(self.record_path), 'reason': 'Autonomous recovery needs attention.'})
            os._exit(1)

    def activate(self, max_wait):
        self.prepare()
        print(json.dumps({'phase': 'ready', 'record': str(self.record_path)}), flush=True)
        watchdog = self.watchdog(os.getpid())
        try:
            with service.ownership_lock(self.state):
                try:
                    self.freeze(max_wait)
                    self.switch()
                    self.finalize_committed()
                    resume_owned(self.record['paused'])
                    self.save('resumed')
                except BaseException:
                    self.recover()
                    raise
        except BaseException:
            # Lock acquisition can fail before entering the inner handler.
            if self.record['phase'] in ('preflight', 'ready'):
                self.recover()
            raise
        finally:
            deadline = time.monotonic() + self.pause_limit + 15
            while time.monotonic() < deadline:
                if os.waitpid(watchdog, os.WNOHANG)[0]:
                    break
                time.sleep(.1)
            else:
                raise MaintenanceError('The recovery watchdog remains active; inspect its transaction record.')
        return self.record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--recover', type=Path)
    parser.add_argument('--app', type=Path, default=Path('/Applications/Model Harbor.app'))
    parser.add_argument('--state', type=Path, default=Path.home() / 'Library/Application Support/Model Harbor/gateway')
    parser.add_argument('--config', type=Path, default=Path.home() / '.codex')
    parser.add_argument('--max-wait', type=int, default=180)
    args = parser.parse_args()
    if bool(args.activate) == bool(args.recover):
        parser.error('Choose --activate or --recover RECORD. Activation briefly pauses Codex processes.')
    operation = Maintenance(args.app, args.state, args.config)
    try:
        if args.recover:
            operation.record_path = args.recover
            operation.record = json.loads(args.recover.read_text())
            operation.keys()
            with service.ownership_lock(operation.state):
                operation.recover()
            result = operation.record
        else:
            result = operation.activate(args.max_wait)
        print(json.dumps({'phase': result['phase'], 'record': str(operation.record_path),
            'active_runtime': result.get('final_identity')}), flush=True)
        return 0 if result['phase'] == 'resumed' else 1
    except BaseException as error:
        message = str(error) if isinstance(error, (MaintenanceError, control.ControlError, service.ServiceError)) else 'Maintenance failed; inspect the private transaction record.'
        print(json.dumps({'error': message, 'record': str(operation.record_path) if operation.record_path else None}), flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
