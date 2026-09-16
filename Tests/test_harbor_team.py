import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('harbor_team', ROOT / 'ModelHarbor/Support/harbor_team.py')
harbor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harbor)


def test_spec():
    return {'models': [{'id': 'example/test-model', 'name': 'Test model', 'context_window': 128000,
                        'input_modalities': ['text'], 'efforts': ['high', 'xhigh'],
                        'default_effort': 'high', 'reasoning_mode': 'effort'}],
            'roles': [{'name': name, 'model': 'example/test-model',
                       'effort': 'high' if name == 'harbor_kimi_coder' else 'xhigh',
                       'kind': 'coder' if name in harbor.CODERS else 'reviewer',
                       'description': name + ' fixed synthetic worker'} for name in harbor.ROLES]}


class HarborTeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='harbor-team-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.data = test_spec()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        harbor.git(self.repo, 'config', 'user.name', 'Harbor Test')
        harbor.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        (self.repo / 'sample.txt').write_text('unchanged\n')
        harbor.git(self.repo, 'add', '.')
        harbor.git(self.repo, 'commit', '-qm', 'test base')
        self.output = self.root / 'team'
        self.prompt = self.root / 'prompt.txt'
        self.prompt.write_text('Synthetic task only.')
        self.token = 'synthetic-private-owner-token-123456789'
        (self.home / 'model-harbor-bridge-token').write_text(self.token)
        (self.home / 'model-harbor-bridge-token').chmod(0o600)
        (self.home / 'model-catalogs').mkdir()
        self.catalog = self.home / 'model-catalogs/model-harbor.json'
        self.catalog.write_text(json.dumps({'models': [{
            'slug': 'harbor/baseten/example/test-model', 'display_name': 'Synthetic',
            'default_reasoning_level': 'high', 'supported_reasoning_levels': [
                {'effort': 'high', 'description': 'High'}, {'effort': 'xhigh', 'description': 'Extra high'}],
            'base_instructions': 'A user-specific catalog instruction that should not be copied.',
            'unexpected_secret': 'private-not-to-copy', 'context_window': 128000}]}))
        (self.home / 'config.toml').write_text('model_catalog_json=' + json.dumps(str(self.catalog)) + '\n[mcp_servers.unrelated]\ncommand="must-not-run"\n')

    def prepare(self):
        harbor.prepare(self.repo, self.output, spec=self.data)
        return self.output / 'team.json'

    def executable(self, body):
        path = self.root / ('fake-codex-' + str(time.time_ns()))
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o700)
        return path

    def run_worker(self, body, timeout=5, name='harbor_glm_coder'):
        if not self.output.exists():
            self.prepare()
        return harbor.run(self.output / 'team.json', name, self.prompt, home=self.home,
                          spec=self.data, codex=self.executable(body), timeout=timeout)

    def test_install_preserves_unowned_file_and_performs_no_partial_overwrite(self):
        agents = self.home / 'agents'
        agents.mkdir()
        custom = agents / 'harbor_glm_coder.toml'
        custom.write_text('name="my_custom_role"\n')
        with self.assertRaisesRegex(ValueError, 'not owned'):
            harbor.install(home=self.home, spec=self.data)
        self.assertEqual(custom.read_text(), 'name="my_custom_role"\n')
        self.assertFalse((agents / 'harbor_kimi_architect.toml').exists())

    def test_install_pins_roles_is_idempotent_and_backs_up_owned_changes(self):
        first = harbor.install(home=self.home, spec=self.data)
        self.assertEqual(first['changed'], 5)
        agents = self.home / 'agents'
        path = agents / 'harbor_glm_coder.toml'
        original = path.read_bytes()
        parsed = tomllib.loads(original.decode())
        self.assertEqual((parsed['model_provider'], parsed['model'], parsed['model_reasoning_effort']),
                         ('model-harbor', 'harbor/baseten/example/test-model', 'xhigh'))
        self.assertNotIn('cwd', parsed)
        self.assertEqual(parsed['model_catalog_json'], str(self.catalog))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(harbor.install(home=self.home, spec=self.data)['changed'], 0)
        self.data['roles'][1]['description'] = 'Updated role description'
        changed = harbor.install(home=self.home, spec=self.data)
        self.assertEqual(changed['changed'], 1)
        self.assertEqual((Path(changed['backup']) / path.name).read_bytes(), original)

    def test_install_project_and_reject_symlink(self):
        result = harbor.install(self.repo, home=self.home, spec=self.data)
        self.assertTrue(result['project_trust_required'])
        self.assertEqual(result['directory'], str(self.repo / '.codex/agents'))
        path = self.repo / '.codex/agents/harbor_glm_coder.toml'
        path.unlink()
        path.symlink_to(self.prompt)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            harbor.install(self.repo, home=self.home, spec=self.data)
        self.assertEqual(self.prompt.read_text(), 'Synthetic task only.')

    def test_preparation_uses_one_immutable_commit_and_independent_worktrees(self):
        manifest = self.prepare()
        team = json.loads(manifest.read_text())
        self.assertEqual(len(team['worktrees']), 4)
        self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
        for record in team['worktrees'].values():
            cwd = Path(record['cwd'])
            self.assertEqual(harbor.git(cwd, 'rev-parse', 'HEAD').stdout.strip(), team['base_commit'])
            (cwd / 'sample.txt').write_text(record['branch'])
        self.assertEqual((self.repo / 'sample.txt').read_text(), 'unchanged\n')
        self.assertEqual(len({(Path(record['cwd']) / 'sample.txt').read_text() for record in team['worktrees'].values()}), 4)

    def test_prepare_rejects_dirty_source_and_existing_output(self):
        (self.repo / 'sample.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'checkout changes'):
            self.prepare()
        self.assertFalse(self.output.exists())
        harbor.git(self.repo, 'checkout', '--', 'sample.txt')
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'new directory'):
            harbor.prepare(self.repo, self.output, spec=self.data)

    def test_partial_prepare_keeps_recovery_manifest_and_created_worktree(self):
        actual = harbor.git
        def fail_second(repo, *args, **kwargs):
            if args[:2] == ('worktree', 'add') and 'harbor_glm_coder' in ' '.join(args):
                raise ValueError('Synthetic worktree creation failure')
            return actual(repo, *args, **kwargs)
        with patch.object(harbor, 'git', side_effect=fail_second):
            with self.assertRaisesRegex(ValueError, 'recoverable'):
                self.prepare()
        team = json.loads((self.output / 'team.json').read_text())
        self.assertEqual(team['status'], 'incomplete')
        self.assertEqual(list(team['worktrees']), ['integration'])
        self.assertTrue((self.output / 'worktrees/integration/sample.txt').exists())

    def test_same_worktree_lock_rejects_duplicate_worker(self):
        self.prepare()
        with harbor.exclusive_lock(self.output / 'locks/harbor_glm_coder.lock'):
            with self.assertRaisesRegex(ValueError, 'Another worker'):
                self.run_worker('print("should not start")')
        self.assertFalse((self.output / 'runs').exists())

    def test_run_uses_fixed_route_safe_environment_and_actual_completed_terminal(self):
        body = '''import json, os, pathlib, sys, tomllib
root=pathlib.Path(os.environ['CODEX_HOME'])
config=tomllib.loads((root/'config.toml').read_text())
assert 'mcp_servers' not in config
assert config['projects'][str(pathlib.Path.cwd())]['trust_level'] == 'untrusted'
assert 'UNRELATED_API_KEY' not in os.environ
assert 'model-harbor' == config['model_provider']
assert config['model_reasoning_effort'] == 'xhigh'
assert config['model_providers']['model-harbor']['env_key'] == 'MODEL_HARBOR_WORKER_AUTH'
catalog=json.loads((root/'catalog.json').read_text())
assert len(catalog['models']) == 1
assert 'unexpected_secret' not in catalog['models'][0]
assert 'user-specific' not in catalog['models'][0]['base_instructions']
assert sys.argv[1:4] == ['exec','--json','--ephemeral']
assert '-C' in sys.argv and sys.argv[sys.argv.index('-C')+1] == str(pathlib.Path.cwd())
assert '--dangerously-bypass-approvals-and-sandbox' not in sys.argv
assert sys.stdin.read() == 'Synthetic task only.'
pathlib.Path('sample.txt').write_text('worker changed this file')
print(json.dumps({'type':'thread.started','thread_id':'synthetic'}))
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Finished synthetic task.'}}))
print(json.dumps({'type':'turn.completed','usage':{}}))
print(os.environ['MODEL_HARBOR_WORKER_AUTH'],file=sys.stderr)
'''
        with patch.dict(os.environ, {'UNRELATED_API_KEY': 'never-copy-this'}):
            result = self.run_worker(body)
        self.assertEqual(result['status'], 'completed')
        self.assertIsNone(result['observed_model'])
        self.assertEqual(result['validation'], 'not_performed_by_launcher')
        artifacts = Path(result['artifacts'])
        self.assertNotIn(self.token, ''.join(p.read_text() for p in artifacts.iterdir()))
        self.assertIn('[REDACTED]', (artifacts / 'stderr.log').read_text())
        self.assertIn('worker changed this file', (artifacts / 'changes.patch').read_text())
        self.assertEqual((self.repo / 'sample.txt').read_text(), 'unchanged\n')
        for path in artifacts.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_empty_or_failed_terminal_never_claims_success(self):
        for body in ('print("not a terminal event")', 'print(\'{"type":"turn.failed"}\')',
                     'import sys\nprint(\'{"type":"turn.completed"}\')\nsys.exit(7)'):
            with self.subTest(body=body):
                result = self.run_worker(body)
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(Path(result['artifacts']).is_dir())

    def test_observed_model_substitution_is_failure(self):
        result = self.run_worker('print(\'{"type":"turn.started","model":"wrong-model"}\')\nprint(\'{"type":"turn.completed"}\')')
        self.assertEqual(result['status'], 'model_mismatch')
        self.assertEqual(result['observed_model'], 'wrong-model')

    def test_architect_and_reviewer_are_read_only_on_integration(self):
        body = 'import sys\nassert sys.argv[sys.argv.index("-s")+1] == "read-only"\nprint(\'{"type":"turn.completed"}\')'
        for name in ('harbor_kimi_architect', 'harbor_reviewer'):
            result = self.run_worker(body, name=name)
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['cwd'], str(self.output / 'worktrees/integration'))

    def test_timeout_terminates_worker_group_and_retains_artifacts(self):
        child_marker = self.root / 'child-survived'
        body = ('import subprocess,sys,time\n'
                'subprocess.Popen([sys.executable,"-c",' + repr('import time,pathlib;time.sleep(1);pathlib.Path(' + repr(str(child_marker)) + ').write_text("bad")') + '])\n'
                'time.sleep(60)\n')
        with patch.object(harbor, 'terminate_group', wraps=harbor.terminate_group) as cleanup:
            result = self.run_worker(body, timeout=.15)
        self.assertEqual(cleanup.call_count, 1)
        self.assertEqual(result['status'], 'timed_out')
        time.sleep(1.2)
        self.assertFalse(child_marker.exists())
        self.assertTrue(Path(result['artifacts']).exists())
        with harbor.exclusive_lock(self.output / 'locks/harbor_glm_coder.lock'):
            pass

    def test_completed_worker_process_group_is_cleaned_up_once(self):
        with patch.object(harbor, 'terminate_group', wraps=harbor.terminate_group) as cleanup:
            result = self.run_worker("print('{\"type\":\"turn.completed\"}')")
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(cleanup.call_count, 1)

    def test_permission_error_for_reaped_group_requires_no_live_members(self):
        for snapshot in ('999 S\n', '321 Z+\n', '321 ZX\n999 S\n'):
            with self.subTest(snapshot=snapshot):
                process = Mock(pid=321)
                process.poll.return_value = 0
                with patch.object(harbor.os, 'killpg', side_effect=PermissionError(1, 'Operation not permitted')), \
                     patch.object(harbor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, snapshot)):
                    harbor.terminate_group(process)
                process.wait.assert_called_once_with(timeout=5)

    def test_zombie_group_before_leader_is_reaped_is_waited_for(self):
        process = Mock(pid=321)
        process.poll.return_value = None
        process.wait.return_value = -signal.SIGTERM
        with patch.object(harbor.os, 'killpg', side_effect=PermissionError(1, 'Operation not permitted')), \
             patch.object(harbor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '321 Z+\n')):
            harbor.terminate_group(process)
        process.wait.assert_called_once_with(timeout=5)

    def test_process_group_permission_failure_is_not_hidden_for_live_or_unknown_group(self):
        for leader_state, snapshot in ((None, '321 S+\n'), (0, '321 S+\n'), (0, ''), (0, 'unrecognized output')):
            with self.subTest(leader_state=leader_state, snapshot=snapshot):
                process = Mock(pid=321)
                process.poll.return_value = leader_state
                with patch.object(harbor.os, 'killpg', side_effect=PermissionError(1, 'Operation not permitted')), \
                     patch.object(harbor.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, snapshot)):
                    with self.assertRaises(PermissionError):
                        harbor.terminate_group(process)
                process.wait.assert_not_called()
        process = Mock(pid=321)
        process.poll.return_value = 0
        with patch.object(harbor.os, 'killpg', side_effect=PermissionError(1, 'Operation not permitted')), \
             patch.object(harbor.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ps', 5)):
            with self.assertRaises(PermissionError):
                harbor.terminate_group(process)

    def test_live_group_cleanup_failure_stops_owned_leader_and_retains_failure_result(self):
        processes = []
        def fail_cleanup(process):
            processes.append(process)
            raise PermissionError(1, 'Operation not permitted')
        with patch.object(harbor, 'terminate_group', side_effect=fail_cleanup) as cleanup:
            result = self.run_worker('import time\ntime.sleep(60)', timeout=.15)
        self.assertEqual(cleanup.call_count, 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['execution_status'], 'timed_out')
        self.assertEqual(result['cleanup']['errors'], ['PermissionError'])
        artifacts = Path(result['artifacts'])
        self.assertEqual(json.loads((artifacts / 'result.json').read_text()), result)
        self.assertTrue((artifacts / 'changes.json').is_file())

    def test_cleanup_failure_cannot_report_completed(self):
        with patch.object(harbor, 'terminate_group', side_effect=PermissionError(1, 'Operation not permitted')):
            result = self.run_worker("print('{\"type\":\"turn.completed\"}')")
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['execution_status'], 'completed')
        self.assertEqual(result['cleanup']['status'], 'failed')

    def test_wrong_branch_and_changed_role_fail_before_process_launch(self):
        self.prepare()
        cwd = self.output / 'worktrees/harbor_glm_coder'
        harbor.git(cwd, 'checkout', '-qb', 'unrelated')
        with self.assertRaisesRegex(ValueError, 'branch changed'):
            self.run_worker('print("never")')
        team = json.loads((self.output / 'team.json').read_text())
        harbor.git(cwd, 'checkout', '-q', team['worktrees']['harbor_glm_coder']['branch'])
        self.data['roles'][1]['effort'] = 'high'
        with self.assertRaisesRegex(ValueError, 'specification changed'):
            self.run_worker('print("never")')

    def test_missing_exact_catalog_entry_refuses(self):
        self.catalog.write_text('{"models":[]}')
        result = self.run_worker('print("never")')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('missing', result['error'])


    def test_unrelated_main_catalog_does_not_change_worker_or_role_catalog(self):
        (self.home / 'config.toml').write_text('model_catalog_json="/nonexistent/native-models.json"\n')
        result = self.run_worker('print(\'{"type":"turn.completed"}\')')
        self.assertEqual(result['status'], 'completed')
        harbor.install(home=self.home, spec=self.data)
        role = tomllib.loads((self.home / 'agents/harbor_glm_coder.toml').read_text())
        self.assertEqual(role['model_catalog_json'], str(self.catalog))

    def test_world_readable_bridge_token_is_refused(self):
        (self.home / 'model-harbor-bridge-token').chmod(0o644)
        result = self.run_worker('print("never")')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('private regular file', result['error'])



    def test_change_artifacts_include_untracked_text_binary_and_symlink_without_staging(self):
        manifest = self.prepare()
        team = json.loads(manifest.read_text())
        cwd = self.output / 'worktrees/harbor_glm_coder'
        (cwd / 'new.txt').write_text('new source file\n')
        (cwd / 'assets').mkdir()
        (cwd / 'assets/data.bin').write_bytes(b'\x00\xff\x10')
        (cwd / 'link.txt').symlink_to('new.txt')
        (cwd / '.gitignore').write_text('ignored-secret\n')
        (cwd / 'ignored-secret').write_text('not part of the proposed change')
        artifacts = self.root / 'artifacts'
        artifacts.mkdir()
        index_before = harbor.git(cwd, 'ls-files', '--stage', '-z').stdout
        summary = harbor.capture_changes(cwd, artifacts, team['base_commit'])
        self.assertEqual(summary['status'], 'complete')
        changes = json.loads((artifacts / 'changes.json').read_text())
        files = {entry['path']: entry for entry in changes['untracked']}
        self.assertEqual((artifacts / files['new.txt']['artifact']).read_text(), 'new source file\n')
        self.assertEqual((artifacts / files['assets/data.bin']['artifact']).read_bytes(), b'\x00\xff\x10')
        self.assertEqual(files['link.txt']['target'], 'new.txt')
        self.assertEqual(files['link.txt']['capture'], 'metadata_only')
        self.assertNotIn('ignored-secret', files)
        self.assertIn('ignored files are excluded', changes['untracked_scope'])
        self.assertEqual(harbor.git(cwd, 'ls-files', '--stage', '-z').stdout, index_before)

    def test_untracked_snapshot_limit_is_reported_without_losing_worktree_file(self):
        team = json.loads(self.prepare().read_text())
        cwd = self.output / 'worktrees/harbor_glm_coder'
        original = cwd / 'large.bin'
        original.write_bytes(b'123456789')
        artifacts = self.root / 'limited-artifacts'
        artifacts.mkdir()
        with patch.object(harbor, 'MAX_UNTRACKED_FILE_BYTES', 4):
            summary = harbor.capture_changes(cwd, artifacts, team['base_commit'])
        self.assertEqual(summary['status'], 'partial')
        changes = json.loads((artifacts / 'changes.json').read_text())
        self.assertEqual(changes['untracked'][0]['capture'], 'omitted')
        self.assertIn('size limit', changes['untracked'][0]['reason'])
        self.assertEqual(original.read_bytes(), b'123456789')


if __name__ == '__main__':
    unittest.main()
