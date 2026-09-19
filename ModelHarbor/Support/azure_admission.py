"""Process-local Azure admission; callers retain permits through response completion."""
from collections import deque
import math
import threading
import time
from urllib.parse import urlsplit


class AdmissionError(Exception):
    pass


class AdmissionTimeout(AdmissionError):
    pass


class AdmissionFull(AdmissionError):
    pass


class AdmissionCancelled(AdmissionError):
    pass


class _Bucket:
    def __init__(self):
        self.active = 0
        self.waiting = deque()


class AdmissionPermit:
    def __init__(self, admission, key, bucket):
        self._admission = admission
        self._key = key
        self._bucket = bucket
        self._released = False

    def release(self):
        with self._admission._condition:
            if self._released:
                return
            self._released = True
            self._bucket.active -= 1
            self._admission._prune(self._key, self._bucket)
            self._admission._condition.notify_all()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


class AdmissionQueue:
    """FIFO per endpoint/deployment, with cancellation callbacks that must not block.

    Keys are (credential-free endpoint, exact deployment). Timeout overrides can
    shorten the policy and cover this acquire call's own queue time. Acquisition
    performs no provider request or retry. Callers retain permits until release.
    """
    def __init__(self, *, active_limit=2, max_waiting=16, wait_seconds=30.0,
                 poll_seconds=.1, clock=time.monotonic):
        if type(active_limit) is not int or active_limit < 1:
            raise ValueError('active_limit must be a positive integer')
        if type(max_waiting) is not int or max_waiting < 0:
            raise ValueError('max_waiting must be a nonnegative integer')
        if type(wait_seconds) not in (int, float) or not math.isfinite(wait_seconds) or wait_seconds <= 0:
            raise ValueError('wait_seconds must be a positive finite number')
        if type(poll_seconds) not in (int, float) or not math.isfinite(poll_seconds) or not 0 < poll_seconds <= .1:
            raise ValueError('poll_seconds must be positive and at most 0.1')
        self.poll_seconds = poll_seconds
        self.active_limit = active_limit
        self.max_waiting = max_waiting
        self.wait_seconds = wait_seconds
        self._clock = clock
        self._condition = threading.Condition(threading.RLock())
        self._buckets = {}

    @staticmethod
    def _key(endpoint, deployment):
        if not isinstance(endpoint, str) or not isinstance(deployment, str) or not deployment:
            raise ValueError('A credential-free endpoint and exact deployment are required')
        parts = urlsplit(endpoint)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment):
            raise ValueError('A credential-free HTTP endpoint is required')
        return (parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == 'https' else 80),
                parts.path.rstrip('/'), deployment)

    def _prune(self, key, bucket):
        if bucket.active == 0 and not bucket.waiting:
            self._buckets.pop(key, None)

    def acquire(self, key, *, cancelled=None, timeout=None):
        started = self._clock()
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValueError('Admission key must be an endpoint/deployment tuple')
        key = self._key(*key)
        if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError('timeout must be a positive finite number')
        deadline = started + min(self.wait_seconds, timeout if timeout is not None else self.wait_seconds)
        cancelled = cancelled or (lambda: False)
        waiter = object()
        with self._condition:
            if cancelled():
                raise AdmissionCancelled('Azure admission cancelled')
            bucket = self._buckets.setdefault(key, _Bucket())
            try:
                if not bucket.waiting and bucket.active < self.active_limit:
                    if cancelled():
                        raise AdmissionCancelled('Azure admission cancelled')
                    if self._clock() >= deadline:
                        raise AdmissionTimeout('Azure admission queue timed out')
                    bucket.active += 1
                    return AdmissionPermit(self, key, bucket)
                if len(bucket.waiting) >= self.max_waiting:
                    raise AdmissionFull('Azure admission queue is full')
                bucket.waiting.append(waiter)
                while True:
                    if cancelled():
                        raise AdmissionCancelled('Azure admission cancelled')
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise AdmissionTimeout('Azure admission queue timed out')
                    if bucket.waiting[0] is waiter and bucket.active < self.active_limit:
                        if cancelled():
                            raise AdmissionCancelled('Azure admission cancelled')
                        if self._clock() >= deadline:
                            raise AdmissionTimeout('Azure admission queue timed out')
                        bucket.waiting.popleft()
                        bucket.active += 1
                        return AdmissionPermit(self, key, bucket)
                    self._condition.wait(timeout=min(remaining, self.poll_seconds))
            finally:
                if waiter in bucket.waiting:
                    bucket.waiting.remove(waiter)
                    self._condition.notify_all()
                self._prune(key, bucket)

    def snapshot(self):
        """Aggregate counters only; endpoints, deployments and credentials are absent."""
        with self._condition:
            return {'keys': len(self._buckets),
                    'active': sum(bucket.active for bucket in self._buckets.values()),
                    'waiting': sum(len(bucket.waiting) for bucket in self._buckets.values())}
