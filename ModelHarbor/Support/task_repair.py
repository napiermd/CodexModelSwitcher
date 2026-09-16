#!/usr/bin/env python3
"""Repair one legacy OpenAI task that selected a Harbor model. Codex must be closed.

Preview is the default. Apply backs up the complete rollout and routing metadata,
then changes only the task's saved provider while preserving its selected Harbor model.
No credentials are read and no model or MCP requests are made.
"""
import argparse
import copy
from contextlib import contextmanager
import hashlib
import fcntl
import threading
import time
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import tomllib
import uuid


PROVIDER = 'model-harbor'


def codex_processes():
    result = subprocess.run(['ps', '-axo', 'uid=,pid=,comm='], check=True,
                            capture_output=True, text=True)
    processes = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3 or parts[0] != str(os.getuid()):
            continue
        executable = Path(parts[2]).name
        if executable in ('codex', 'Codex', 'ChatGPT'):
            processes.append(int(parts[1]))
    return processes


@contextmanager
def open_database(home, writable=False):
    database = home / 'state_5.sqlite'
    connection = sqlite3.connect(database.as_uri() + ('?mode=rw' if writable else '?mode=ro'),
                                 uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def task_plan(home, thread_id):
    if str(uuid.UUID(thread_id)) != thread_id:
        raise ValueError('Use the exact task UUID.')
    config = tomllib.loads((home / 'config.toml').read_text())
    if PROVIDER not in config.get('model_providers', {}):
        raise ValueError('Set up Model Harbor before repairing a task.')
    if config.get('sqlite_home') and Path(config['sqlite_home']).expanduser().resolve() != home:
        raise ValueError('A custom SQLite location needs a separate repair; nothing was changed.')
    catalog_path = Path(config.get('model_catalog_json', ''))
    catalog = json.loads(catalog_path.read_text())
    available = {entry['slug'] for entry in catalog['models'] if entry['slug'].startswith('harbor/') or entry['slug'] == 'harbor-selected'}
    with open_database(home) as connection:
        row = connection.execute('SELECT id, model_provider, model, rollout_path FROM threads WHERE id = ?', (thread_id,)).fetchone()
    if row is None:
        raise ValueError('Task not found in this Codex home.')
    if row['model_provider'] not in ('openai', PROVIDER):
        raise ValueError('This repair only supports tasks saved with the OpenAI provider.')
    selected = row['model']
    if selected not in available:
        raise ValueError('This task has not selected an installed harbor/... model; repair was refused.')
    rollout = Path(row['rollout_path']).resolve(strict=True)
    if not any(rollout.is_relative_to(home / folder) for folder in ('sessions', 'archived_sessions')):
        raise ValueError('The rollout is outside this Codex home; repair was refused.')
    with rollout.open('rb') as handle:
        first = handle.readline(4 * 1024 * 1024)
    metadata = json.loads(first)
    payload = metadata.get('payload', {})
    if (metadata.get('type') != 'session_meta' or payload.get('id') != thread_id
            or payload.get('model_provider') != row['model_provider']):
        raise ValueError('The rollout and task index disagree; repair was refused.')
    if payload.get('history_mode', 'legacy') not in ('legacy', 'paginated'):
        raise ValueError('Unsupported task history format; repair was refused.')
    if row['model_provider'] == PROVIDER:
        return {'thread_id': thread_id, 'already_repaired': True}
    return {'thread_id': thread_id, 'already_repaired': False,
            'previous_provider': row['model_provider'], 'previous_model': row['model'],
            'provider': PROVIDER, 'model': selected, 'rollout': str(rollout),
            'rollout_bytes': rollout.stat().st_size, 'metadata': metadata,
            'first_line': first}


def digest(path, skip_metadata=False):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        if skip_metadata:
            handle.readline()
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def apply_plan(home, plan, process_check=codex_processes):
    if plan['already_repaired']:
        return {'already_repaired': True, 'thread_id': plan['thread_id']}
    if process_check():
        raise ValueError('Quit Codex/ChatGPT and any Codex CLI sessions, then rerun this command. '
                         'An open task retains its old provider in memory. Nothing was changed.')
    current = task_plan(home, plan['thread_id'])
    if current != plan:
        raise ValueError('The task changed after the preview; run the preview again.')
    rollout = Path(plan['rollout'])
    backups = home / 'model-harbor-task-backups'
    backups.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=plan['thread_id'] + '-', dir=backups))
    backup_rollout = backup / rollout.name
    shutil.copy2(rollout, backup_rollout)
    backup_rollout.chmod(0o600)
    original_digest = digest(backup_rollout)
    if digest(rollout) != original_digest:
        raise ValueError('The task changed while making its backup. Nothing was changed.')
    record = {key: value for key, value in plan.items() if key not in ('first_line', 'metadata')}
    record['rollout_sha256'] = original_digest
    record['conversation_sha256'] = digest(backup_rollout, skip_metadata=True)
    record_path = backup / 'routing.json'
    record['repair_state'] = 'prepared'
    private_json(record_path, record)
    updated = copy.deepcopy(plan['metadata'])
    updated['payload']['model_provider'] = PROVIDER
    temporary = None
    replaced = False
    committed = False
    try:
        with tempfile.NamedTemporaryFile(dir=rollout.parent, prefix='.harbor-repair-', delete=False) as out:
            temporary = Path(out.name)
            out.write(json.dumps(updated, ensure_ascii=False, separators=(',', ':')).encode() + b'\n')
            with backup_rollout.open('rb') as source:
                source.readline()
                shutil.copyfileobj(source, out)
            out.flush()
            os.fsync(out.fileno())
        if digest(temporary, skip_metadata=True) != record['conversation_sha256']:
            raise ValueError('Conversation verification failed. Nothing was changed.')
        record['repaired_sha256'] = digest(temporary)
        private_json(record_path, record)
        with open_database(home, writable=True) as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT model_provider, model, rollout_path FROM threads WHERE id = ?',
                                     (plan['thread_id'],)).fetchone()
            if (process_check() or row is None or row['model_provider'] != plan['previous_provider']
                    or row['model'] != plan['previous_model'] or Path(row['rollout_path']).resolve() != rollout
                    or digest(rollout) != original_digest):
                raise ValueError('Codex reopened or the task changed. Nothing was changed.')
            connection.execute('UPDATE threads SET model_provider = ?, model = ? WHERE id = ?',
                               (PROVIDER, plan['model'], plan['thread_id']))
            os.replace(temporary, rollout)
            replaced = True
            connection.commit()
            committed = True
        record['repair_state'] = 'complete'
        private_json(record_path, record)
        return {'thread_id': plan['thread_id'], 'provider': PROVIDER, 'model': plan['model'],
                'conversation_unchanged': True, 'backup': str(backup)}
    except BaseException:
        if replaced and not committed:
            shutil.copy2(backup_rollout, rollout)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)



