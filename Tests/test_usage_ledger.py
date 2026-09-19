import importlib.util
import json
import os
from pathlib import Path
import threading
import unittest

PATH = Path(__file__).parents[1] / 'ModelHarbor/Support/usage_ledger.py'
SPEC = importlib.util.spec_from_file_location('usage_ledger', PATH)
ledger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ledger)


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.id().replace('.', '/')).name
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'state' / 'usage-events.jsonl'
        ledger.configure(self.path)

    def tearDown(self):
        ledger.configure(None)
        self.tmp.cleanup()

    def event(self, **overrides):
        base = {'provider': 'azure', 'model': 'harbor/azure/fixture', 'status': 'completed',
                'http_status': 200, 'duration_seconds': 1.5, 'ttft_seconds': 0.4,
                'usage': {'input_tokens': 100, 'cached_input_tokens': 80,
                          'output_tokens': 20, 'total_tokens': 120}}
        base.update(overrides)
        return base

    def test_record_and_read_round_trip(self):
        self.assertTrue(ledger.record(self.event()))
        events = ledger.read(self.path)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['provider'], 'azure')
        self.assertEqual(event['usage'], {'input_tokens': 100, 'cached_input_tokens': 80,
                                          'output_tokens': 20, 'total_tokens': 120})
        self.assertEqual(event['usage_source'], 'provider')
        self.assertFalse(event['retried'])

    def test_nested_cached_usage_is_normalized(self):
        self.assertTrue(ledger.record(self.event(usage={
            'input_tokens': 10, 'input_tokens_details': {'cached_tokens': 7}})))
        self.assertEqual(ledger.read(self.path)[0]['usage']['cached_input_tokens'], 7)

    def test_content_never_survives(self):
        event = self.event()
        event['prompt'] = 'PRIVATE clinical text'
        event['usage'] = {'input_tokens': 5, 'raw': 'PRIVATE'}
        self.assertTrue(ledger.record(event))
        data = self.path.read_text()
        self.assertNotIn('PRIVATE', data)
        self.assertNotIn('prompt', data)

    def test_missing_required_fields_rejected(self):
        for field in ('provider', 'model', 'status'):
            event = self.event()
            del event[field]
            self.assertFalse(ledger.record(event))
        self.assertEqual(ledger.read(self.path), [])

    def test_window_applies_before_limit(self):
        for index in range(20):
            self.assertTrue(ledger.record(self.event(at=1000 + index)))
        events = ledger.read(self.path, since=1010, limit=5)
        self.assertEqual([event['at'] for event in events], [1015, 1016, 1017, 1018, 1019])

    def test_malformed_lines_are_skipped(self):
        ledger.record(self.event())
        with open(self.path, 'ab') as handle:
            handle.write(b'{not json\n')
            handle.write(b'{"schema": 999}\n')
        self.assertEqual(len(ledger.read(self.path)), 1)

    def test_rotation_keeps_current_and_previous(self):
        ledger.MAX_BYTES = 200
        try:
            for index in range(10):
                ledger.record(self.event(at=2000 + index))
            previous = self.path.with_name(self.path.name + '.1')
            self.assertTrue(previous.exists())
            self.assertTrue(self.path.exists())
            current = ledger.read(self.path)
            self.assertTrue(current)
            self.assertEqual(current[-1]['at'], 2009)
        finally:
            ledger.MAX_BYTES = 32 * 1024 * 1024

    def test_permissions_are_private(self):
        ledger.record(self.event())
        self.assertEqual(oct(self.path.stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct(self.path.parent.stat().st_mode & 0o777), '0o700')

    def test_concurrent_writers_do_not_tear_lines(self):
        def write(index):
            for offset in range(25):
                ledger.record(self.event(at=3000 + index * 100 + offset))
        threads = [threading.Thread(target=write, args=(index,)) for index in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        events = ledger.read(self.path, limit=100000)
        self.assertEqual(len(events), 200)

    def test_write_failure_never_raises_and_unwritable_dir_disables_cleanly(self):
        ledger.configure(Path(self.tmp.name) / 'missing-parent' / 'ledger.jsonl')
        os.chmod(self.tmp.name, 0o500)
        try:
            ledger.configure(Path('/proc/cannot-exist/ledger.jsonl'))
            self.assertFalse(ledger.record(self.event()))
        finally:
            os.chmod(self.tmp.name, 0o700)

    def test_estimated_usage_is_labeled(self):
        self.assertTrue(ledger.record(self.event(usage=None, estimated=4321)))
        event = ledger.read(self.path)[0]
        self.assertEqual(event['usage_source'], 'estimated')
        self.assertEqual(event['usage']['input_tokens'], 4321)


if __name__ == '__main__':
    unittest.main()
