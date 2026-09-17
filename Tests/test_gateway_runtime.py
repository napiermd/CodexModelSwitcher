"""Runtime ownership contracts, exercised without starting a user's service."""
import concurrent.futures
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock


SUPPORT = Path(__file__).resolve().parents[1] / 'ModelHarbor' / 'Support'
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
from gateway_runtime import GatewayRuntime, RouteReadiness, turn_key


ROUTE = {'provider': 'azure', 'model': 'fixture-model'}
OTHER_ROUTE = {'provider': 'openrouter', 'model': 'fixture-other'}


class GatewayRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / 'state'

    def runtime(self, runtime_id='runtime-a', boot_id='boot-a', **kwargs):
        runtime = GatewayRuntime(self.state, runtime_id, boot_id, **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def test_completed_request_retains_turn_across_tool_gap_and_restart(self):
        first = self.runtime()
        self.assertEqual(first.pin('turn-a', lambda: ROUTE), ROUTE)
        request = first.begin('turn-a', 'account-a/config-a')
        first.finish(request, delivered=True)
        self.assertEqual(first.status()['active_requests'], 0)
        self.assertEqual(first.status()['unresolved_turns'], 1)
        first.close()

        reopened = self.runtime(boot_id='boot-b')
        select_current = Mock(return_value=OTHER_ROUTE)
        self.assertEqual(reopened.pin('turn-a', select_current), ROUTE)
        select_current.assert_not_called()
        resumed = reopened.begin('turn-a', 'account-a/config-a')
        reopened.finish(resumed, delivered=True)
        self.assertEqual(reopened.status()['unresolved_turns'], 1)

    def test_more_than_4096_unfinished_turns_preserve_oldest_after_restart(self):
        runtime = self.runtime()
        for index in range(4105):
            runtime.pin(f'turn-{index}', lambda: ROUTE)
        self.assertEqual(runtime.status()['unresolved_turns'], 4105)
        runtime.close()

        reopened = self.runtime(boot_id='boot-b')
        select_current = Mock(return_value=OTHER_ROUTE)
        self.assertEqual(reopened.pin('turn-0', select_current), ROUTE)
        self.assertEqual(reopened.pin('turn-4096', select_current), ROUTE)
        self.assertEqual(reopened.pin('turn-4104', select_current), ROUTE)
        select_current.assert_not_called()
        self.assertEqual(reopened.status()['unresolved_turns'], 4105)

    def test_storage_pressure_rejects_new_turn_and_keeps_existing_owners(self):
        runtime = self.runtime(max_turns=2)
        runtime.pin('oldest', lambda: ROUTE)
        runtime.pin('second', lambda: OTHER_ROUTE)
        with self.assertRaisesRegex(ValueError, 'full'):
            runtime.pin('third', lambda: OTHER_ROUTE)
        self.assertEqual(runtime.status()['unresolved_turns'], 2)
        self.assertEqual(runtime.pin('oldest', lambda: OTHER_ROUTE), ROUTE)
        self.assertEqual(runtime.pin('second', lambda: ROUTE), OTHER_ROUTE)
        request = runtime.begin('oldest', 'account-a/config-a')
        runtime.finish(request, delivered=True)
        with self.assertRaisesRegex(ValueError, 'full'):
            runtime.pin('third', lambda: OTHER_ROUTE)

    def test_concurrent_first_requests_choose_one_durable_owner(self):
        runtime = self.runtime()
        barrier = threading.Barrier(12)
        select = Mock(return_value=ROUTE)

        def claim(_):
            barrier.wait(timeout=5)
            return runtime.pin('same-turn', select)

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(claim, range(12)))
        self.assertEqual(results, [ROUTE] * 12)
        select.assert_called_once_with()
        self.assertEqual(runtime.status()['unresolved_turns'], 1)
        runtime.close()
        reopened = self.runtime(boot_id='boot-b')
        self.assertEqual(reopened.pin('same-turn', lambda: OTHER_ROUTE), ROUTE)

    def test_simultaneous_dispatch_for_same_turn_is_refused(self):
        runtime = self.runtime()
        runtime.pin('turn-a', lambda: ROUTE)
        first = runtime.begin('turn-a', 'account-a/config-a')
        with self.assertRaisesRegex(ValueError, 'in flight|duplicate'):
            runtime.begin('turn-a', 'account-a/config-a')
        self.assertEqual(runtime.status()['active_requests'], 1)
        runtime.finish(first, delivered=True)
        later = runtime.begin('turn-a', 'account-a/config-a')
        runtime.finish(later, delivered=True)

    def test_changed_configuration_for_same_account_cannot_take_over_turn(self):
        runtime = self.runtime()
        runtime.pin('turn-a', lambda: ROUTE)
        first = runtime.begin('turn-a', 'account-a/config-a')
        runtime.finish(first, delivered=True)
        with self.assertRaisesRegex(ValueError, 'changed'):
            runtime.begin('turn-a', 'account-a/config-b')
        self.assertEqual(runtime.status()['active_requests'], 0)
        resumed = runtime.begin('turn-a', 'account-a/config-a')
        runtime.finish(resumed, delivered=True)

    def test_different_account_cannot_take_over_turn(self):
        runtime = self.runtime()
        runtime.pin('turn-a', lambda: ROUTE)
        first = runtime.begin('turn-a', 'account-a/config-a')
        runtime.finish(first, delivered=True)
        with self.assertRaisesRegex(ValueError, 'changed'):
            runtime.begin('turn-a', 'account-b/config-a')
        self.assertEqual(runtime.status()['active_requests'], 0)

    def test_unfinished_dispatch_becomes_uncertain_on_new_boot(self):
        first = self.runtime()
        first.pin('turn-a', lambda: ROUTE)
        first.begin('turn-a', 'account-a/config-a')
        first.close()
        reopened = self.runtime(boot_id='boot-b')
        select = Mock(return_value=OTHER_ROUTE)
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            reopened.pin('turn-a', select)
        with self.assertRaisesRegex(ValueError, 'uncertain'):
            reopened.begin('turn-a', 'account-a/config-a')
        select.assert_not_called()
        self.assertEqual(reopened.status()['uncertain_turns'], 1)
        self.assertEqual(reopened.status()['unresolved_turns'], 1)
        self.assertEqual(reopened.status()['active_requests'], 0)
        self.assertEqual(reopened.status()['recovered_uncertain_requests'], 1)

    def test_partial_delivery_blocks_automatic_replay_after_restart(self):
        runtime = self.runtime()
        runtime.pin('turn-a', lambda: ROUTE)
        request = runtime.begin('turn-a', 'account-a/config-a')
        runtime.finish(request, delivered=False)
        self.assertEqual(runtime.status()['active_requests'], 0)
        runtime.close()

        reopened = self.runtime(boot_id='boot-b')
        select = Mock(return_value=ROUTE)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'uncertain'):
                reopened.pin('turn-a', select)
        select.assert_not_called()
        self.assertEqual(reopened.status()['unresolved_turns'], 1)
        self.assertEqual(reopened.status()['uncertain_turns'], 1)

    def test_finishing_same_request_twice_is_harmless(self):
        runtime = self.runtime()
        runtime.pin('turn-a', lambda: ROUTE)
        request = runtime.begin('turn-a', 'account-a/config-a')
        runtime.finish(request, delivered=True)
        runtime.finish(request, delivered=True)
        self.assertEqual(runtime.status()['active_requests'], 0)
        self.assertEqual(runtime.status()['uncertain_turns'], 0)
        self.assertEqual(runtime.status()['unresolved_turns'], 1)

    def test_begin_requires_known_owner(self):
        runtime = self.runtime()
        with self.assertRaisesRegex(ValueError, 'ownership'):
            runtime.begin('not-pinned', 'account-a/config-a')
        self.assertEqual(runtime.status()['active_requests'], 0)

    def test_different_runtime_cannot_reassign_old_turn(self):
        original = self.runtime()
        original.pin('turn-a', lambda: ROUTE)
        original.close()
        candidate = self.runtime(runtime_id='runtime-b', boot_id='boot-b')
        select = Mock(return_value=OTHER_ROUTE)
        with self.assertRaisesRegex(ValueError, 'another runtime'):
            candidate.pin('turn-a', select)
        select.assert_not_called()
        self.assertEqual(candidate.status()['unresolved_turns'], 1)
        candidate.close()
        restored = self.runtime(boot_id='boot-c')
        self.assertEqual(restored.pin('turn-a', lambda: OTHER_ROUTE), ROUTE)

    def test_unkeyed_requests_are_visible_without_claiming_lifecycle(self):
        runtime = self.runtime()
        self.assertEqual(runtime.pin(None, lambda: ROUTE), ROUTE)
        request = runtime.begin(None, 'account-a/config-a')
        runtime.finish(request, delivered=True)
        status = runtime.status()
        self.assertEqual(status['untracked_admissions'], 1)
        self.assertEqual(status['unresolved_turns'], 0)
        self.assertIs(status['desktop_lifecycle_verified'], False)
        self.assertIs(status['promotion_allowed'], False)
        self.assertIs(status['retirement_allowed'], False)

    def test_zero_active_requests_cannot_enable_live_update_gates(self):
        runtime = self.runtime()
        runtime.pin('private-thread-turn', lambda: ROUTE)
        request = runtime.begin('private-thread-turn', 'private-account-binding')
        runtime.finish(request, delivered=True)
        status = runtime.status()
        self.assertEqual(status['active_requests'], 0)
        self.assertIs(status['desktop_lifecycle_verified'], False)
        self.assertIs(status['promotion_allowed'], False)
        self.assertIs(status['retirement_allowed'], False)
        self.assertIn('maintenance', status['update_block_reason'])
        serialized = json.dumps(status)
        self.assertNotIn('private-thread-turn', serialized)
        self.assertNotIn('private-account-binding', serialized)

    def test_state_lock_refuses_another_process_and_preserves_owner(self):
        runtime = self.runtime()
        runtime.pin('turn-a', lambda: ROUTE)
        code = """
import sys
from gateway_runtime import GatewayRuntime
try:
    runtime = GatewayRuntime(sys.argv[1], 'competitor', 'other-boot')
except ValueError:
    raise SystemExit(3)
runtime.close()
raise SystemExit(0)
"""
        environment = {'PATH': os.defpath, 'PYTHONPATH': str(SUPPORT), 'HOME': str(self.root),
                       'CODEX_HOME': str(self.root / 'codex')}
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.state)],
                                env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(runtime.pin('turn-a', lambda: OTHER_ROUTE), ROUTE)
        self.assertEqual(runtime.status()['boot_id'], 'boot-a')

    def test_close_releases_lock_and_is_idempotent(self):
        first = self.runtime()
        first.close()
        first.close()
        second = self.runtime(boot_id='boot-b')
        self.assertEqual(second.status()['boot_id'], 'boot-b')

    def test_public_state_directory_is_rejected(self):
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'private'):
            self.runtime()
        self.assertFalse((self.state / 'ownership.sqlite').exists())

    def test_symlinked_state_directory_is_rejected(self):
        target = self.root / 'target'
        target.mkdir(mode=0o700)
        self.state.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.runtime()
        self.assertEqual(list(target.iterdir()), [])

    def test_symlinked_journal_is_rejected_without_touching_target(self):
        self.state.mkdir(mode=0o700)
        target = self.root / 'sentinel'
        target.write_bytes(b'do-not-change')
        (self.state / 'ownership.sqlite').symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.runtime()
        self.assertEqual(target.read_bytes(), b'do-not-change')

    def test_corrupt_journal_is_not_reset_and_failed_open_releases_lock(self):
        self.state.mkdir(mode=0o700)
        journal = self.state / 'ownership.sqlite'
        journal.write_bytes(b'not-a-sqlite-database')
        with self.assertRaises(sqlite3.DatabaseError):
            self.runtime()
        self.assertEqual(journal.read_bytes(), b'not-a-sqlite-database')
        journal.unlink()
        recovered = self.runtime()
        self.assertEqual(recovered.status()['unresolved_turns'], 0)

    def test_future_schema_is_rejected_without_losing_existing_turn(self):
        original = self.runtime()
        original.pin('turn-a', lambda: ROUTE)
        original.close()
        journal = self.state / 'ownership.sqlite'
        with contextlib.closing(sqlite3.connect(journal)) as database, database:
            database.execute('PRAGMA user_version=999')
        with self.assertRaisesRegex(ValueError, 'version'):
            self.runtime(boot_id='boot-b')
        with contextlib.closing(sqlite3.connect(journal)) as database, database:
            self.assertEqual(database.execute('PRAGMA user_version').fetchone()[0], 999)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM turns').fetchone()[0], 1)


