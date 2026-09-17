"""Bounded, credential-free threading tests for Azure admission permits."""
import concurrent.futures
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

SUPPORT = Path(__file__).resolve().parents[1] / 'ModelHarbor' / 'Support'
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
from azure_admission import AdmissionCancelled, AdmissionFull, AdmissionQueue, AdmissionTimeout

KEY = ('https://fixture.openai.azure.com/openai/v1', 'Exact-Deployment')
EMPTY = {'keys': 0, 'active': 0, 'waiting': 0}


class AzureAdmissionTests(unittest.TestCase):
    def pool(self, workers=20):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        self.addCleanup(pool.shutdown, wait=True, cancel_futures=True)
        return pool

    def hold(self, queue, key=KEY):
        permit = queue.acquire(key)
        self.addCleanup(permit.release)
        return permit

    def wait_counts(self, queue, **expected):
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            snapshot = queue.snapshot()
            if all(snapshot[name] == value for name, value in expected.items()):
                return snapshot
            threading.Event().wait(.001)
        self.fail(f'Admission counters did not reach {expected}: {queue.snapshot()}')

    def acquire_and_release(self, queue, key=KEY, **kwargs):
        with queue.acquire(key, **kwargs):
            return True

    def test_default_two_active_and_one_waiting_until_explicit_release(self):
        queue, pool = AdmissionQueue(wait_seconds=1), self.pool()
        first, second = self.hold(queue), self.hold(queue)
        granted, response_finished = threading.Event(), threading.Event()
        self.addCleanup(response_finished.set)
        def consume_response():
            with queue.acquire(KEY):
                granted.set()
                if not response_finished.wait(1):
                    raise TimeoutError('Synthetic response never finished')
        future = pool.submit(consume_response)
        self.assertEqual(self.wait_counts(queue, waiting=1), {'keys': 1, 'active': 2, 'waiting': 1})
        self.assertFalse(granted.is_set())
        first.release()
        self.assertTrue(granted.wait(.5))
        self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 2, 'waiting': 0})
        second.release()
        self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 1, 'waiting': 0})
        response_finished.set()
        future.result(timeout=1)
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_default_waiting_capacity_is_sixteen_and_overflow_never_grants(self):
        queue, pool = AdmissionQueue(wait_seconds=2), self.pool()
        first, second = self.hold(queue), self.hold(queue)
        futures = [pool.submit(self.acquire_and_release, queue) for _ in range(16)]
        self.wait_counts(queue, waiting=16)
        with self.assertRaises(AdmissionFull):
            queue.acquire(KEY)
        self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 2, 'waiting': 16})
        first.release()
        second.release()
        self.assertEqual([future.result(timeout=1) for future in futures], [True] * 16)
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_fifo_waiters_cannot_be_overtaken_by_new_arrival_racing_release(self):
        pool = self.pool()
        for attempt in range(12):
            with self.subTest(attempt=attempt):
                queue = AdmissionQueue(active_limit=1, wait_seconds=1)
                held = self.hold(queue)
                order = []
                def run(label, gate=None):
                    if gate is not None and not gate.wait(1):
                        raise TimeoutError('Synthetic start gate timed out')
                    with queue.acquire(KEY):
                        order.append(label)
                first = pool.submit(run, 'first')
                self.wait_counts(queue, waiting=1)
                second = pool.submit(run, 'second')
                self.wait_counts(queue, waiting=2)
                start = threading.Event()
                late = pool.submit(run, 'late', start)
                held.release()
                start.set()
                for future in (first, second, late):
                    future.result(timeout=1)
                self.assertEqual(order, ['first', 'second', 'late'])
                self.assertEqual(queue.snapshot(), EMPTY)

    def test_cancelled_head_is_removed_without_dispatch_or_blocking_next_waiter(self):
        queue, pool = AdmissionQueue(active_limit=1, wait_seconds=1), self.pool()
        held = self.hold(queue)
        cancelled = threading.Event()
        head = pool.submit(self.acquire_and_release, queue, cancelled=cancelled.is_set)
        self.wait_counts(queue, waiting=1)
        next_waiter = pool.submit(self.acquire_and_release, queue)
        self.wait_counts(queue, waiting=2)
        cancelled.set()
        with self.assertRaises(AdmissionCancelled):
            head.result(timeout=.3)
        self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 1, 'waiting': 1})
        held.release()
        self.assertTrue(next_waiter.result(timeout=1))
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_cancellation_is_rechecked_immediately_before_an_available_grant(self):
        queue = AdmissionQueue()
        checks = iter((False, True))
        with self.assertRaises(AdmissionCancelled):
            queue.acquire(KEY, cancelled=lambda: next(checks))
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_cancellation_observed_while_release_races_never_grants(self):
        queue, pool = AdmissionQueue(active_limit=1, wait_seconds=1), self.pool()
        held = self.hold(queue)
        cancel = threading.Event()
        future = pool.submit(self.acquire_and_release, queue, cancelled=cancel.is_set)
        self.wait_counts(queue, waiting=1)
        cancel.set()
        held.release()
        with self.assertRaises(AdmissionCancelled):
            future.result(timeout=1)
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_waiting_callback_exception_removes_waiter_and_preserves_active_permit(self):
        queue, pool = AdmissionQueue(active_limit=1, wait_seconds=1), self.pool()
        held = self.hold(queue)
        fail = threading.Event()
        def cancelled():
            if fail.is_set():
                raise RuntimeError('Synthetic cancellation callback failure')
            return False
        future = pool.submit(self.acquire_and_release, queue, cancelled=cancelled)
        self.wait_counts(queue, waiting=1)
        fail.set()
        with self.assertRaisesRegex(RuntimeError, 'Synthetic cancellation callback failure'):
            future.result(timeout=.3)
        self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 1, 'waiting': 0})
        held.release()
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_callback_exception_before_available_grant_leaves_no_key(self):
        queue = AdmissionQueue()
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('Synthetic callback failure')
            return False
        with self.assertRaises(RuntimeError):
            queue.acquire(KEY, cancelled=cancelled)
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_cancel_poll_wait_never_exceeds_100ms_without_release_notification(self):
        queue, pool = AdmissionQueue(active_limit=1, wait_seconds=1), self.pool()
        held = self.hold(queue)
        cancel = threading.Event()
        with patch.object(queue._condition, 'wait', wraps=queue._condition.wait) as waits:
            future = pool.submit(self.acquire_and_release, queue, cancelled=cancel.is_set)
            self.wait_counts(queue, waiting=1)
            started = time.monotonic()
            cancel.set()
            with self.assertRaises(AdmissionCancelled):
                future.result(timeout=.25)
            self.assertLess(time.monotonic() - started, .2)  # Allows OS scheduling delay.
            self.assertTrue(waits.call_args_list)
            self.assertTrue(all(0 < call.kwargs['timeout'] <= .1 for call in waits.call_args_list))
        self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 1, 'waiting': 0})
        held.release()
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_own_queue_timeout_does_not_include_age_of_held_permit(self):
        queue = AdmissionQueue(active_limit=1, wait_seconds=.04)
        held = self.hold(queue)
        threading.Event().wait(.05)
        started = time.monotonic()
        with self.assertRaises(AdmissionTimeout):
            queue.acquire(KEY)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, .035)
        self.assertLess(elapsed, .2)
        self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 1, 'waiting': 0})
        held.release()
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_timeout_override_shortens_but_cannot_extend_policy(self):
        queue = AdmissionQueue(active_limit=1, wait_seconds=.06)
        held = self.hold(queue)
        for timeout, maximum in ((.02, .1), (10, .2)):
            with self.subTest(timeout=timeout):
                started = time.monotonic()
                with self.assertRaises(AdmissionTimeout):
                    queue.acquire(KEY, timeout=timeout)
                elapsed = time.monotonic() - started
                self.assertGreaterEqual(elapsed, min(timeout, .06) * .9)
                self.assertLess(elapsed, maximum)
                self.assertEqual(queue.snapshot()['waiting'], 0)
        held.release()
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_expired_waiter_cannot_grant_when_capacity_becomes_available(self):
        now = [0.0]
        queue, pool = AdmissionQueue(active_limit=1, wait_seconds=30, clock=lambda: now[0]), self.pool()
        held = self.hold(queue)
        future = pool.submit(self.acquire_and_release, queue)
        self.wait_counts(queue, waiting=1)
        now[0] = 31
        held.release()
        with self.assertRaises(AdmissionTimeout):
            future.result(timeout=1)
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_deployment_case_and_endpoint_have_independent_capacity(self):
        queue, pool = AdmissionQueue(active_limit=1, wait_seconds=1), self.pool()
        held = self.hold(queue)
        waiting = pool.submit(self.acquire_and_release, queue)
        self.wait_counts(queue, waiting=1)
        other_deployment = self.hold(queue, (KEY[0], KEY[1].lower()))
        other_endpoint = self.hold(queue, ('https://other.openai.azure.com/openai/v1', KEY[1]))
        self.assertEqual(queue.snapshot(), {'keys': 3, 'active': 3, 'waiting': 1})
        other_deployment.release()
        other_endpoint.release()
        held.release()
        self.assertTrue(waiting.result(timeout=1))
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_equivalent_endpoint_spellings_share_capacity(self):
        queue = AdmissionQueue(active_limit=1, max_waiting=0)
        held = self.hold(queue)
        with self.assertRaises(AdmissionFull):
            queue.acquire(('https://FIXTURE.openai.azure.com:443/openai/v1/', KEY[1]))
        held.release()
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_zero_waiting_capacity_can_grant_available_slot_but_rejects_full(self):
        queue = AdmissionQueue(active_limit=1, max_waiting=0)
        held = self.hold(queue)
        with self.assertRaises(AdmissionFull):
            queue.acquire(KEY)
        self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 1, 'waiting': 0})
        held.release()
        with queue.acquire(KEY):
            self.assertEqual(queue.snapshot()['active'], 1)
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_idempotent_release_is_safe_when_multiple_threads_race(self):
        queue, pool = AdmissionQueue(active_limit=1, max_waiting=0), self.pool()
        held = self.hold(queue)
        barrier = threading.Barrier(8, timeout=1)
        def release():
            barrier.wait()
            held.release()
        for future in [pool.submit(release) for _ in range(8)]:
            future.result(timeout=1)
        self.assertEqual(queue.snapshot(), EMPTY)
        with queue.acquire(KEY):
            held.release()
            with self.assertRaises(AdmissionFull):
                queue.acquire(KEY)
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_response_exception_context_releases_capacity(self):
        queue = AdmissionQueue()
        with self.assertRaisesRegex(RuntimeError, 'Synthetic response failed'):
            with queue.acquire(KEY):
                raise RuntimeError('Synthetic response failed')
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_completed_keys_are_removed_and_snapshot_contains_only_counts(self):
        queue = AdmissionQueue()
        for index in range(100):
            with queue.acquire((KEY[0], 'deployment-' + str(index))):
                self.assertEqual(queue.snapshot(), {'keys': 1, 'active': 1, 'waiting': 0})
            self.assertEqual(queue.snapshot(), EMPTY)
        self.assertNotIn('deployment', json.dumps(queue.snapshot()))
        self.assertNotIn('azure.com', json.dumps(queue.snapshot()))

    def test_invalid_policies_and_timeout_overrides_are_rejected(self):
        for kwargs in ({'active_limit': 0}, {'active_limit': True}, {'active_limit': 1.5},
                       {'max_waiting': -1}, {'max_waiting': False}, {'max_waiting': 1.5},
                       {'wait_seconds': 0}, {'wait_seconds': float('nan')},
                       {'wait_seconds': float('inf')}, {'wait_seconds': True},
                       {'poll_seconds': 0}, {'poll_seconds': .101}, {'poll_seconds': float('nan')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AdmissionQueue(**kwargs)
        queue = AdmissionQueue()
        self.assertEqual(queue.wait_seconds, 30)
        for timeout in (0, -1, float('nan'), float('inf'), True, '1'):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                queue.acquire(KEY, timeout=timeout)
        self.assertEqual(queue.snapshot(), EMPTY)

    def test_invalid_and_credential_bearing_keys_are_rejected_without_storing_secrets(self):
        queue = AdmissionQueue()
        secret = 'synthetic-never-store-this'
        keys = [None, 'endpoint', (KEY[0],), (KEY[0], ''), (KEY[0], None),
                ('file:///tmp/fixture', 'model'), ('https://', 'model'),
                ('https://name:' + secret + '@fixture.openai.azure.com/openai/v1', 'model'),
                (KEY[0] + '?api-key=' + secret, 'model'), (KEY[0] + '#' + secret, 'model')]
        for key in keys:
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as caught:
                    queue.acquire(key)
                self.assertNotIn(secret, str(caught.exception))
                self.assertEqual(queue.snapshot(), EMPTY)


if __name__ == '__main__':
    unittest.main()
