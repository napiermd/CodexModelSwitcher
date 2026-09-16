#!/usr/bin/env python3
"""Install fixed Harbor roles and run bounded coding workers in Git worktrees."""
import argparse
from contextlib import ExitStack, contextmanager
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid

SPEC_PATH = Path(__file__).with_name('baseten-models.json')
OWNER = '# Managed by Model Harbor (harbor-team v1).\n'
CODERS = ('harbor_glm_coder', 'harbor_deepseek_coder', 'harbor_kimi_coder')
ROLES = ('harbor_kimi_architect', *CODERS, 'harbor_reviewer')
MAX_UNTRACKED_FILE_BYTES = 16 * 1024 * 1024
MAX_UNTRACKED_TOTAL_BYTES = 64 * 1024 * 1024
CATALOG_FIELDS = {
    'slug', 'display_name', 'description', 'visibility', 'supported_in_api', 'priority',
    'default_reasoning_level', 'supported_reasoning_levels', 'shell_type', 'context_window',
    'max_context_window', 'input_modalities', 'support_verbosity', 'supports_parallel_tool_calls',
    'truncation_policy', 'experimental_supported_tools', 'supports_reasoning_summaries',
}


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f'Expected a real directory: {path}')


def atomic_write(path, content):
    raw = content.encode() if isinstance(content, str) else content
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2) + '\n')


def specification(path=None):
    data = json.loads((path or SPEC_PATH).read_text())
    models = {item['id']: item for item in data['models']}
    roles = {item['name']: item for item in data['roles']}
    if not set(ROLES).issubset(roles):
        raise ValueError('The Baseten specification is missing required Harbor roles.')
    for name in ROLES:
        role = roles[name]
        if role['model'] not in models or role['effort'] not in models[role['model']]['efforts']:
            raise ValueError(f'Unsupported model or reasoning effort for {name}.')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+', role['model']):
            raise ValueError(f'Invalid model identifier for {name}.')
    return data


def role_map(spec):
    return {role['name']: role for role in spec['roles'] if role['name'] in ROLES}


def role_model(role):
    return 'harbor/baseten/' + role['model']


def role_instructions(role):
    if role['name'] in CODERS:
        job = ('Implement only the assigned component in your assigned worktree. '
               'Other workers are active in separate worktrees. Do not edit their worktrees or the main checkout. '
               'Run the relevant checks and report changed files, exact validation results, and remaining risks. '
               'Do not merge, push, deploy, or change credentials. Do not claim that a diff was verified unless you ran its checks.')
    elif role['name'] == 'harbor_kimi_architect':
        job = ('Define the architecture and bounded assignments for the three coding roles. '
               'Separate overlapping work, specify validation requirements, and collect worker results. '
               'This role is read-only: propose integration steps and ask the parent task to perform accepted integration. '
               'Do not claim that worker changes are integrated until the parent verifies them.')
    else:
        job = ('Review the proposed changes independently in read-only mode. '
               'Prioritize concrete correctness defects, missing validation, and integration problems. '
               'Give file references and distinguish reproduced problems from unverified concerns.')
    return (job + ' Keep the exact assigned model and reasoning setting. '
            'If the requested model is unavailable, report the error instead of substituting another model. '
            'Follow applicable repository instructions and all Codex-enforced permission and approval policies.')


def role_toml(role, catalog_path=None):
    values = {
        'name': role['name'], 'description': role['description'],
        'model_provider': 'model-harbor', 'model': role_model(role),
        'model_reasoning_effort': role['effort'],
        'model_catalog_json': str(catalog_path or Path.home() / '.codex/model-catalogs/model-harbor.json'),
        'sandbox_mode': 'workspace-write' if role['name'] in CODERS else 'read-only',
        'developer_instructions': role_instructions(role),
    }
    return OWNER + ''.join(f'{key} = {json.dumps(value)}\n' for key, value in values.items())


