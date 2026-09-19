import importlib.util
import json
from pathlib import Path
import unittest

MODULE = Path(__file__).parents[1] / 'ModelHarbor/Support/codex_app_tools.py'
SPEC = importlib.util.spec_from_file_location('codex_app_tools', MODULE)
tools_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tools_module)

SNAPSHOT = Path(__file__).parents[1] / 'ModelHarbor/Support/codex_app_tools.json'


class CodexAppToolsTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = tools_module.load_snapshot(SNAPSHOT)

    def test_snapshot_is_valid(self):
        self.assertIsNotNone(self.snapshot)
        self.assertIn('codex_app', [entry['name'] for entry in self.snapshot['tools']])
        self.assertGreaterEqual(len(self.snapshot['tools'][0]['tools']), 10)

    def test_missing_namespace_is_appended(self):
        merged, changed = tools_module.merge([{'type': 'function', 'name': 'exec_command'}], self.snapshot)
        self.assertTrue(changed)
        namespaces = [tool for tool in merged if tool.get('type') == 'namespace']
        self.assertEqual(namespaces[0]['name'], 'codex_app')
        self.assertTrue(any(child['name'] == 'read_thread' for child in namespaces[0]['tools']))

    def test_client_definitions_win(self):
        client = {'type': 'namespace', 'name': 'codex_app', 'tools': [
            {'type': 'function', 'name': 'read_thread', 'description': 'CLIENT VERSION',
             'parameters': {'type': 'object'}}]}
        merged, changed = tools_module.merge([client], self.snapshot)
        self.assertTrue(changed)
        namespace = [tool for tool in merged if tool.get('type') == 'namespace'][0]
        read_thread = [t for t in namespace['tools'] if t['name'] == 'read_thread'][0]
        self.assertEqual(read_thread['description'], 'CLIENT VERSION')
        self.assertTrue(any(t['name'] == 'fork_thread' for t in namespace['tools']))

    def test_complete_client_namespace_is_unchanged(self):
        full = copy = {'type': 'namespace', 'name': 'codex_app',
                       'tools': json.loads(json.dumps(self.snapshot['tools'][0]['tools']))}
        merged, changed = tools_module.merge([copy], self.snapshot)
        self.assertFalse(changed)

    def test_invalid_snapshot_never_merges(self):
        for bad in ({}, {'tools': 'x'}, {'tools': [{'type': 'function'}]},
                    {'tools': [{'type': 'namespace', 'tools': [{'type': 'custom', 'name': 'x'}]}]}):
            merged, changed = tools_module.merge([], bad)
            self.assertFalse(changed)

    def test_staleness_warns_on_version_change(self):
        self.assertIsNone(tools_module.staleness(self.snapshot, '0.155.0-alpha.9.2'))
        self.assertIsNotNone(tools_module.staleness(self.snapshot, '0.200.0'))
        self.assertIsNone(tools_module.staleness(self.snapshot, None))

    def test_malformed_snapshot_file_returns_none(self):
        self.assertIsNone(tools_module.load_snapshot(Path('/nonexistent/none.json')))


if __name__ == '__main__':
    unittest.main()