class TaskInUse(ValueError):
    pass


def private_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.harbor-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def writer_lease(home, thread_id):
    """Use Codex's own flock namespace, including its lock-file cleanup mutex.

    Tested against Codex 0.150.1. Never unlink a writer lock: other clients may
    have the same inode open. A loaded runtime owns this lock even while idle.
    """
    if str(uuid.UUID(thread_id)) != thread_id:
        raise ValueError('Use the exact task UUID.')
    directory = home / 'thread-writer-locks'
    coordination = directory / '.coordination.lock'
    if not coordination.is_file():
        raise ValueError('Codex task locks are unavailable. Use the offline repair command.')
    with coordination.open('r+b') as mutex:
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TaskInUse('Codex is updating its task locks. Repair will wait.') from error
        try:
            fd = os.open(directory / (thread_id + '.lock'), os.O_RDWR | os.O_CREAT, 0o600)
            lock = os.fdopen(fd, 'r+b')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                lock.close()
                raise TaskInUse('This task is still loaded in Codex. Repair will wait.') from error
        finally:
            fcntl.flock(mutex, fcntl.LOCK_UN)
    try:
        yield
    finally:
        lock.close()


def candidate_ids(home):
    with open_database(home) as connection:
        return [row[0] for row in connection.execute(
            "SELECT id FROM threads WHERE model_provider = 'openai' "
            "AND (model LIKE 'harbor/%' OR model = 'harbor-selected') ORDER BY id")]


def repair_unloaded(home, thread_id):
    with writer_lease(home, thread_id):
        # The per-task lease replaces the coarse all-processes-stopped guard.
        # It stays held through backup, file publication, index commit and report.
        return apply_plan(home, task_plan(home, thread_id), process_check=lambda: [])


def recover_pending(home):
    """Finish an interrupted file/index commit only when both exact states match."""
    results = []
    for path in sorted((home / 'model-harbor-task-backups').glob('*/routing.json')):
        record = json.loads(path.read_text())
        if record.get('repair_state') != 'prepared':
            continue
        thread_id = record['thread_id']
        with writer_lease(home, thread_id):
            rollout = Path(record['rollout']).resolve(strict=True)
            if not any(rollout.is_relative_to(home / folder) for folder in ('sessions', 'archived_sessions')):
                raise ValueError('An interrupted repair references a different Codex home.')
            with open_database(home, writable=True) as connection:
                connection.execute('BEGIN IMMEDIATE')
                row = connection.execute('SELECT model_provider, model, rollout_path FROM threads WHERE id=?',
                                         (thread_id,)).fetchone()
                if row is None or row['model'] != record['model'] or Path(row['rollout_path']).resolve() != rollout:
                    raise ValueError('An interrupted repair changed. Keep its backup for recovery.')
                current = digest(rollout)
                if current == record['rollout_sha256'] and row['model_provider'] == record['previous_provider']:
                    record['repair_state'] = 'rolled_back'
                elif (current == record.get('repaired_sha256')
                      and row['model_provider'] in (record['previous_provider'], PROVIDER)):
                    connection.execute('UPDATE threads SET model_provider=? WHERE id=?', (PROVIDER, thread_id))
                    record['repair_state'] = 'complete'
                    results.append({'thread_id': thread_id, 'recovered': True})
                else:
                    raise ValueError('An interrupted repair changed. Keep its backup for recovery.')
                connection.commit()
            private_json(path, record)
    return results


