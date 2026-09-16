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
    available = {entry['slug'] for entry in catalog['models'] if entry['slug'].startswith('harbor/')}
    with open_database(home) as connection:
        row = connection.execute('SELECT * FROM threads WHERE id = ?', (thread_id,)).fetchone()
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
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    record_path.chmod(0o600)
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
        return {'thread_id': plan['thread_id'], 'provider': PROVIDER, 'model': plan['model'],
                'conversation_unchanged': True, 'backup': str(backup)}
    except BaseException:
        if replaced and not committed:
            shutil.copy2(backup_rollout, rollout)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('thread_id', help='Exact UUID from the task URL or local task metadata')
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser())
    parser.add_argument('--apply', action='store_true', help='Apply the repair after quitting Codex')
    args = parser.parse_args()
    try:
        home = args.codex_home.expanduser().resolve()
        plan = task_plan(home, args.thread_id)
        if args.apply:
            result = apply_plan(home, plan)
        else:
            result = {key: value for key, value in plan.items() if key not in ('metadata', 'first_line')}
            result.update({'preview': True, 'codex_is_running': bool(codex_processes())})
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, sqlite3.Error, KeyError) as error:
        parser.exit(1, f'Repair stopped: {error}\n')


if __name__ == '__main__':
    main()
