from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/gateway-maintenance.py'
spec = importlib.util.spec_from_file_location('maintenance_controller', SCRIPT)
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)
from gateway_runtime import GatewayRuntime


class OwnershipTransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)
        self.old, self.new = 'a' * 64, 'b' * 64
        self.runtime = GatewayRuntime(self.state, self.old, 'first')
        self.route = {'provider': 'azure', 'model': 'same-deployment'}
        self.runtime.pin('tool-gap-turn', lambda: self.route)
        request = self.runtime.begin('tool-gap-turn', 'same-account-binding')
        self.runtime.finish(request, True)
        self.runtime.pin('uncertain-turn', lambda: self.route)
        request = self.runtime.begin('uncertain-turn', 'same-account-binding')
        self.runtime.finish(request, False)
        self.runtime.pin(None, lambda: self.route)
        self.path = self.state / 'ownership.sqlite'
        self.before = maintenance.journal_snapshot(self.path)
        self.runtime.close()

    def test_same_tool_gap_resumes_without_replay_after_explicit_transfer(self):
        maintenance.migrate_owners(self.path, self.before['turns'], self.old, self.new)
        candidate = GatewayRuntime(self.state, self.new, 'second')
        self.addCleanup(candidate.close)
        self.assertEqual(candidate.pin('tool-gap-turn', lambda: self.fail('Must retain saved route')), self.route)
        request = candidate.begin('tool-gap-turn', 'same-account-binding')
        candidate.finish(request, True)
        self.assertEqual(candidate.status()['untracked_admissions'], 1)
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            candidate.pin('uncertain-turn', lambda: self.fail('Must not replay uncertain work'))
        with self.assertRaisesRegex(ValueError, 'Account or route'):
            candidate.begin('tool-gap-turn', 'different-account-binding')

    def test_transfer_and_rollback_preserve_every_nonruntime_value(self):
        maintenance.migrate_owners(self.path, self.before['turns'], self.old, self.new)
        maintenance.migrate_owners(self.path, self.before['turns'], self.old, self.new, rollback=True)
        maintenance.migrate_owners(self.path, self.before['turns'], self.old, self.new, rollback=True)
        self.assertEqual(maintenance.journal_snapshot(self.path), self.before)

    def test_active_transport_refuses_migration_atomically(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO requests VALUES('active', 'tool-gap-turn', 0)")
        snapshot = maintenance.journal_snapshot(self.path)
        with self.assertRaisesRegex(maintenance.MaintenanceError, 'still active'):
            maintenance.migrate_owners(self.path, self.before['turns'], self.old, self.new)
        self.assertEqual(maintenance.journal_snapshot(self.path), snapshot)

    def test_changed_binding_rolls_back_entire_transaction(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE turns SET binding='other' WHERE id='uncertain-turn'")
        snapshot = maintenance.journal_snapshot(self.path)
        with self.assertRaisesRegex(maintenance.MaintenanceError, 'binding changed'):
            maintenance.migrate_owners(self.path, self.before['turns'], self.old, self.new)
        self.assertEqual(maintenance.journal_snapshot(self.path), snapshot)

    def test_unrelated_owner_is_not_transferred(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO turns VALUES(?,?,?,?,?)', ('unrelated', json.dumps(self.route), 'c' * 64, 'binding', 0))
        snapshot = maintenance.journal_snapshot(self.path)
        maintenance.migrate_owners(self.path, snapshot['turns'], self.old, self.new)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT runtime FROM turns WHERE id='unrelated'").fetchone()[0], 'c' * 64)

    def test_rollback_keeps_new_uncertainty(self):
        maintenance.migrate_owners(self.path, self.before['turns'], self.old, self.new)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE turns SET uncertain=1 WHERE id='tool-gap-turn'")
        maintenance.migrate_owners(self.path, self.before['turns'], self.old, self.new, rollback=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT runtime,uncertain FROM turns WHERE id='tool-gap-turn'").fetchone(), (self.old, 1))

    def test_journal_lease_refuses_a_running_gateway(self):
        runtime = GatewayRuntime(self.state, self.old, 'running')
        self.addCleanup(runtime.close)
        with self.assertRaises(BlockingIOError):
            with maintenance.journal_lease(self.state):
                self.fail('Cannot transfer a live journal')


class ProviderCoverageTests(unittest.TestCase):
    def test_restore_records_and_reuses_the_candidate_revision(self):
        operation = maintenance.Maintenance(Path('/app'), Path('/state'), Path('/config'))
        operation.record = {'configuration_revision': 'old-revision', 'required_models': ['harbor/azure/model']}
        operation.connections = {'azure': {'endpoint': 'https://fixture.test', 'key': 'key'}}
        identity = {'mode': 'independent', 'protocol_version': 1,
                    'runtime_id': 'a' * 64, 'boot_id': 'boot'}
        status = {'configuration_revision': 'candidate-revision'}
        with patch.object(operation, 'status', return_value=(status, identity)), \
                patch.object(operation, 'post', return_value={'configuration_revision': 'candidate-revision'}) as post:
            operation.restore()
            operation.restore()
        self.assertEqual(operation.record['new_configuration_revision'], 'candidate-revision')
        self.assertEqual(post.call_args.args[1]['previous_configuration_revision'], 'old-revision')

        with patch.object(operation, 'status', return_value=(status, identity)), \
                patch.object(operation, 'post', return_value={'configuration_revision': 'different'}):
            with self.assertRaisesRegex(maintenance.MaintenanceError, 'do not reproduce'):
                operation.restore()

    def test_known_self_authenticating_routes_transfer_without_connection_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config'
            config.mkdir()
            (config / 'model-switcher.json').write_text(json.dumps({'services': [{
                'id': 'azure', 'baseURL': 'https://fixture.openai.azure.com/openai/v1',
                'models': [{'id': 'gpt-fixture'}]
            }]}))
            operation = maintenance.Maintenance(root / 'app', root / 'state', config)
            operation.record = {
                'credentials_available': {'azure': True, 'openrouter': False},
                'ownership': {'turns': [
                    ['azure', json.dumps({'provider': 'azure', 'model': 'gpt-fixture'}), 'old', 'binding', 0],
                    ['baseten', json.dumps({'provider': 'baseten', 'model': 'fixture-code'}), 'old', 'binding', 0],
                    ['grok', json.dumps({'provider': 'grok-oauth', 'model': 'grok-fixture'}), 'old', 'binding', 0],
                    ['codex', json.dumps({'provider': 'codex-subscription', 'model': 'gpt-fixture'}), 'old', 'binding', 0],
                ]}
            }
            result = type('Result', (), {'returncode': 0, 'stdout': b'synthetic-key\n'})()
            with patch.object(maintenance.subprocess, 'run', return_value=result):
                operation.keys()
            self.assertEqual(operation.record['required_models'], ['harbor/azure/gpt-fixture'])
            self.assertEqual(operation.record['preserved_unprobed_models'], [
                'harbor/baseten/fixture-code', 'harbor/grok-oauth/grok-fixture',
                'harbor/codex-subscription/gpt-fixture'])

    def test_freeze_preserves_known_self_authenticating_routes_without_live_probe(self):
        operation = maintenance.Maintenance(Path('/app'), Path('/state'), Path('/config'))
        identity = {'mode': 'independent', 'protocol_version': 1,
                    'runtime_id': 'a' * 64, 'boot_id': 'old-boot'}
        snapshot = {'requests': [], 'counters': [], 'turns': [
            ['baseten', json.dumps({'provider': 'baseten', 'model': 'fixture-code'}),
             'a' * 64, 'binding', 0],
            ['grok', json.dumps({'provider': 'grok-oauth', 'model': 'grok-fixture'}),
             'a' * 64, 'binding', 0],
        ]}
        operation.record = {'old_identity': identity, 'configuration_revision': 'revision',
                            'shared_files': {'config': 'same'}, 'required_models': [],
                            'ownership': snapshot, 'paused': []}
        process = {'pid': 100, 'ppid': 1, 'started': 'first', 'state': 'S',
                   'executable': '/Applications/ChatGPT.app/Contents/Resources/codex'}
        status = {'configuration_revision': 'revision', 'runtime': {'active_requests': 0}}
        with patch.object(operation, 'status', return_value=(status, identity)),              patch.object(operation, 'assert_paused'),              patch.object(operation, 'shared_files', return_value={'config': 'same'}),              patch.object(operation, 'save'),              patch.object(maintenance, 'processes', return_value={100: process}),              patch.object(maintenance, 'journal_snapshot', return_value=snapshot),              patch.object(maintenance.os, 'kill'):
            operation.freeze(1)
        self.assertEqual(operation.record['ownership'], snapshot)

    def test_unknown_owned_provider_still_blocks_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config'
            config.mkdir()
            (config / 'model-switcher.json').write_text(json.dumps({'services': [{
                'id': 'azure', 'baseURL': 'https://fixture.openai.azure.com/openai/v1',
                'models': [{'id': 'gpt-fixture'}]
            }]}))
            operation = maintenance.Maintenance(root / 'app', root / 'state', config)
            operation.record = {
                'credentials_available': {'azure': True, 'openrouter': False},
                'ownership': {'turns': [[
                    'unknown', json.dumps({'provider': 'unknown', 'model': 'fixture'}),
                    'old', 'binding', 0
                ]]}
            }
            result = type('Result', (), {'returncode': 0, 'stdout': b'synthetic-key\n'})()
            with patch.object(maintenance.subprocess, 'run', return_value=result):
                with self.assertRaisesRegex(maintenance.MaintenanceError, 'cannot transfer'):
                    operation.keys()


class PauseTests(unittest.TestCase):
    def item(self, pid=100, started='first', state='T'):
        return {'pid': pid, 'ppid': 1, 'started': started, 'state': state, 'executable': '/Applications/ChatGPT.app/Contents/Resources/codex'}

    def test_resume_only_matching_stopped_processes(self):
        original = [self.item(), self.item(101), self.item(102)]
        current = {100: self.item(), 101: self.item(101, started='reused'), 102: self.item(102, state='S')}
        sent = []
        maintenance.resume_owned(original, lambda: current, lambda pid, sig: sent.append((pid, sig)))
        self.assertEqual(sent, [(100, signal.SIGCONT)])

    def test_unexpected_new_issuer_prevents_switch(self):
        operation = maintenance.Maintenance(Path('/app'), Path('/state'), Path('/config'))
        operation.record = {'paused': [self.item()]}
        with patch.object(maintenance, 'processes', return_value={100: self.item(), 101: self.item(101, state='S')}):
            with self.assertRaisesRegex(maintenance.MaintenanceError, 'not paused'):
                operation.assert_paused()

    def test_changed_pid_identity_prevents_switch(self):
        operation = maintenance.Maintenance(Path('/app'), Path('/state'), Path('/config'))
        operation.record = {'paused': [self.item()]}
        with patch.object(maintenance, 'processes', return_value={100: self.item(started='reused')}):
            with self.assertRaises(maintenance.MaintenanceError):
                operation.assert_paused()

    def test_uncertain_or_legacy_process_name_does_not_match_substring(self):
        self.assertFalse(maintenance.issuer({'executable': '/tmp/not-codex-backup'}))
        self.assertTrue(maintenance.issuer({'executable': '/Applications/ChatGPT.app/Contents/MacOS/ChatGPT'}))
        self.assertTrue(maintenance.issuer({'executable': '/somewhere/codex'}))



class RecoveryTests(unittest.TestCase):
    def test_crash_phases_restore_old_service_before_resuming(self):
        for phase in ('stopping', 'transferring', 'starting', 'restoring'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                self.run_recovery(Path(directory), phase)

    def run_recovery(self, state, phase, previous_marker=None):
        old, new = 'a' * 64, 'b' * 64
        runtime = GatewayRuntime(state, old, 'old-boot')
        route = {'provider': 'azure', 'model': 'fixture'}
        runtime.pin('retained-tool-gap', lambda: route)
        request = runtime.begin('retained-tool-gap', 'same-account')
        runtime.finish(request, True)
        snapshot = maintenance.journal_snapshot(state / 'ownership.sqlite')
        runtime.close()
        job = {'Label': 'fixture', 'EnvironmentVariables': {'MODEL_HARBOR_RUNTIME_DIGEST': old},
               'ProgramArguments': ['/python', '-B', '-u', '/old/grok_adapter.py']}
        old_identity = {'mode': 'independent', 'protocol_version': 1, 'runtime_id': old, 'boot_id': 'old-boot'}
        path = state / 'record.json'
        record = {'phase': phase, 'old_identity': old_identity, 'new_digest': new,
            'old_job': job, 'previous_marker': previous_marker, 'paused': [], 'ownership': snapshot,
            'configuration_revision': 'same-revision'}
        maintenance.atomic_json(path, record)
        if previous_marker:
            maintenance.atomic_json(state / 'maintenance.json', previous_marker)
        if phase in ('starting', 'restoring'):
            maintenance.migrate_owners(state / 'ownership.sqlite', snapshot['turns'], old, new)
        events = []

        class Fixture(maintenance.Maintenance):
            active = None
            revision = None

            def assert_paused(self):
                events.append('paused-confirmed')

            def launch(self, action):
                events.append(action)
                if action == 'bootout':
                    self.active.close(); self.active = None
                else:
                    self.active = GatewayRuntime(state, old, 'restored-boot')

            def wait_identity(self, digest, port=None, process=None):
                return self.status()

            def post(self, path, value, identity, port=None):
                self.revision = 'same-revision'
                events.append('credentials-restored')
                return {'configured': True}

            def status(self, port=None):
                events.append('status-verified')
                return {'configuration_revision': self.revision, 'maintenance': {'blocked': (state / 'maintenance.json').exists()}}, dict(old_identity, boot_id='restored-boot')

        operation = Fixture(state / 'app', state, state / 'config')
        operation.record_path = path
        operation.connections = {'azure': {'key': 'synthetic', 'endpoint': 'https://fixture.openai.azure.com/openai/v1'}}
        operation.active = GatewayRuntime(state, new if phase == 'restoring' else old, 'running') if phase in ('stopping', 'restoring') else None
        launcher = type('Launcher', (), {'registered': lambda _self, _target: operation.active is not None})()
        try:
            with patch.object(maintenance.service, 'Launchctl', return_value=launcher), patch.object(maintenance, 'resume_owned', side_effect=lambda _items: events.append('resumed')):
                operation.recover()
            self.assertEqual(events[-1], 'resumed')
            self.assertLess(events.index('credentials-restored'), events.index('resumed'))
            self.assertLess(events.index('status-verified'), events.index('resumed'))
            self.assertEqual(maintenance.journal_snapshot(state / 'ownership.sqlite'), snapshot)
            self.assertEqual(json.loads(path.read_text())['phase'], 'rolled_back')
        finally:
            if operation.active:
                operation.active.close()

    def test_a_failed_committed_runtime_is_not_blindly_resumed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operation = maintenance.Maintenance(root / 'app', root, root / 'config')
            operation.record_path = root / 'record.json'
            maintenance.atomic_json(operation.record_path, {'phase': 'committed', 'paused': [],
                'final_identity': {'boot_id': 'accepted'}, 'configuration_revision': 'expected'})
            with patch.object(operation, 'status', return_value=({'configuration_revision': 'expected'}, {'boot_id': 'restarted'})), patch.object(maintenance, 'resume_owned') as resumed:
                with self.assertRaisesRegex(maintenance.MaintenanceError, 'identity changed'):
                    operation.recover()
                resumed.assert_not_called()

    def test_second_upgrade_rollback_retires_previous_committed_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_recovery(Path(directory), 'restoring', {'phase': 'committed', 'boot_id': 'previous-boot'})

    def test_recorded_rollback_always_resumes_owned_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operation = maintenance.Maintenance(root / 'app', root, root / 'config')
            operation.record_path = root / 'record.json'
            maintenance.atomic_json(operation.record_path, {'phase': 'rolled_back', 'paused': [{'pid': 123}]})
            with patch.object(maintenance, 'resume_owned') as resumed:
                operation.recover()
                resumed.assert_called_once_with([{'pid': 123}])

    def test_lock_timeout_before_pause_aborts_without_waiting_forever(self):
        from contextlib import contextmanager
        @contextmanager
        def unavailable(_state):
            raise maintenance.MaintenanceError('lock busy')
            yield
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operation = maintenance.Maintenance(root / 'app', root, root / 'config')
            operation.record_path = root / 'record.json'
            operation.record = {'phase': 'ready', 'paused': []}
            maintenance.atomic_json(operation.record_path, operation.record)
            with patch.object(operation, 'prepare'), patch.object(operation, 'watchdog', return_value=123), patch.object(maintenance.service, 'ownership_lock', unavailable), patch.object(maintenance.os, 'waitpid', return_value=(123, 0)), patch.object(maintenance, 'resume_owned'):
                with self.assertRaisesRegex(maintenance.MaintenanceError, 'lock busy'):
                    operation.activate(1)
            self.assertEqual(json.loads(operation.record_path.read_text())['phase'], 'aborted')

    def test_marker_publication_failure_has_recoverable_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operation = maintenance.Maintenance(root / 'app', root, root / 'config')
            operation.record_path = root / 'record.json'
            operation.record = {'phase': 'paused', 'id': 'id', 'old_identity': {'runtime_id': 'old'},
                'new_digest': 'new', 'configuration_revision': 'revision', 'required_models': []}
            original = maintenance.atomic_json
            def interrupted(path, value):
                if path == root / 'maintenance.json':
                    raise OSError('power loss')
                return original(path, value)
            with patch.object(operation, 'assert_paused'), patch.object(maintenance, 'atomic_json', interrupted):
                with self.assertRaises(OSError):
                    operation.switch()
            self.assertEqual(json.loads(operation.record_path.read_text())['phase'], 'stopping')

    def test_finalized_gate_does_not_survive_successful_maintenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operation = maintenance.Maintenance(root / 'app', root, root / 'config')
            operation.record = {'id': 'transaction', 'final_identity': {'boot_id': 'verified'}, 'configuration_revision': 'revision'}
            maintenance.atomic_json(root / 'maintenance.json', {'id': 'transaction', 'phase': 'committed', 'boot_id': 'verified', 'configuration_revision': 'revision'})
            with patch.object(operation, 'status', return_value=({'configuration_revision': 'revision', 'maintenance': {'blocked': False}}, {'boot_id': 'verified'})):
                operation.finalize_committed()
                operation.finalize_committed()
            self.assertFalse((root / 'maintenance.json').exists())

    def test_finalize_preserves_another_maintenance_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operation = maintenance.Maintenance(root / 'app', root, root / 'config')
            operation.record = {'id': 'ours', 'final_identity': {'boot_id': 'verified'}, 'configuration_revision': 'revision'}
            maintenance.atomic_json(root / 'maintenance.json', {'id': 'theirs'})
            with self.assertRaises(maintenance.MaintenanceError):
                operation.finalize_committed()
            self.assertTrue((root / 'maintenance.json').exists())

if __name__ == '__main__':
    unittest.main()
