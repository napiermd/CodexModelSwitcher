import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

PATH = Path(__file__).parents[1] / 'scripts/report-context-drift.py'
SPEC = importlib.util.spec_from_file_location('context_drift', PATH)
drift_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drift_module)


def event(model, tokens, **overrides):
    base = {'schema': 1, 'provider': 'azure', 'model': model, 'status': 'completed',
            'http_status': 200, 'usage': {'input_tokens': tokens}, 'usage_source': 'provider',
            'retried': False, 'at': 1000.0}
    base.update(overrides)
    return base


class ContextDriftTests(unittest.TestCase):
    def catalog(self):
        return {'models': [
            {'slug': 'harbor/azure/small', 'context_window': 100000, 'max_context_window': 200000},
            {'slug': 'harbor/azure/large', 'context_window': 400000, 'max_context_window': 400000}]}

    def test_over_window_acceptance_reports_drift(self):
        rows = drift_module.drift([event('harbor/azure/small', 121600)], self.catalog())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'drift')
        self.assertEqual(rows[0]['ratio'], 1.22)
        self.assertEqual(rows[0]['auto_compact'], None)

    def test_estimated_and_failed_events_are_not_evidence(self):
        rows = drift_module.drift([
            event('harbor/azure/small', 150000, usage_source='estimated'),
            event('harbor/azure/small', 150000, http_status=500),
            event('harbor/azure/small', 150000, status='failed'),
            event('harbor/azure/small', 150000, retried=True),
        ], self.catalog())
        self.assertEqual(rows, [])

    def test_implausible_observation_is_not_believed(self):
        rows = drift_module.drift([event('harbor/azure/small', 100000 * 65)], self.catalog())
        self.assertEqual(rows[0]['status'], 'implausible')

    def test_unknown_slug_is_reported_not_dropped(self):
        rows = drift_module.drift([event('harbor/azure/unknown', 50000)], self.catalog())
        self.assertEqual(rows[0]['status'], 'not-in-catalog')

    def test_worst_first_ordering(self):
        rows = drift_module.drift([
            event('harbor/azure/small', 110000),
            event('harbor/azure/large', 800000),
        ], self.catalog())
        self.assertEqual([row['slug'] for row in rows],
                         ['harbor/azure/large', 'harbor/azure/small'])

    def test_content_never_appears_in_output(self):
        evil = event('harbor/azure/small', 150000)
        evil['note'] = 'PRIVATE'
        rows = drift_module.drift([evil], self.catalog())
        self.assertNotIn('PRIVATE', json.dumps(rows))

    def test_cli_missing_inputs_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'missing.jsonl'
            catalog = Path(tmp) / 'catalog.json'
            catalog.write_text(json.dumps(self.catalog()))
            import subprocess, sys
            result = subprocess.run([sys.executable, str(PATH), '--ledger', str(missing),
                                     '--catalog', str(catalog)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)['status'], 'ledger-unavailable')


if __name__ == '__main__':
    unittest.main()
