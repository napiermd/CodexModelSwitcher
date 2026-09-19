import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/verify-azure-history.py'
spec = importlib.util.spec_from_file_location('verify_azure_history', SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class Control:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls = []

    def owner_request(self, *args):
        self.calls.append(args)
        return b'{}', self.runtime


class VerifyAzureHistoryTests(unittest.TestCase):
    token_path = Path('/fixture/token')

    def test_matching_installed_runtime_is_authenticated_before_the_live_proof(self):
        expected = 'a' * 64
        control = Control({'runtime_id': expected})

        self.assertTrue(verify.installed_runtime_matches(control, 48118, self.token_path, expected))
        self.assertEqual(control.calls, [('GET', '/harbor/status', b'', 48118, self.token_path)])

    def test_mismatched_installed_runtime_stops_the_live_proof(self):
        control = Control({'runtime_id': 'b' * 64})

        with self.assertRaisesRegex(verify.VerificationError, 'expected activation'):
            verify.installed_runtime_matches(control, 48118, self.token_path, 'a' * 64)

    def test_invalid_authenticated_runtime_is_not_accepted(self):
        control = Control({'runtime_id': 'not-a-runtime'})

        with self.assertRaisesRegex(verify.VerificationError, 'valid runtime identity'):
            verify.installed_runtime_matches(control, 48118, self.token_path, 'a' * 64)


if __name__ == '__main__':
    unittest.main()