@contextmanager
def exclusive_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f'Another worker or installer holds {path.name}.') from error
        yield
    finally:
        os.close(fd)


def install(project=None, *, home=None, spec=None):
    spec = spec or specification()
    home = Path(home or Path.home() / '.codex').resolve()
    if project is not None:
        project = Path(project)
        if not project.is_absolute() or not project.is_dir():
            raise ValueError('--project must be an existing absolute directory.')
        destination = project.resolve() / '.codex' / 'agents'
    else:
        destination = home / 'agents'
    private_dir(destination)
    with exclusive_lock(destination / '.harbor-team.install.lock'):
        changes = []
        for role in role_map(spec).values():
            path = destination / (role['name'] + '.toml')
            if path.is_symlink():
                raise ValueError(f'Refusing to replace a symlink: {path}')
            old = path.read_bytes() if path.exists() else None
            new = role_toml(role, home / 'model-catalogs/model-harbor.json').encode()
            if old is not None and not old.startswith(OWNER.encode()):
                raise ValueError(f'Existing agent file is not owned by Model Harbor: {path}')
            if old != new:
                changes.append((path, old, new))
        backup = None
        if any(old is not None for _, old, _ in changes):
            backup = destination / '.model-harbor-backups' / uuid.uuid4().hex
            private_dir(backup)
            for path, old, _ in changes:
                if old is not None:
                    atomic_write(backup / path.name, old)
        for path, old, new in changes:
            current = path.read_bytes() if path.exists() else None
            if current != old or path.is_symlink():
                raise ValueError(f'Agent file changed during installation: {path}')
            atomic_write(path, new)
    return {'status': 'installed', 'directory': str(destination),
            'changed': len(changes), 'backup': str(backup) if backup else None,
            'roles': list(role_map(spec)),
            'project_trust_required': project is not None}


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def prepare(repo, output, *, spec=None):
    spec = spec or specification()
    repo = Path(repo).resolve()
    top = Path(git(repo, 'rev-parse', '--show-toplevel').stdout.strip()).resolve()
    if repo != top:
        raise ValueError('--repo must identify the Git checkout root.')
    if git(repo, 'status', '--porcelain=v1', '--untracked-files=all').stdout:
        raise ValueError('Commit or otherwise resolve checkout changes before preparing a team.')
    base = git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').stdout.strip()
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('--output must be a new directory; existing team data is never replaced.')
    if output == repo or repo in output.parents:
        raise ValueError('Place the team directory outside the source checkout.')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700)
    output = output.resolve()
    private_dir(output / 'worktrees')
    team_id = uuid.uuid4().hex[:12]
    team = {'version': 1, 'id': team_id, 'root': str(output), 'repo': str(repo),
            'base_commit': base, 'status': 'preparing', 'roles': list(role_map(spec).values()),
            'worktrees': {}, 'created_at': datetime.now(timezone.utc).isoformat()}
    manifest = output / 'team.json'
    write_json(manifest, team)
    try:
        for name in ('integration', *CODERS):
            branch = f'harbor/team-{team_id}/{name}'
            cwd = output / 'worktrees' / name
            git(repo, 'worktree', 'add', '-b', branch, str(cwd), base)
            team['worktrees'][name] = {'cwd': str(cwd), 'branch': branch, 'base_commit': base}
            write_json(manifest, team)
        team['status'] = 'ready'
        write_json(manifest, team)
    except Exception as error:
        team['status'] = 'incomplete'
        team['recovery'] = ('Keep this directory. Inspect git worktree list and the recorded paths; '
                            'no worktree or branch was deleted automatically.')
        write_json(manifest, team)
        raise ValueError(f'Team preparation stopped; recoverable state is in {manifest}.') from error
    return {'status': 'ready', 'team': str(manifest), 'base_commit': base,
            'worktrees': team['worktrees']}


