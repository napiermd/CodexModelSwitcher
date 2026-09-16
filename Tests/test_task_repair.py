import importlib.util
from contextlib import closing, contextmanager
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('task_repair', pathlib.Path(__file__).resolve().parents[1] / 'scripts/repair-task-provider.py')
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


if __name__ == '__main__':
    unittest.main()
