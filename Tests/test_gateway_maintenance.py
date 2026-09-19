import copy
import concurrent.futures
import threading
import time
import json
import os
import unittest
import uuid
from unittest.mock import patch

from Tests import test_provider_restore as fixtures

bridge = fixtures.bridge


class MaintenanceTests(unittest.TestCase):
    setUp = fixtures.RestoreTests.setUp
    stop = fixtures.RestoreTests.stop
    request = fixtures.RestoreTests.request
    identity = fixtures.RestoreTests.identity
    restore = fixtures.RestoreTests.restore
    response = staticmethod(fixtures.RestoreTests.response)
    inference = fixtures.RestoreTests.inference
    wait_for_finished_requests = fixtures.RestoreTests.wait_for_finished_requests
    restart = fixtures.RestoreTests.restart

    def marker(self, **changes):
        value = {'schema': 1, 'id': str(uuid.uuid4()), 'from_runtime': 'b' * 64,
                 'to_runtime': self.runtime.runtime_id,
                 'configuration_revision': bridge.configuration_revision([
                     self.payload['connections']['azure'], self.payload['connections']['openrouter']['key']]),
                 'required_models': list(self.payload['required_models']), 'phase': 'prepared'}
        value.update(changes)
        path = self.runtime.directory / 'maintenance.json'
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return value

    def commit(self, marker, **changes):
        body = {'expected_runtime': self.identity(),
                'expected_configuration_revision': bridge.configuration_revision(), 'maintenance_id': marker['id']}
        body.update(changes)
        return self.request(path='/harbor/maintenance/commit', body=body)

    def test_prepared_marker_blocks_before_ownership_or_dispatch_and_status_remains_available(self):
        marker = self.marker()
        before = self.runtime.status()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            status, value = self.inference()
            self.assertEqual(status, 503, value)
            self.assertIn('maintenance', value['error'].lower())
            opener.assert_not_called()
        self.assertEqual(self.runtime.status(), before)
        status, value = self.request('GET', '/harbor/status')
        self.assertEqual(status, 200)
        self.assertEqual(value['maintenance'], {'id': marker['id'], 'blocked': True, 'phase': 'prepared'})

    def test_exact_restore_preserves_untracked_counter_and_stays_closed_until_commit(self):
        self.runtime.pin(None, lambda: {'provider': 'azure', 'model': 'coding-prod'})
        marker = self.marker()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.restore()[0], 200)
            self.assertEqual(opener.return_value.open.call_count, 2)
            self.assertEqual(self.inference()[0], 503)
            status, value = self.commit(marker)
            self.assertEqual(status, 200, value)
            self.assertEqual(value['maintenance'], {'id': marker['id'], 'blocked': False, 'phase': 'committed'})
            self.assertEqual(self.inference()[0], 200)
            self.wait_for_finished_requests()
            self.assertEqual(opener.return_value.open.call_count, 3)
        self.assertEqual(self.runtime.status()['untracked_admissions'], 1)
        saved = json.loads((self.runtime.directory / 'maintenance.json').read_text())
        self.assertEqual(saved, dict(marker, phase='committed', boot_id=self.runtime.boot_id))
        self.assertEqual(os.stat(self.runtime.directory / 'maintenance.json').st_mode & 0o777, 0o600)
        self.assertFalse(self.runtime.status()['promotion_allowed'])

    def test_changed_credentials_or_incomplete_required_models_refuse_before_probe(self):
        self.runtime.pin(None, lambda: {'provider': 'azure', 'model': 'coding-prod'})
        self.marker()
        for mode in ('changed-key', 'partial'):
            payload = copy.deepcopy(self.payload)
            if mode == 'changed-key':
                payload['connections']['azure']['key'] = 'different-valid-key'
            else:
                payload['connections'].pop('openrouter')
                payload['required_models'] = ['harbor/azure/coding-prod']
            with self.subTest(mode=mode), patch.object(bridge.urllib.request, 'build_opener') as opener:
                self.assertEqual(self.restore(payload)[0], 409)
                opener.assert_not_called()
        self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.OPENROUTER_KEY, '')
        self.assertEqual(self.runtime.status()['untracked_admissions'], 1)

    def test_commit_requires_exact_identity_revision_and_every_current_proof(self):
        marker = self.marker()
        bridge.AZURE_CONNECTION = dict(self.payload['connections']['azure'])
        bridge.OPENROUTER_KEY = self.payload['connections']['openrouter']['key']
        self.assertEqual(self.commit(marker)[0], 409)
        revision = bridge.configuration_revision()
        bridge.READINESS.record({'provider': 'azure', 'model': 'coding-prod'}, revision, 'verified')
        self.assertEqual(self.commit(marker)[0], 409)
        bridge.READINESS.record({'provider': 'openrouter', 'model': 'fixture/coder'}, revision, 'verified')
        for changes in ({'expected_runtime': dict(self.identity(), boot_id=str(uuid.uuid4()))},
                        {'expected_configuration_revision': '0' * 64}, {'maintenance_id': str(uuid.uuid4())}):
            self.assertEqual(self.commit(marker, **changes)[0], 409)
        self.assertEqual(self.commit(marker)[0], 200)

    def test_committed_marker_closes_on_new_boot_or_changed_configuration(self):
        marker = self.marker()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.restore()[0], 200)
        self.assertEqual(self.commit(marker)[0], 200)
        bridge.OPENROUTER_KEY = 'changed-key'
        self.assertEqual(self.inference()[0], 503)
        self.restart()
        self.assertEqual(self.inference()[0], 503)
        status = self.request('GET', '/harbor/status')[1]
        self.assertTrue(status['maintenance']['blocked'])
        self.assertEqual(self.runtime.status()['unresolved_turns'], 0)

    def test_unsafe_or_malformed_markers_fail_closed_without_echoing_content(self):
        path = self.runtime.directory / 'maintenance.json'
        for mode in ('public', 'symlink', 'directory', 'json', 'schema', 'missing', 'models', 'committed-no-boot'):
            with self.subTest(mode=mode):
                marker = self.marker()
                if mode == 'public':
                    path.chmod(0o644)
                elif mode == 'symlink':
                    path.unlink()
                    target = self.root / 'private-marker'
                    target.write_text(json.dumps(marker))
                    target.chmod(0o600)
                    path.symlink_to(target)
                elif mode == 'directory':
                    path.unlink()
                    path.mkdir()
                elif mode == 'json':
                    path.write_text('synthetic-sensitive-marker-content')
                else:
                    if mode == 'schema': marker['schema'] = True
                    if mode == 'missing': marker.pop('configuration_revision')
                    if mode == 'models': marker['required_models'] = ['harbor-selected']
                    if mode == 'committed-no-boot': marker['phase'] = 'committed'
                    path.write_text(json.dumps(marker))
                with patch.object(bridge.urllib.request, 'build_opener') as opener:
                    status, value = self.inference()
                    self.assertEqual(status, 503, value)
                    self.assertEqual(self.restore()[0], 409)
                    self.assertEqual(self.commit(marker)[0], 409)
                    opener.assert_not_called()
                status = self.request('GET', '/harbor/status')[1]
                self.assertEqual(status['maintenance'], {'id': None, 'blocked': True, 'phase': 'invalid'})
                self.assertNotIn('synthetic-sensitive-marker-content', json.dumps(status))
                self.assertEqual(self.runtime.status()['untracked_admissions'], 0)
                self.assertEqual(self.runtime.status()['unresolved_turns'], 0)
                if path.is_dir() and not path.is_symlink():
                    path.rmdir()
                else:
                    path.unlink()

    def test_valid_foreign_target_does_not_block_or_authorize_restore_exception(self):
        self.marker(to_runtime='c' * 64)
        bridge.AZURE_CONNECTION = dict(self.payload['connections']['azure'])
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.inference()[0], 200)
            self.wait_for_finished_requests()
        bridge.AZURE_CONNECTION = None
        self.runtime.pin(None, lambda: {'provider': 'azure', 'model': 'coding-prod'})
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            self.assertEqual(self.restore()[0], 409)
            opener.assert_not_called()
        self.assertEqual(self.request('GET', '/harbor/status')[1]['maintenance'],
                         {'id': None, 'blocked': False, 'phase': None})

    def test_commit_refuses_active_requests_and_does_not_change_journal(self):
        marker = self.marker()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.restore()[0], 200)
        self.runtime.pin('tracked-owner', lambda: {'provider': 'azure', 'model': 'coding-prod'})
        lease = self.runtime.begin('tracked-owner', 'preserved-binding')
        before = self.runtime.status()
        original = (self.runtime.directory / 'maintenance.json').read_bytes()
        status, value = self.commit(marker)
        self.assertEqual(status, 409, value)
        self.assertEqual(self.runtime.status(), before)
        self.assertEqual((self.runtime.directory / 'maintenance.json').read_bytes(), original)
        self.runtime.finish(lease, True)
        self.assertEqual(self.commit(marker)[0], 200)
        self.assertEqual(self.runtime.status()['unresolved_turns'], 1)
        self.assertEqual(self.runtime.db.execute('SELECT binding FROM turns WHERE id=?', ('tracked-owner',)).fetchone()[0],
                         'preserved-binding')

    def test_expired_wrong_boot_or_failed_proof_cannot_commit(self):
        marker = self.marker()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.restore()[0], 200)
        route = {'provider': 'azure', 'model': 'coding-prod'}
        revision = bridge.configuration_revision()
        proof_key = ('azure', 'coding-prod')
        for mode in ('expired', 'wrong-boot', 'failed', 'wrong-revision'):
            bridge.READINESS.record(route, revision, 'verified')
            proof = bridge.READINESS.proofs[proof_key]
            if mode == 'expired': proof['checked_at'] -= 301
            if mode == 'wrong-boot': proof['boot_id'] = str(uuid.uuid4())
            if mode == 'failed': proof['result'] = 'auth_failed'
            if mode == 'wrong-revision': proof['configuration_revision'] = '0' * 64
            self.assertEqual(self.commit(marker)[0], 409)
        self.assertEqual(json.loads((self.runtime.directory / 'maintenance.json').read_text())['phase'], 'prepared')

    def test_changed_marker_during_probe_cannot_publish_credentials(self):
        self.marker()
        def open_request(*args, **kwargs):
            self.marker()
            return self.response()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = open_request
            self.assertEqual(self.restore()[0], 409)
        self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.OPENROUTER_KEY, '')
        self.assertEqual(bridge.provider_status()['route_verification'], [])

    def test_marker_changed_during_atomic_commit_is_preserved(self):
        marker = self.marker()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.restore()[0], 200)
        original = bridge._runtime_module.os.fsync
        replacement = dict(marker, id=str(uuid.uuid4()))
        def fsync(fd):
            original(fd)
            (self.runtime.directory / 'maintenance.json').write_text(json.dumps(replacement))
        with patch.object(bridge._runtime_module.os, 'fsync', side_effect=fsync):
            self.assertEqual(self.commit(marker)[0], 409)
        self.assertEqual(json.loads((self.runtime.directory / 'maintenance.json').read_text()), replacement)
        self.assertEqual(list(self.runtime.directory.glob('.maintenance-*')), [])

    def test_authenticated_helper_checks_expected_identity_before_commit_payload(self):
        marker = self.marker()
        bridge.TOKEN_PATH.chmod(0o600)
        bridge._control_module.ensure_proof_secret(bridge.TOKEN_PATH)
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **kw: self.response()
            self.assertEqual(self.restore()[0], 200)
        expected = dict(self.identity(), boot_id=str(uuid.uuid4()))
        body = {'expected_runtime': expected, 'expected_configuration_revision': bridge.configuration_revision(),
                'maintenance_id': marker['id']}
        with patch.object(bridge, 'commit_maintenance', wraps=bridge.commit_maintenance) as commit:
            with self.assertRaisesRegex(bridge._control_module.ControlError, 'No owner credentials'):
                bridge._control_module.owner_request('POST', '/harbor/maintenance/commit', json.dumps(body).encode(),
                                                     self.server.server_port, bridge.TOKEN_PATH)
            commit.assert_not_called()
        body['expected_runtime'] = self.identity()
        raw, identity = bridge._control_module.owner_request(
            'POST', '/harbor/maintenance/commit', json.dumps(body).encode(), self.server.server_port, bridge.TOKEN_PATH)
        self.assertEqual(identity, self.identity())
        self.assertFalse(json.loads(raw)['maintenance']['blocked'])
        for path in ('promote', 'retire', 'rollback', 'shutdown'):
            self.assertEqual(self.request(path='/harbor/runtime/' + path)[0], 409)

    def test_queued_request_stops_before_dispatch_when_maintenance_is_prepared(self):
        bridge.AZURE_CONNECTION = dict(self.payload['connections']['azure'])
        key = (self.endpoint, 'coding-prod')
        permits = [bridge.AZURE_ADMISSION.acquire(key) for _ in range(2)]
        try:
            with patch.object(bridge.urllib.request, 'build_opener') as opener:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(self.inference)
                    deadline = time.monotonic() + 2
                    while bridge.AZURE_ADMISSION.snapshot()['waiting'] != 1 and time.monotonic() < deadline:
                        threading.Event().wait(.01)
                    self.assertEqual(bridge.AZURE_ADMISSION.snapshot()['waiting'], 1)
                    self.marker()
                    for permit in permits:
                        permit.release()
                    status, value = pending.result(timeout=3)
                    self.assertEqual(status, 503, value)
                    self.wait_for_finished_requests()
                opener.return_value.open.assert_not_called()
            self.assertEqual(self.runtime.status()['uncertain_turns'], 0)
            self.assertEqual(self.runtime.status()['unresolved_turns'], 1)
        finally:
            for permit in permits:
                permit.release()

    def test_unauthorized_commit_does_not_change_marker(self):
        marker = self.marker()
        original = (self.runtime.directory / 'maintenance.json').read_bytes()
        body = {'expected_runtime': self.identity(), 'expected_configuration_revision': bridge.configuration_revision(),
                'maintenance_id': marker['id']}
        for headers in ({}, {'X-Model-Harbor-Token': 'wrong'},
                        {'X-Model-Harbor-Token': 'synthetic-owner-token', 'Origin': 'https://example.test'}):
            status, _ = self.request(path='/harbor/maintenance/commit', body=body, headers=headers)
            self.assertEqual(status, 401)
        self.assertEqual((self.runtime.directory / 'maintenance.json').read_bytes(), original)
