import importlib.util
from contextlib import closing, contextmanager
import json
import fcntl
import os
import subprocess
import sys
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('task_repair', pathlib.Path(__file__).resolve().parents[1] / 'ModelHarbor/Support/task_repair.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
TASK = '11111111-2222-4333-8444-555555555555'
MODEL = 'harbor/baseten/example/model'


class TaskRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = pathlib.Path(self.temp.name).resolve()
        self.rollout = self.home / 'sessions' / 'task.jsonl'
        self.rollout.parent.mkdir()
        self.metadata = {'type': 'session_meta', 'payload': {'id': TASK, 'model_provider': 'openai',
                         'history_mode': 'paginated', 'history_base': {'path': 'previous-segment.jsonl'},
                         'base_instructions': 'Preserve every instruction.'}}
        self.history = b'{"type":"event_msg","payload":{"type":"user_message","message":"Keep my exact history.\\u00a0"}}\n' + b'{"type":"turn_context","payload":{"model":"harbor/baseten/example/model"}}\n'
        self.rollout.write_bytes(json.dumps(self.metadata).encode() + b'\n' + self.history)
        self.original = self.rollout.read_bytes()
        catalog = self.home / 'catalog.json'
        catalog.write_text(json.dumps({'models': [{'slug': MODEL}]}))
        (self.home / 'config.toml').write_text('model_catalog_json=' + json.dumps(str(catalog)) + '\n[model_providers.model-harbor]\nname="Model Harbor"\n')
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, model_provider TEXT, model TEXT, rollout_path TEXT, title TEXT)')
            db.execute('INSERT INTO threads VALUES (?, ?, ?, ?, ?)', (TASK, 'openai', MODEL, str(self.rollout), 'Keep this title'))
            db.execute('INSERT INTO threads VALUES (?, ?, ?, ?, ?)', ('unrelated', 'openai', 'native-model', '', 'Other task'))

    def row(self, task=TASK):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            return db.execute('SELECT model_provider,model,title FROM threads WHERE id=?', (task,)).fetchone()

    def test_preview_does_not_mutate_task_or_create_backups(self):
        plan = repair.task_plan(self.home, TASK)
        self.assertEqual(plan['previous_provider'], 'openai')
        self.assertEqual(plan['model'], MODEL)
        self.assertEqual(self.rollout.read_bytes(), self.original)
        self.assertEqual(self.row(), ('openai', MODEL, 'Keep this title'))
        self.assertFalse((self.home / 'model-harbor-task-backups').exists())

    def test_only_target_provider_changes_and_backup_is_exact(self):
        result = repair.apply_plan(self.home, repair.task_plan(self.home, TASK), process_check=lambda: [])
        first, history = self.rollout.read_bytes().split(b'\n', 1)
        updated = json.loads(first)
        updated['payload']['model_provider'] = 'openai'
        self.assertEqual(updated, self.metadata)
        self.assertEqual(history, self.history)
        self.assertEqual(self.row(), ('model-harbor', MODEL, 'Keep this title'))
        self.assertEqual(self.row('unrelated'), ('openai', 'native-model', 'Other task'))
        backup = pathlib.Path(result['backup'])
        self.assertEqual((backup / self.rollout.name).read_bytes(), self.original)
        self.assertEqual((backup / self.rollout.name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertTrue(result['conversation_unchanged'])
        self.assertTrue(repair.task_plan(self.home, TASK)['already_repaired'])

    def test_open_codex_refuses_before_any_backup_or_write(self):
        with self.assertRaisesRegex(ValueError, 'Quit Codex'):
            repair.apply_plan(self.home, repair.task_plan(self.home, TASK), process_check=lambda: [123])
        self.assertEqual(self.rollout.read_bytes(), self.original)
        self.assertEqual(self.row()[0], 'openai')
        self.assertFalse((self.home / 'model-harbor-task-backups').exists())

    def test_codex_reopening_during_backup_refuses_both_writes(self):
        states = iter([[], [123]])
        with self.assertRaisesRegex(ValueError, 'Codex reopened'):
            repair.apply_plan(self.home, repair.task_plan(self.home, TASK), process_check=lambda: next(states))
        self.assertEqual(self.rollout.read_bytes(), self.original)
        self.assertEqual(self.row()[0], 'openai')

    def test_mismatched_metadata_is_refused(self):
        self.metadata['payload']['model_provider'] = 'baseten'
        self.rollout.write_text(json.dumps(self.metadata) + '\n')
        with self.assertRaisesRegex(ValueError, 'disagree'):
            repair.task_plan(self.home, TASK)

    def test_partially_repaired_task_is_not_reported_as_already_repaired(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET model_provider=? WHERE id=?', ('model-harbor', TASK))
        with self.assertRaisesRegex(ValueError, 'disagree'):
            repair.task_plan(self.home, TASK)

    def test_unknown_model_or_native_model_is_refused(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET model=? WHERE id=?', ('gpt-native', TASK))
        with self.assertRaisesRegex(ValueError, 'has not selected'):
            repair.task_plan(self.home, TASK)

    def test_failed_file_replace_rolls_back_database(self):
        with patch.object(repair.os, 'replace', side_effect=OSError('Simulated disk error')):
            with self.assertRaisesRegex(OSError, 'Simulated'):
                repair.apply_plan(self.home, repair.task_plan(self.home, TASK), process_check=lambda: [])
        self.assertEqual(self.rollout.read_bytes(), self.original)
        self.assertEqual(self.row()[0], 'openai')

    def test_failed_database_commit_restores_rollout_and_routing(self):
        original_open = repair.open_database

        class FailedCommit:
            def __init__(self, connection):
                self.execute = connection.execute

            def commit(self):
                raise sqlite3.OperationalError('Simulated commit failure')

        @contextmanager
        def database(home, writable=False):
            with original_open(home, writable) as connection:
                yield FailedCommit(connection) if writable else connection

        with patch.object(repair, 'open_database', database):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'Simulated commit'):
                repair.apply_plan(self.home, repair.task_plan(self.home, TASK), process_check=lambda: [])
        self.assertEqual(self.rollout.read_bytes(), self.original)
        self.assertEqual(self.row()[0], 'openai')

    def test_process_detection_uses_executable_name_not_prompt_arguments(self):
        output = f'{repair.os.getuid()} 123 /Applications/ChatGPT.app/Contents/Resources/codex\n{repair.os.getuid()} 456 /bin/zsh\n'
        with patch.object(repair.subprocess, 'run', return_value=type('Result', (), {'stdout': output})()):
            self.assertEqual(repair.codex_processes(), [123])

    def prepare_locks(self):
        locks = self.home / 'thread-writer-locks'
        locks.mkdir(exist_ok=True)
        (locks / '.coordination.lock').touch()
        return locks

    def add_second_task(self):
        task = '22222222-2222-4333-8444-555555555555'
        rollout = self.rollout.with_name('second.jsonl')
        metadata = json.loads(json.dumps(self.metadata))
        metadata['payload']['id'] = task
        rollout.write_bytes(json.dumps(metadata).encode() + b'\n' + self.history)
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('INSERT INTO threads VALUES (?, ?, ?, ?, ?)',
                       (task, 'openai', MODEL, str(rollout), 'Second task'))
        return task, rollout

    def run_batch(self, *options):
        return subprocess.run([sys.executable, repair.__file__, '--codex-home', str(self.home),
                               '--all', *options], capture_output=True, text=True)

    def test_batch_skips_loaded_task_and_repairs_next_task(self):
        locks = self.prepare_locks()
        second, rollout = self.add_second_task()
        with (locks / (TASK + '.lock')).open('w+b') as loaded:
            fcntl.flock(loaded, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_batch('--apply', '--unloaded')
        self.assertEqual(result.returncode, 2, result.stderr)
        results = json.loads(result.stdout)
        self.assertEqual(results[0]['state'], 'waiting')
        self.assertTrue(results[1]['conversation_unchanged'])
        self.assertEqual(self.rollout.read_bytes(), self.original)
        self.assertEqual(self.row()[0], 'openai')
        self.assertEqual(self.row(second)[0], 'model-harbor')
        self.assertEqual(rollout.read_bytes().split(b'\n', 1)[1], self.history)

    def test_batch_reports_invalid_task_and_repairs_next_task(self):
        self.prepare_locks()
        second, _ = self.add_second_task()
        self.metadata['payload']['model_provider'] = 'model-harbor'
        self.rollout.write_bytes(json.dumps(self.metadata).encode() + b'\n' + self.history)
        original = self.rollout.read_bytes()
        result = self.run_batch('--apply', '--unloaded')
        self.assertEqual(result.returncode, 1, result.stderr)
        results = json.loads(result.stdout)
        self.assertEqual(results[0]['state'], 'error')
        self.assertIn('disagree', results[0]['error'])
        self.assertTrue(results[1]['conversation_unchanged'])
        self.assertEqual(self.rollout.read_bytes(), original)
        self.assertEqual(self.row()[0], 'openai')
        self.assertEqual(self.row(second)[0], 'model-harbor')

    def test_batch_preview_reports_all_tasks_without_writing(self):
        second, rollout = self.add_second_task()
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET model=? WHERE id=?', ('harbor/unknown/model', TASK))
        original_second = rollout.read_bytes()
        result = self.run_batch()
        self.assertEqual(result.returncode, 1, result.stderr)
        results = json.loads(result.stdout)
        self.assertEqual(results[0]['state'], 'error')
        self.assertTrue(results[1]['preview'])
        self.assertEqual(self.row(second)[0], 'openai')
        self.assertEqual(rollout.read_bytes(), original_second)
        self.assertFalse((self.home / 'model-harbor-task-backups').exists())

    def test_live_repair_refuses_loaded_writer_then_repairs_after_release(self):
        locks = self.prepare_locks()
        with (locks / (TASK + '.lock')).open('w+b') as loaded:
            fcntl.flock(loaded, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(repair.TaskInUse):
                repair.repair_unloaded(self.home, TASK)
            self.assertEqual(self.row()[0], 'openai')
            self.assertEqual(self.rollout.read_bytes(), self.original)
        result = repair.repair_unloaded(self.home, TASK)
        self.assertTrue(result['conversation_unchanged'])
        self.assertEqual(self.row()[0], 'model-harbor')

    def test_coordination_lock_blocks_cleanup_and_repair_race(self):
        locks = self.prepare_locks()
        with (locks / '.coordination.lock').open('r+b') as cleanup:
            fcntl.flock(cleanup, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(repair.TaskInUse):
                repair.repair_unloaded(self.home, TASK)
        self.assertEqual(self.rollout.read_bytes(), self.original)

    def test_live_repair_requires_codex_lock_namespace(self):
        with self.assertRaisesRegex(ValueError, 'locks are unavailable'):
            repair.repair_unloaded(self.home, TASK)
        self.assertEqual(self.row()[0], 'openai')

    def test_monitor_opt_in_survives_restart_and_preserves_unrelated_task(self):
        self.prepare_locks()
        monitor = repair.RepairMonitor(self.home)
        monitor.run_once()
        self.assertEqual(monitor.snapshot, {'enabled': False, 'state': 'waiting', 'pending': 1, 'repaired': 0})
        self.assertEqual(self.row()[0], 'openai')
        monitor.set_enabled(True)
        restarted = repair.RepairMonitor(self.home)
        restarted.run_once()
        self.assertEqual(restarted.snapshot, {'enabled': True, 'state': 'ready', 'pending': 0, 'repaired': 1})
        restarted.run_once()
        self.assertEqual(restarted.snapshot['repaired'], 1)
        self.assertEqual(self.row('unrelated'), ('openai', 'native-model', 'Other task'))
        self.assertEqual(restarted.settings.stat().st_mode & 0o777, 0o600)
        self.assertEqual(restarted.report.stat().st_mode & 0o777, 0o600)

    def test_monitor_waits_for_loaded_task_without_error_or_backup(self):
        locks = self.prepare_locks()
        monitor = repair.RepairMonitor(self.home)
        monitor.set_enabled(True)
        with (locks / (TASK + '.lock')).open('w+b') as loaded:
            fcntl.flock(loaded, fcntl.LOCK_EX | fcntl.LOCK_NB)
            monitor.run_once()
            self.assertEqual(monitor.snapshot['state'], 'waiting')
            self.assertFalse((self.home / 'model-harbor-task-backups').exists())
        monitor.run_once()
        self.assertEqual(monitor.snapshot['pending'], 0)

    def test_unknown_models_stop_monitor_without_repeated_backups(self):
        self.prepare_locks()
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('UPDATE threads SET model=? WHERE id=?', ('harbor/unknown/model', TASK))
        monitor = repair.RepairMonitor(self.home)
        monitor.set_enabled(True)
        monitor.run_once()
        self.assertEqual(monitor.snapshot['state'], 'error')
        report = monitor.report.read_bytes()
        monitor.run_once()
        self.assertEqual(monitor.report.read_bytes(), report)
        self.assertFalse((self.home / 'model-harbor-task-backups').exists())

    def test_disabling_automatic_repairs_persists_and_does_not_write_routes(self):
        self.prepare_locks()
        monitor = repair.RepairMonitor(self.home)
        monitor.set_enabled(True)
        monitor.set_enabled(False)
        repair.RepairMonitor(self.home).run_once()
        self.assertEqual(self.rollout.read_bytes(), self.original)

    def test_report_write_failure_keeps_retry_available(self):
        self.prepare_locks()
        monitor = repair.RepairMonitor(self.home)
        monitor.set_enabled(True)
        with patch.object(repair, 'private_json', side_effect=OSError('Disk full')):
            monitor.run_once()
        self.assertEqual(monitor.snapshot['state'], 'error')
        monitor.set_enabled(True)
        monitor.run_once()
        self.assertEqual(monitor.snapshot['state'], 'ready')
        self.assertEqual(self.row()[0], 'model-harbor')

    def crash_after_rollout_publication(self):
        self.prepare_locks()
        code = """
import importlib.util, os, pathlib, sys
from contextlib import contextmanager
spec = importlib.util.spec_from_file_location('repair', sys.argv[1])
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
original = repair.open_database
@contextmanager
def database(home, writable=False):
    with original(home, writable) as connection:
        if not writable:
            yield connection
        else:
            class CrashBeforeCommit:
                execute = connection.execute
                def commit(self): os._exit(83)
            yield CrashBeforeCommit()
repair.open_database = database
repair.repair_unloaded(pathlib.Path(sys.argv[2]), sys.argv[3])
"""
        result = subprocess.run([sys.executable, '-c', code, repair.__file__, str(self.home), TASK])
        self.assertEqual(result.returncode, 83)
        self.assertEqual(self.row()[0], 'openai')
        self.assertEqual(json.loads(self.rollout.read_bytes().split(b'\n', 1)[0])['payload']['model_provider'], 'model-harbor')

    def test_interrupted_commit_recovers_exact_route_and_history(self):
        self.crash_after_rollout_publication()
        self.assertEqual(repair.recover_pending(self.home), [{'thread_id': TASK, 'recovered': True}])
        self.assertEqual(self.row()[0], 'model-harbor')
        self.assertEqual(self.rollout.read_bytes().split(b'\n', 1)[1], self.history)
        self.assertEqual(repair.recover_pending(self.home), [])

    def test_interrupted_commit_refuses_changed_conversation(self):
        self.crash_after_rollout_publication()
        with self.rollout.open('ab') as out:
            out.write(b'{"type":"new-event"}\n')
        with self.assertRaisesRegex(ValueError, 'interrupted repair changed'):
            repair.recover_pending(self.home)
        self.assertEqual(self.row()[0], 'openai')


if __name__ == '__main__':
    unittest.main()
