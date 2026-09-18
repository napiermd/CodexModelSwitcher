"""Ownership evidence for an independent gateway; no inferred turn completion."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import re
import tempfile
import threading
import time
import uuid

PROTOCOL_VERSION = 1
LIFECYCLE_GATE = 'Desktop turn completion is not verified. Stage updates for coordinated maintenance.'


class MaintenanceError(ValueError):
    pass


def turn_key(source, headers):
    metadata = source.get('client_metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('Invalid turn metadata')
    nested = metadata.get('x-codex-turn-metadata', headers.get('x-codex-turn-metadata', {}))
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except ValueError as error:
            raise ValueError('Invalid turn metadata JSON') from error
    if not isinstance(nested, dict):
        raise ValueError('Invalid turn metadata')
    for values in (metadata, nested):
        for field in ('thread_id', 'turn_id', 'session_id'):
            if field in values and (not isinstance(values[field], str) or not values[field] or len(values[field]) > 512):
                raise ValueError('Invalid turn identity')
    turn = nested.get('turn_id', metadata.get('turn_id'))
    task = nested.get('thread_id', metadata.get('thread_id', metadata.get('session_id')))
    if turn is None or task is None:
        return None
    return hashlib.sha256(json.dumps([task, turn], separators=(',', ':')).encode()).hexdigest()


class RouteReadiness:
    """Proofs are specific to a boot, route and private configuration fingerprint."""
    def __init__(self, ttl=300, clock=time.time):
        self.ttl, self.clock = ttl, clock
        self.boot_id = str(uuid.uuid4())
        self.lock = threading.RLock()
        self.proofs = {}

    def record(self, route, revision, result):
        if result not in ('verified', 'auth_failed', 'unavailable', 'invalid_response'):
            raise ValueError('Invalid verification result')
        with self.lock:
            self.proofs[(route['provider'], route['model'])] = {
                'provider': route['provider'], 'model': route['model'],
                'boot_id': self.boot_id, 'configuration_revision': revision,
                'result': result, 'checked_at': self.clock()}

    def invalidate(self, provider):
        with self.lock:
            self.proofs = {k: v for k, v in self.proofs.items() if k[0] != provider}

    def snapshot(self, revision):
        with self.lock:
            records = []
            for value in self.proofs.values():
                record = dict(value)
                record['verified'] = (record['result'] == 'verified'
                    and record['configuration_revision'] == revision
                    and 0 <= self.clock() - record['checked_at'] < self.ttl)
                records.append(record)
            return records


class GatewayRuntime:
    def __init__(self, directory, runtime_id, boot_id, max_turns=100000):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
            raise ValueError('Gateway state directory must be private and not a symlink')
        self.runtime_id, self.boot_id = runtime_id, boot_id
        self.max_turns = max_turns
        self.lock = threading.RLock()
        self.lease = os.open(self.directory / 'gateway.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.lease)
            raise ValueError('Another gateway owns this state directory')
        self.db = None
        try:
            path = self.directory / 'ownership.sqlite'
            for suffix in ('', '-wal', '-shm', '-journal'):
                if Path(str(path) + suffix).is_symlink():
                    raise ValueError('Gateway ownership journal must not be a symlink')
            self.db = sqlite3.connect(path, check_same_thread=False)
            path.chmod(0o600)
            self.db.execute('PRAGMA synchronous=FULL')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise ValueError('Unsupported gateway ownership journal version')
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS turns (
                id TEXT PRIMARY KEY, route TEXT NOT NULL, runtime TEXT NOT NULL,
                binding TEXT, uncertain INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, turn_id TEXT, finished INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
              PRAGMA user_version=1;
            ''')
            with self.db:
                self.db.execute('UPDATE turns SET uncertain=1 WHERE id IN (SELECT turn_id FROM requests WHERE finished=0)')
                self.db.execute('UPDATE requests SET finished=1 WHERE finished=0')
        except BaseException:
            self.close()
            raise

    def pin(self, key, select_route):
        with self.lock, self.db:
            if key:
                row = self.db.execute('SELECT route, runtime, uncertain FROM turns WHERE id=?', (key,)).fetchone()
                if row:
                    if row[1] != self.runtime_id:
                        raise ValueError('This unfinished turn belongs to another runtime. Resume its retained runtime during maintenance.')
                    if row[2]:
                        raise ValueError('Previous delivery for this turn is uncertain. Review it before resuming; Harbor will not replay it.')
                    return json.loads(row[0])
            route = select_route()
            if key:
                if self.db.execute('SELECT COUNT(*) FROM turns').fetchone()[0] >= self.max_turns:
                    raise ValueError('Unfinished turn journal is full. Existing owners are retained; new turns require maintenance.')
                self.db.execute('INSERT INTO turns(id,route,runtime) VALUES(?,?,?)',
                                (key, json.dumps(route, sort_keys=True), self.runtime_id))
            else:
                self.db.execute("INSERT INTO counters VALUES('untracked',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
            return dict(route)

    def begin(self, key, binding):
        with self.lock, self.db:
            if key:
                row = self.db.execute('SELECT binding,uncertain FROM turns WHERE id=?', (key,)).fetchone()
                if not row or row[1]:
                    raise ValueError('Turn ownership is unavailable or delivery is uncertain')
                if row[0] and row[0] != binding:
                    raise ValueError('Account or route configuration changed during an unfinished turn. Restore its original connection before continuing.')
                if self.db.execute('SELECT 1 FROM requests WHERE turn_id=? AND finished=0', (key,)).fetchone():
                    raise ValueError('This turn already has a request in flight; duplicate dispatch was refused')
                self.db.execute('UPDATE turns SET binding=? WHERE id=?', (binding, key))
            request_id = secrets.token_hex(16)
            self.db.execute('INSERT INTO requests(id,turn_id) VALUES(?,?)', (request_id, key))
            return request_id

    def finish(self, request_id, delivered):
        with self.lock, self.db:
            if not delivered:
                self.db.execute('UPDATE turns SET uncertain=1 WHERE id=(SELECT turn_id FROM requests WHERE id=?)', (request_id,))
            self.db.execute('DELETE FROM requests WHERE id=?', (request_id,))

    def maintenance_record(self):
        path = self.directory / 'maintenance.json'
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError:
            raise MaintenanceError('The maintenance marker is unavailable or unsafe. Inference remains paused.') from None
        try:
            with os.fdopen(descriptor) as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise ValueError('unsafe marker')
                raw = handle.read(16385)
            if len(raw) > 16384:
                raise ValueError('oversized marker')
            value = json.loads(raw)
            fields = {'schema', 'id', 'from_runtime', 'to_runtime', 'configuration_revision', 'required_models', 'phase'}
            if not isinstance(value, dict) or not fields <= set(value) or not set(value) <= fields | {'boot_id'}:
                raise ValueError('marker fields')
            if type(value['schema']) is not int or value['schema'] != 1 or value['phase'] not in ('prepared', 'committed'):
                raise ValueError('marker version or phase')
            uuid.UUID(value['id'])
            for field in ('from_runtime', 'to_runtime', 'configuration_revision'):
                if not isinstance(value[field], str) or not re.fullmatch('[a-f0-9]{64}', value[field]):
                    raise ValueError('marker digest')
            models = value['required_models']
            if (not isinstance(models, list) or not 1 <= len(models) <= 8
                    or any(not isinstance(model, str) or len(model) > 512
                           or not re.fullmatch(r'harbor/(azure|openrouter)/[^\s]+', model) for model in models)
                    or len(set(models)) != len(models)):
                raise ValueError('marker models')
            if value['phase'] == 'committed' or 'boot_id' in value:
                uuid.UUID(value['boot_id'])
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise MaintenanceError('The maintenance marker is invalid or unsafe. Inference remains paused.') from None
        return value if value['to_runtime'] == self.runtime_id else None

    def maintenance_state(self, revision):
        try:
            marker = self.maintenance_record()
        except MaintenanceError:
            return {'id': None, 'blocked': True, 'phase': 'invalid'}
        if marker is None:
            return {'id': None, 'blocked': False, 'phase': None}
        opened = (marker['phase'] == 'committed' and marker.get('boot_id') == self.boot_id
                  and marker['configuration_revision'] == revision)
        return {'id': marker['id'], 'blocked': not opened, 'phase': marker['phase']}

    def commit_maintenance(self, maintenance_id, revision, verified_models):
        with self.lock:
            marker = self.maintenance_record()
            if (marker is None or marker['id'] != maintenance_id or marker['configuration_revision'] != revision
                    or not set(marker['required_models']) <= set(verified_models)):
                raise MaintenanceError('The maintenance context or required route proofs do not match. Maintenance was not committed.')
            if self.db.execute('SELECT 1 FROM requests WHERE finished=0 LIMIT 1').fetchone():
                raise MaintenanceError('Requests are still active. Maintenance cannot be committed.')
            committed = dict(marker, phase='committed', boot_id=self.boot_id)
            descriptor, temporary = tempfile.mkstemp(prefix='.maintenance-', dir=self.directory)
            try:
                with os.fdopen(descriptor, 'w') as handle:
                    json.dump(committed, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                if self.maintenance_record() != marker:
                    raise MaintenanceError('The maintenance marker changed. Maintenance was not committed.')
                os.replace(temporary, self.directory / 'maintenance.json')
                directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return self.maintenance_state(revision)

    def require_tracked_credential_restore(self):
        with self.lock:
            untracked = self.db.execute("SELECT COALESCE(SUM(value),0) FROM counters WHERE name='untracked'").fetchone()[0]
            if untracked:
                raise ValueError('Saved credentials cannot be restored after untracked admissions. Their original account binding is unavailable; existing ownership was preserved.')

    def status(self):
        with self.lock:
            return {'protocol_version': PROTOCOL_VERSION, 'runtime_id': self.runtime_id,
                    'boot_id': self.boot_id, 'mode': 'independent',
                    'unresolved_turns': self.db.execute('SELECT COUNT(*) FROM turns').fetchone()[0],
                    'uncertain_turns': self.db.execute('SELECT COUNT(*) FROM turns WHERE uncertain=1').fetchone()[0],
                    'active_requests': self.db.execute('SELECT COUNT(*) FROM requests WHERE finished=0').fetchone()[0],
                    'recovered_uncertain_requests': self.db.execute('SELECT COUNT(*) FROM requests WHERE finished=1').fetchone()[0],
                    'untracked_admissions': self.db.execute("SELECT COALESCE(SUM(value),0) FROM counters WHERE name='untracked'").fetchone()[0],
                    'desktop_lifecycle_verified': False, 'promotion_allowed': False,
                    'retirement_allowed': False, 'update_block_reason': LIFECYCLE_GATE}

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lease is not None:
            os.close(self.lease)
            self.lease = None