class TurnIdentityTests(unittest.TestCase):
    def test_equivalent_metadata_locations_have_same_identity(self):
        direct = {'client_metadata': {'thread_id': 'thread-a', 'turn_id': 'turn-a'}}
        nested = {'client_metadata': {'x-codex-turn-metadata': {'thread_id': 'thread-a', 'turn_id': 'turn-a'}}}
        encoded = {'client_metadata': {'x-codex-turn-metadata': '{"turn_id":"turn-a","thread_id":"thread-a"}'}}
        header = {'x-codex-turn-metadata': '{"turn_id":"turn-a","thread_id":"thread-a"}'}
        expected = turn_key(direct, {})
        self.assertIsInstance(expected, str)
        self.assertEqual(turn_key(nested, {}), expected)
        self.assertEqual(turn_key(encoded, {}), expected)
        self.assertEqual(turn_key({}, header), expected)
        self.assertNotIn('thread-a', expected)
        self.assertNotIn('turn-a', expected)

    def test_task_and_turn_both_participate_in_identity(self):
        keys = {turn_key({'client_metadata': {'thread_id': task, 'turn_id': turn}}, {})
                for task, turn in [('task-a', 'turn-a'), ('task-b', 'turn-a'), ('task-a', 'turn-b')]}
        self.assertEqual(len(keys), 3)

    def test_absent_or_incomplete_identity_is_explicitly_untracked(self):
        for source in [{}, {'client_metadata': {}}, {'client_metadata': {'turn_id': 'turn-a'}},
                       {'client_metadata': {'thread_id': 'thread-a'}}]:
            with self.subTest(source=source):
                self.assertIsNone(turn_key(source, {}))

    def test_session_fallback_matches_thread_identity(self):
        direct = {'client_metadata': {'thread_id': 'task-a', 'turn_id': 'turn-a'}}
        fallback = {'client_metadata': {'session_id': 'task-a', 'turn_id': 'turn-a'}}
        self.assertEqual(turn_key(direct, {}), turn_key(fallback, {}))

    def test_wrong_metadata_types_fail_before_untracked_admission(self):
        for value in [[], False, 3, 'metadata']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                turn_key({'client_metadata': value}, {})

    def test_malformed_nested_metadata_fails_before_untracked_admission(self):
        for value in ['{broken-json', 'null', '[]', [], False, 3]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                turn_key({'client_metadata': {'x-codex-turn-metadata': value}}, {})

    def test_nonstring_or_oversized_identity_is_rejected(self):
        for value in [1, True, ['turn-a'], {'id': 'turn-a'}, 'a' * 513]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                turn_key({'client_metadata': {'thread_id': 'task-a', 'turn_id': value}}, {})

    def test_false_identity_is_malformed_rather_than_missing(self):
        for value in [0, False, [], {}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                turn_key({'client_metadata': {'thread_id': 'task-a', 'turn_id': value}}, {})


class RouteReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.readiness = RouteReadiness(ttl=30, clock=lambda: self.now)

    def test_missing_proof_has_no_verified_route(self):
        self.assertEqual(self.readiness.snapshot('config-a'), [])

    def test_verification_requires_same_configuration_and_fresh_time(self):
        self.readiness.record(ROUTE, 'config-a', 'verified')
        self.assertIs(self.readiness.snapshot('config-a')[0]['verified'], True)
        self.assertIs(self.readiness.snapshot('config-b')[0]['verified'], False)
        self.now += 29
        self.assertIs(self.readiness.snapshot('config-a')[0]['verified'], True)
        self.now += 1
        self.assertIs(self.readiness.snapshot('config-a')[0]['verified'], False)

    def test_clock_moving_backwards_does_not_extend_proof(self):
        self.readiness.record(ROUTE, 'config-a', 'verified')
        self.now -= 1
        self.assertIs(self.readiness.snapshot('config-a')[0]['verified'], False)

    def test_failure_supersedes_previously_verified_route(self):
        for result in ['auth_failed', 'unavailable', 'invalid_response']:
            with self.subTest(result=result):
                self.readiness.record(ROUTE, 'config-a', 'verified')
                self.readiness.record(ROUTE, 'config-a', result)
                snapshot = self.readiness.snapshot('config-a')
                self.assertEqual(len(snapshot), 1)
                self.assertEqual(snapshot[0]['result'], result)
                self.assertIs(snapshot[0]['verified'], False)

    def test_different_boot_has_no_inherited_proof(self):
        self.readiness.record(ROUTE, 'config-a', 'verified')
        restarted = RouteReadiness(ttl=30, clock=lambda: self.now)
        self.assertNotEqual(self.readiness.boot_id, restarted.boot_id)
        self.assertEqual(restarted.snapshot('config-a'), [])

    def test_proof_is_model_specific_and_provider_invalidation_is_scoped(self):
        same_provider = {'provider': 'azure', 'model': 'fixture-second'}
        self.readiness.record(ROUTE, 'config-a', 'verified')
        self.readiness.record(same_provider, 'config-a', 'unavailable')
        self.readiness.record(OTHER_ROUTE, 'config-a', 'verified')
        status = {(p['provider'], p['model']): p['verified'] for p in self.readiness.snapshot('config-a')}
        self.assertEqual(status, {('azure', 'fixture-model'): True,
                                  ('azure', 'fixture-second'): False,
                                  ('openrouter', 'fixture-other'): True})
        self.readiness.invalidate('azure')
        remaining = self.readiness.snapshot('config-a')
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]['provider'], 'openrouter')
        self.assertIs(remaining[0]['verified'], True)

    def test_invalid_result_cannot_create_proof(self):
        for value in [True, 'ready', 'credentials_present', None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.readiness.record(ROUTE, 'config-a', value)
        self.assertEqual(self.readiness.snapshot('config-a'), [])

    def test_mutating_status_copy_cannot_change_evidence(self):
        self.readiness.record(ROUTE, 'config-a', 'unavailable')
        snapshot = self.readiness.snapshot('config-a')
        snapshot[0]['result'] = 'verified'
        snapshot[0]['verified'] = True
        self.assertIs(self.readiness.snapshot('config-a')[0]['verified'], False)


if __name__ == '__main__':
    unittest.main()