def validate_team(team_file, name, spec):
    team_file = Path(team_file).resolve()
    team = json.loads(team_file.read_text())
    root = Path(team['root']).resolve()
    if team.get('version') != 1 or team.get('status') != 'ready' or team_file != root / 'team.json':
        raise ValueError('The team manifest is not a ready Harbor team at its recorded location.')
    expected = role_map(spec)
    saved = {role['name']: role for role in team['roles']}
    if name not in expected or name not in saved:
        raise ValueError('Unknown Harbor role.')
    for field in ('model', 'effort'):
        if saved[name][field] != expected[name][field]:
            raise ValueError('The team model specification changed. Prepare a new team; no substitution was made.')
    key = name if name in CODERS else 'integration'
    record = team['worktrees'][key]
    cwd = Path(record['cwd']).resolve()
    if cwd != root / 'worktrees' / key or Path(record['cwd']).is_symlink():
        raise ValueError('The worktree path does not match this team.')
    if Path(git(cwd, 'rev-parse', '--show-toplevel').stdout.strip()).resolve() != cwd:
        raise ValueError('The recorded path is not its own worktree root.')
    if git(cwd, 'branch', '--show-current').stdout.strip() != record['branch']:
        raise ValueError('The worktree branch changed; refusing to run on another branch.')
    for location in (Path(team['repo']), cwd):
        common = Path(git(location, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()).resolve()
        if location == Path(team['repo']):
            source_common = common
        elif common != source_common:
            raise ValueError('The worktree is attached to a different repository.')
    if not re.fullmatch('[0-9a-f]{40,64}', team['base_commit']):
        raise ValueError('Invalid team base commit.')
    if record['base_commit'] != team['base_commit'] or git(cwd, 'merge-base', '--is-ancestor', team['base_commit'], 'HEAD', check=False).returncode:
        raise ValueError('The worktree no longer descends from the recorded base commit.')
    return team, expected[name], cwd, key


def worker_config(home, temporary, role):
    catalog_path = home / 'model-catalogs/model-harbor.json'
    entries = json.loads(catalog_path.read_text())['models']
    entry = next((entry for entry in entries if entry.get('slug') == role_model(role)), None)
    if entry is None:
        raise ValueError('The exact worker model is missing from the installed Harbor catalog. Update Model Harbor first.')
    supported = {item['effort'] for item in entry.get('supported_reasoning_levels', [])}
    if role['effort'] not in supported:
        raise ValueError('The installed catalog does not support the fixed worker reasoning effort.')
    safe = {key: copy.deepcopy(value) for key, value in entry.items() if key in CATALOG_FIELDS}
    safe['base_instructions'] = 'You are a coding assistant. Follow the user instructions, use available tools, and verify results.'
    safe['default_reasoning_level'] = role['effort']
    write_json(temporary / 'catalog.json', {'models': [safe]})
    sandbox = 'workspace-write' if role['name'] in CODERS else 'read-only'
    values = {
        'model': role_model(role), 'model_provider': 'model-harbor',
        'model_reasoning_effort': role['effort'], 'model_catalog_json': str(temporary / 'catalog.json'),
        'sandbox_mode': sandbox, 'developer_instructions': role_instructions(role),
    }
    text = ''.join(f'{key} = {json.dumps(value)}\n' for key, value in values.items())
    text += '''
[agents]
enabled = false
[features]
apps = false
[model_providers.model-harbor]
name = "Model Harbor worker"
base_url = "http://127.0.0.1:48118/harbor/v1"
wire_api = "responses"
env_key = "MODEL_HARBOR_WORKER_AUTH"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
stream_idle_timeout_ms = 900000
'''
    atomic_write(temporary / 'config.toml', text)
    token_path = home / 'model-harbor-bridge-token'
    if token_path.is_symlink() or token_path.stat().st_mode & 0o077:
        raise ValueError('Harbor bridge token must be a private regular file.')
    token = token_path.read_text().strip()
    if len(token) < 16 or '\n' in token:
        raise ValueError('The local Harbor bridge token is invalid.')
    return token, sandbox


def capture_changes(cwd, artifacts, base):
    atomic_write(artifacts / 'changes.patch', git(cwd, 'diff', '--binary', '--no-ext-diff', '--no-textconv', base, '--').stdout)
    atomic_write(artifacts / 'worktree-status.txt', git(cwd, 'status', '--porcelain=v1', '--untracked-files=all').stdout)
    manifest = {'base_commit': base, 'tracked_patch': 'changes.patch',
                'tracked_patch_scope': 'Tracked changes relative to the team base, including binary changes; excludes untracked files.',
                'untracked_scope': 'Non-ignored files listed by git ls-files --others --exclude-standard; ignored files are excluded.',
                'untracked': [], 'status': 'complete'}
    total = 0
    paths = git(cwd, 'ls-files', '--others', '--exclude-standard', '-z', '--').stdout.split('\0')
    for relative in filter(None, paths):
        source = cwd / relative
        item = {'path': relative}
        manifest['untracked'].append(item)
        try:
            if Path(relative).is_absolute() or '..' in Path(relative).parts or not source.parent.resolve().is_relative_to(cwd):
                raise ValueError('Path escaped the worktree; contents were not read.')
            metadata = source.lstat()
            item['mode'] = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                item.update(kind='symlink', target=os.readlink(source), capture='metadata_only')
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError('Special file retained in worktree; contents were not read.')
            item.update(kind='file', bytes=metadata.st_size)
            if metadata.st_size > MAX_UNTRACKED_FILE_BYTES or total + metadata.st_size > MAX_UNTRACKED_TOTAL_BYTES:
                raise ValueError('Snapshot size limit reached; original file remains in the worktree.')
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as input_file:
                before = os.fstat(input_file.fileno())
                data = input_file.read(MAX_UNTRACKED_FILE_BYTES + 1)
                after = os.fstat(input_file.fileno())
            stable = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if stable(before) != stable(after) or stable(before) != stable(metadata) or len(data) != before.st_size:
                raise ValueError('File changed during snapshot; original file remains in the worktree.')
            if len(data) > MAX_UNTRACKED_FILE_BYTES or total + len(data) > MAX_UNTRACKED_TOTAL_BYTES:
                raise ValueError('Snapshot size limit reached; original file remains in the worktree.')
            destination = artifacts / 'untracked-files' / relative
            private_dir(destination.parent)
            atomic_write(destination, data)
            total += len(data)
            item.update(capture='copied', artifact=str(destination.relative_to(artifacts)),
                        sha256=hashlib.sha256(data).hexdigest())
        except (OSError, ValueError) as error:
            item.update(capture='omitted', reason=str(error))
            manifest['status'] = 'partial'
    write_json(artifacts / 'changes.json', manifest)
    return {'manifest': 'changes.json', 'status': manifest['status'],
            'untracked_count': len(manifest['untracked']), 'snapshot_bytes': total}


def signal_group(process, signum):
    try:
        os.killpg(process.pid, signum)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS may report EPERM for zombies before waitpid can reap the leader.
        # Check every group member; an exited leader can still have live children.
        try:
            snapshot = subprocess.run(['/bin/ps', '-axo', 'pgid=,stat='], check=True,
                                      capture_output=True, text=True, timeout=5)
            members = [line.split() for line in snapshot.stdout.splitlines() if line.strip()]
            valid = members and all(len(fields) == 2 and fields[0].isdigit() for fields in members)
            if valid and not any(int(group) == process.pid and state[0] not in ('Z', 'X')
                                 for group, state in members):
                return False
        except (OSError, subprocess.SubprocessError):
            pass
        raise


def terminate_group(process):
    if not signal_group(process, signal.SIGTERM):
        process.wait(timeout=5)
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        process.poll()
        if not signal_group(process, 0):
            break
        time.sleep(.02)
    else:
        signal_group(process, signal.SIGKILL)
    process.wait(timeout=5)


class Cancelled(Exception):
    pass


def run(team_file, name, prompt_file, *, timeout=1800, home=None, spec=None, codex=None):
    if timeout <= 0 or timeout > 86400:
        raise ValueError('Timeout must be greater than zero and at most 86400 seconds.')
    spec = spec or specification()
    team, role, cwd, key = validate_team(team_file, name, spec)
    home = Path(home or Path.home() / '.codex').resolve()
    prompt_path = Path(prompt_file)
    if prompt_path.stat().st_size > 262144:
        raise ValueError('Worker prompt exceeds the 256 KiB limit.')
    prompt = prompt_path.read_text()
    if not prompt.strip():
        raise ValueError('Worker prompt is empty.')
    codex = codex or shutil.which('codex')
    if not codex:
        raise ValueError('Install Codex CLI and place it on PATH first.')
    root = Path(team['root'])
    with exclusive_lock(root / 'locks' / (key + '.lock')):
        private_dir(root / 'runs')
        artifacts = Path(tempfile.mkdtemp(prefix=name + '-', dir=root / 'runs'))
        result = {'role': name, 'requested_model': role_model(role), 'requested_effort': role['effort'],
                  'cwd': str(cwd), 'status': 'starting', 'observed_model': None,
                  'observed_effort': None, 'artifacts': str(artifacts), 'validation': 'not_performed_by_launcher'}
        write_json(artifacts / 'result.json', result)
        process = None
        readers = []
        cleanup_errors = []
        original_handlers = {}
        terminal = []
        try:
            with ExitStack() as cleanup:
                temporary_name = cleanup.enter_context(tempfile.TemporaryDirectory(prefix='model-harbor-worker-'))
                temporary = Path(temporary_name).resolve()
                token, sandbox = worker_config(home, temporary, role)
                # Project instructions remain visible, but project config must not redirect this fixed route or add MCP servers.
                config_path = temporary / 'config.toml'
                trust = ''.join('\n[projects.' + json.dumps(str(path)) + ']\ntrust_level = "untrusted"\n'
                                for path in dict.fromkeys((cwd, Path(team['repo']))))
                atomic_write(config_path, config_path.read_text() + trust)
                env = {key: value for key, value in os.environ.items()
                       if key in ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TERM', 'SHELL')}
                env.update(CODEX_HOME=str(temporary), MODEL_HARBOR_WORKER_AUTH=token)
                command = [str(codex), 'exec', '--json', '--ephemeral', '--color', 'never',
                           '-C', str(cwd), '-m', role_model(role), '-s', sandbox,
                           '-c', 'model_provider="model-harbor"',
                           '-c', 'model_reasoning_effort=' + json.dumps(role['effort']), '-']
                process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True,
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding='utf-8', errors='replace')
                def cleanup_process():
                    try:
                        terminate_group(process)
                    except Exception as error:
                        cleanup_errors.append(type(error).__name__)
                        # The owned leader can still be stopped even when group signaling fails.
                        # This is incomplete group cleanup and can never produce a completed result.
                        if process.poll() is None:
                            try:
                                process.kill()
                                process.wait(timeout=5)
                            except Exception as leader_error:
                                cleanup_errors.append(type(leader_error).__name__)
                cleanup.callback(cleanup_process)

                def collect(stream, filename, events=False):
                    fd = os.open(artifacts / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, 'w') as output:
                        for line in stream:
                            output.write(line.replace(token, '[REDACTED]'))
                            output.flush()
                            if not events:
                                continue
                            try:
                                event = json.loads(line)
                            except ValueError:
                                continue
                            if event.get('type') in ('turn.completed', 'turn.failed'):
                                terminal.append(event['type'])
                            if event.get('type') in ('thread.started', 'turn.started', 'turn_context'):
                                metadata = event.get('payload', event)
                                if isinstance(metadata.get('model'), str):
                                    result['observed_model'] = metadata['model']
                                if isinstance(metadata.get('reasoning_effort'), str):
                                    result['observed_effort'] = metadata['reasoning_effort']
                    stream.close()

                for stream, filename, events in ((process.stdout, 'events.jsonl', True), (process.stderr, 'stderr.log', False)):
                    reader = threading.Thread(target=collect, args=(stream, filename, events), daemon=True)
                    reader.start()
                    readers.append(reader)
                if threading.current_thread() is threading.main_thread():
                    def cancel(_signum, _frame):
                        raise Cancelled('Worker cancelled.')
                    for signum in (signal.SIGTERM, signal.SIGINT):
                        original_handlers[signum] = signal.getsignal(signum)
                        signal.signal(signum, cancel)
                def feed_prompt():
                    try:
                        process.stdin.write(prompt)
                    except (BrokenPipeError, OSError):
                        pass
                    finally:
                        process.stdin.close()
                feeder = threading.Thread(target=feed_prompt, daemon=True)
                feeder.start()
                process.wait(timeout=timeout)
                feeder.join(timeout=5)
                for reader in readers:
                    reader.join(timeout=10)
                if any(reader.is_alive() for reader in readers):
                    raise RuntimeError('Worker log streams did not close.')
                result['exit_code'] = process.returncode
                result['status'] = 'completed' if process.returncode == 0 and terminal and terminal[-1] == 'turn.completed' else 'failed'
                if result['observed_model'] not in (None, role_model(role)) or result['observed_effort'] not in (None, role['effort']):
                    result['status'] = 'model_mismatch'
                if result['status'] == 'failed':
                    result['error'] = 'Codex did not exit successfully with a completed terminal turn. Inspect the private logs.'
        except subprocess.TimeoutExpired:
            result['status'] = 'timed_out'
            result['error'] = 'Worker exceeded its configured timeout.'
        except (Cancelled, KeyboardInterrupt):
            result['status'] = 'cancelled'
            result['error'] = 'Worker was cancelled; its worktree and logs were retained.'
        except Exception as error:
            result['status'] = 'failed'
            # Network/process exceptions may contain sensitive text. Keep the public failure bounded.
            result['error'] = str(error) if isinstance(error, ValueError) else f'Worker setup or execution failed ({type(error).__name__}).'
        finally:
            if process is not None:
                for reader in readers:
                    reader.join(timeout=5)
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            for signum, handler in original_handlers.items():
                signal.signal(signum, handler)
            if cleanup_errors:
                result['execution_status'] = result['status']
                result['status'] = 'failed'
                result['error'] = 'Worker process-group cleanup could not be verified (' + ', '.join(cleanup_errors) + ').'
                result['cleanup'] = {'status': 'failed', 'errors': cleanup_errors}
            try:
                result['changes'] = capture_changes(cwd, artifacts, team['base_commit'])
            except Exception:
                result['diff_capture'] = 'failed'
            write_json(artifacts / 'result.json', result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    installer = commands.add_parser('install', help='Install owned native Codex agent TOML definitions.')
    installer.add_argument('--project', type=Path)
    preparer = commands.add_parser('prepare', help='Prepare three coding worktrees and one integration worktree.')
    preparer.add_argument('--repo', type=Path, required=True)
    preparer.add_argument('--output', type=Path, required=True)
    runner = commands.add_parser('run', help='Run one fixed worker; run the three coding roles concurrently if desired.')
    runner.add_argument('--team', type=Path, required=True)
    runner.add_argument('--role', choices=ROLES, required=True)
    runner.add_argument('--prompt-file', type=Path, required=True)
    runner.add_argument('--timeout', type=float, default=1800)
    args = parser.parse_args(argv)
    try:
        if args.command == 'install':
            result = install(args.project)
        elif args.command == 'prepare':
            result = prepare(args.repo, args.output)
        else:
            result = run(args.team, args.role, args.prompt_file, timeout=args.timeout)
        print(json.dumps(result, indent=2))
        return 0 if result['status'] in ('installed', 'ready', 'completed') else 1
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