class RepairMonitor:
    """Opt-in background repair; never unloads a task or touches credentials."""
    def __init__(self, home):
        self.home = home
        self.settings = home / 'model-harbor-repairs.json'
        self.report = home / 'model-harbor-repair-report.json'
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._wake = threading.Event()
        self._snapshot = {'enabled': False, 'state': 'checking', 'pending': 0, 'repaired': 0}
        self._failed = False
        try:
            self._snapshot['enabled'] = json.loads(self.settings.read_text()).get('enabled') is True
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self._failed = True
            self._snapshot['state'] = 'error'

    @property
    def snapshot(self):
        with self._lock:
            return dict(self._snapshot)

    def set_enabled(self, enabled):
        private_json(self.settings, {'enabled': enabled})
        with self._lock:
            self._snapshot['enabled'] = enabled
            self._failed = False
        self._wake.set()

    def run_once(self):
        if not self._run_lock.acquire(blocking=False):
            return
        try:
            self._run_once()
        except (ValueError, OSError, sqlite3.Error, KeyError):
            # Reporting can fail too, e.g. a full disk. Keep the worker alive so
            # an explicit retry can recover after storage becomes writable.
            with self._lock:
                self._snapshot['state'] = 'error'
                self._failed = True
        finally:
            self._run_lock.release()

    def _run_once(self):
        with self._lock:
            enabled = self._snapshot['enabled']
            if self._failed:
                return
        completed = []
        failures = []
        try:
            if enabled:
                try:
                    completed.extend(recover_pending(self.home))
                except TaskInUse:
                    pass
            ids = candidate_ids(self.home)
            for thread_id in ids:
                with self._lock:
                    if enabled and not self._snapshot['enabled']:
                        break
                try:
                    if enabled:
                        completed.append(repair_unloaded(self.home, thread_id))
                    else:
                        task_plan(self.home, thread_id)
                except TaskInUse:
                    continue
                except (ValueError, OSError, sqlite3.Error, KeyError) as error:
                    failures.append({'thread_id': thread_id, 'error': str(error)})
            remaining = len(candidate_ids(self.home))
            with self._lock:
                self._snapshot.update(state='error' if failures else 'waiting' if remaining else 'ready', pending=remaining)
                self._snapshot['repaired'] += len(completed)
                self._failed = bool(failures)
            if completed or failures:
                private_json(self.report, {'checked_at': time.time(), 'completed': completed,
                                          'failures': failures, 'pending': remaining})
        except (ValueError, OSError, sqlite3.Error, KeyError) as error:
            with self._lock:
                self._snapshot['state'] = 'error'
                self._failed = True
            private_json(self.report, {'checked_at': time.time(), 'failures': [{'error': str(error)}]})

    def start(self):
        def watch():
            while True:
                self._wake.clear()
                self.run_once()
                self._wake.wait(10)
        threading.Thread(target=watch, daemon=True, name='harbor-task-repairs').start()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('thread_id', nargs='?', help='Exact task UUID')
    parser.add_argument('--all', action='store_true', help='Find all incompatible Harbor task routes')
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser())
    parser.add_argument('--apply', action='store_true', help='Apply after quitting Codex')
    parser.add_argument('--unloaded', action='store_true', help='Apply only with the Codex per-task writer lock')
    args = parser.parse_args()
    if bool(args.thread_id) == bool(args.all):
        parser.error('Provide one task UUID or --all.')
    if args.unloaded and not args.apply:
        parser.error('--unloaded requires --apply.')
    try:
        home = args.codex_home.expanduser().resolve()
        ids = candidate_ids(home) if args.all else [args.thread_id]
        results = []
        for thread_id in ids:
            if args.apply and args.unloaded:
                result = repair_unloaded(home, thread_id)
            else:
                plan = task_plan(home, thread_id)
                if args.apply:
                    result = apply_plan(home, plan)
                else:
                    result = {key: value for key, value in plan.items() if key not in ('metadata', 'first_line')}
                    result.update(preview=True, codex_is_running=bool(codex_processes()))
            results.append(result)
        print(json.dumps(results if args.all else results[0], indent=2))
    except (ValueError, OSError, sqlite3.Error, KeyError) as error:
        parser.exit(1, f'Repair stopped: {error}\n')


if __name__ == '__main__':
    main()
