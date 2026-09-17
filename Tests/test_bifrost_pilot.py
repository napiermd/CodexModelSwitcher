import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'bifrost_pilot', Path(__file__).resolve().parents[1] / 'experiments/bifrost/pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class FakeDocker:
    def __init__(self, *, logs='Bifrost v2.2.0', file_content='', omit_last_case=False,
                 before_suite=None):
        self.calls = []
        self.logs = logs
        self.file_content = file_content
        self.omit_last_case = omit_last_case
        self.before_suite = before_suite

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('image', 'inspect'):
            return '{}'
        if args[0] == 'exec':
            if '--health' in args:
                return '{"ready": true}'
            if '--suite' in args:
                if self.before_suite:
                    self.before_suite()
                cases = json.loads(args[-1])
                if self.omit_last_case:
                    cases = cases[:-1]
                return json.dumps({'cases': [{'case': case, 'passed': True} for case in cases]})
        if args[0] == 'inspect':
            return '{"Running": true, "OOMKilled": false}'
        if args[0] == 'logs':
            return self.logs
        if args[0] == 'cp':
            if args[1].endswith(':/app/data/.'):
                (Path(args[2]) / 'audit.log').write_text(self.file_content)
            return ''
        if args[0] in ('create', 'start', 'rm') or args[:2] in (
                ('network', 'create'), ('network', 'rm')):
            return ''
        raise AssertionError(f'Unexpected Docker command: {args}')


class BifrostPilotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'reports' / 'result.json'

    def run_pilot(self, docker, cases=None):
        argv = ['pilot.py', '--output', str(self.output)]
        if cases is not None:
            argv += ['--cases', *cases]
        with patch.object(pilot, 'docker', docker), patch.object(sys, 'argv', argv), \
                patch.object(pilot.platform, 'machine', return_value='arm64'), \
                contextlib.redirect_stdout(io.StringIO()):
            pilot.main()
        return json.loads(self.output.read_text())

    def assert_owned_resources_removed(self, docker):
        containers = [call[call.index('--name') + 1] for call in docker.calls if call[0] == 'create']
        networks = [call[-1] for call in docker.calls if call[:2] == ('network', 'create')]
        self.assertEqual(len(containers), 2)
        self.assertEqual(len(networks), 1)
        cleanup = [call for call in docker.calls if call[0] == 'rm' or call[:2] == ('network', 'rm')]
        self.assertEqual(cleanup, [('rm', '-f', '-v', name) for name in reversed(containers)]
                         + [('network', 'rm', networks[0])])

    def test_invalid_report_parent_is_rejected_before_resource_creation(self):
        self.output.parent.write_text('This is a file, not a directory')
        docker = FakeDocker()
        with self.assertRaises(FileExistsError):
            self.run_pilot(docker)
        self.assertFalse(any(call[0] == 'create' or call[:2] == ('network', 'create')
                             for call in docker.calls))

    def test_unwritable_report_is_rejected_before_resource_creation(self):
        docker = FakeDocker()
        original_write = Path.write_text
        def deny_report_write(path, *args, **kwargs):
            if path.parent == self.output.parent:
                raise PermissionError('Synthetic report permission failure')
            return original_write(path, *args, **kwargs)
        with patch.object(Path, 'write_text', deny_report_write), self.assertRaises(PermissionError):
            self.run_pilot(docker)
        self.assertFalse(any(call[0] == 'create' or call[:2] == ('network', 'create')
                             for call in docker.calls))

    def test_report_becoming_unwritable_still_removes_owned_resources(self):
        denied = False
        def deny_after_start():
            nonlocal denied
            denied = True
        docker = FakeDocker(before_suite=deny_after_start)
        original_write = Path.write_text
        def deny_report_write(path, *args, **kwargs):
            if denied and path.parent == self.output.parent:
                raise PermissionError('Synthetic report permission failure')
            return original_write(path, *args, **kwargs)
        with patch.object(Path, 'write_text', deny_report_write), self.assertRaises(PermissionError):
            self.run_pilot(docker)
        self.assert_owned_resources_removed(docker)

    def test_report_parent_becoming_invalid_still_removes_owned_resources(self):
        def replace_parent_with_file():
            if self.output.exists():
                self.output.unlink()
            self.output.parent.mkdir(parents=True, exist_ok=True)
            self.output.parent.rmdir()
            self.output.parent.write_text('Synthetic destination change')
        docker = FakeDocker(before_suite=replace_parent_with_file)
        with self.assertRaises(FileExistsError):
            self.run_pilot(docker)
        self.assert_owned_resources_removed(docker)

    def test_complete_matrix_with_verified_content_and_version_passes_synthetic_gates(self):
        docker = FakeDocker()
        report = self.run_pilot(docker)
        self.assertEqual(len(report['cases']), 17)
        self.assertTrue(report['matrix_complete'])
        self.assertTrue(report['all_synthetic_gates_passed'])
        self.assertTrue(report['production_decision'].startswith('no-go'))
        self.assert_owned_resources_removed(docker)

    def test_partial_matrix_does_not_pass_all_synthetic_gates(self):
        report = self.run_pilot(FakeDocker(), cases=['basic'])
        self.assertTrue(report['cases'][0]['passed'])
        self.assertFalse(report['matrix_complete'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_missing_requested_case_does_not_complete_matrix_or_pass_gates(self):
        report = self.run_pilot(FakeDocker(omit_last_case=True))
        self.assertEqual(len(report['cases']), 16)
        self.assertFalse(report['requested_cases_complete'])
        self.assertFalse(report['matrix_complete'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_console_content_leak_does_not_pass_all_synthetic_gates(self):
        report = self.run_pilot(FakeDocker(logs='v2.2.0 SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG'))
        self.assertTrue(report['synthetic_content_in_console_logs'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_runtime_content_leak_does_not_pass_all_synthetic_gates(self):
        report = self.run_pilot(FakeDocker(file_content='SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG'))
        self.assertTrue(report['synthetic_content_in_runtime_files'])
        self.assertFalse(report['all_synthetic_gates_passed'])

    def test_unverified_version_does_not_pass_all_synthetic_gates(self):
        report = self.run_pilot(FakeDocker(logs='Bifrost unknown version'))
        self.assertFalse(report['transport_version_banner_verified'])
        self.assertFalse(report['all_synthetic_gates_passed'])


if __name__ == '__main__':
    unittest.main()
