#!/usr/bin/env python3
"""Opt-in pinned-Bifrost queue and stream-lifetime measurements with synthetic traffic."""
import concurrent.futures
import copy
import json
import sys
import threading
import time
import urllib.request

import pilot

NAME = 'provider-pressure'
CASES = ('queue_capacity', 'stream_lifetime')
SCRIPT = 'pressure_pilot.py'
SCOPE = 'synthetic provider admission and stream-lifetime observations only'
STEP_TIMEOUT = 3
OVERLAP_WINDOW = 2
HOLD_TIMEOUT = 9
CONTENT_SENTINEL = 'SYNTHETIC-PILOT-CONTENT-DO-NOT-LOG'


def configure(config):
    config = copy.deepcopy(config)
    config['client']['drop_excess_requests'] = True
    provider = config['providers']['azure']
    provider['concurrency_and_buffer_size'] = {'concurrency': 1, 'buffer_size': 1}
    provider['network_config'].update(max_retries=0, default_request_timeout_in_seconds=10,
                                      stream_idle_timeout_in_seconds=10)
    return config


def correlation_response(request_id):
    response = pilot.completed()
    response['id'] = 'resp_' + request_id
    response['output'][0]['content'][0]['text'] = CONTENT_SENTINEL + ':' + request_id
    return response


class PressureAzure(pilot.MockAzure):
    def __init__(self, address=('127.0.0.1', 0)):
        super().__init__(address)
        self.RequestHandlerClass = PressureHandler
        self.condition = threading.Condition(self.lock)
        self.released = {}

    def release(self, request_id):
        with self.condition:
            self.released.setdefault(request_id, time.monotonic())
            self.condition.notify_all()

    def await_release(self, request_id):
        with self.condition:
            return self.condition.wait_for(lambda: request_id in self.released, HOLD_TIMEOUT)

    def snapshot(self):
        with self.lock:
            return {'requests': copy.deepcopy(self.requests), 'released': dict(self.released)}


class PressureHandler(pilot.MockHandler):
    def do_GET(self):
        if self.path.startswith('/release/'):
            request_id = self.path.removeprefix('/release/')
            self.server.release(request_id)
            self.send_json(200, {'released': request_id})
        else:
            super().do_GET()

    def respond(self, body, case, attempt, record):
        if not record['synthetic_key_correct']:
            return super().respond(body, case, attempt, record)
        request_id = record['request_id']
        if case == 'queue_first':
            if not self.server.await_release(request_id):
                self.send_json(504, {'error': {'code': 'synthetic_hold_timeout'}})
                return
        response = correlation_response(request_id)
        if not body.get('stream'):
            self.send_json(200, response)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.event('response.created', response={**response, 'status': 'in_progress', 'output': []})
        self.event('response.output_text.delta', delta=response['output'][0]['content'][0]['text'], item_id='msg_pilot',
                   output_index=0, content_index=0)
        with self.server.lock:
            record['stream_started_at'] = time.monotonic()
        if not self.server.await_release(request_id):
            self.event('response.failed', response={'status': 'failed', 'error': {'code': 'synthetic_hold_timeout'}})
            return
        self.event('response.completed', response=response)


class MockControl:
    def __init__(self, base='http://127.0.0.1:8081'):
        self.base = base

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=STEP_TIMEOUT) as response:
            return json.load(response)

    def snapshot(self):
        return self.get('/snapshot')

    def release(self, request_id):
        return self.get('/release/' + request_id)

    def wait_for_request(self, request_id):
        deadline = time.monotonic() + STEP_TIMEOUT
        while time.monotonic() < deadline:
            snapshot = self.snapshot()
            if any(r['request_id'] == request_id for r in snapshot['requests']):
                return snapshot
            threading.Event().wait(.02)
        raise TimeoutError('Synthetic upstream did not receive the expected request')


class RequestRun:
    def __init__(self, pool, body, host, port=8080, barrier=None):
        self.body = body
        self.request_id = body['metadata']['harbor_pilot_request']
        self.sent = threading.Event()
        self.output = threading.Event()
        self.output_at = None
        def observe(event):
            if event.get('type') == 'response.output_text.delta' and event.get('delta'):
                if self.output_at is None:
                    self.output_at = time.monotonic()
                self.output.set()
        def perform():
            if barrier is not None:
                barrier.wait()
            return pilot.perform(port, body, host=host, surface='azure-passthrough',
                                 on_sent=self.sent.set, on_event=observe)
        self.future = pool.submit(perform)

    def wait_sent(self):
        if not self.sent.wait(STEP_TIMEOUT):
            raise TimeoutError('Synthetic client did not send its request')

    def result(self):
        return self.future.result(timeout=pilot.DEADLINE + 1)


def response_matches(result, request_id, stream=False):
    try:
        if stream:
            events = result['events']
            terminal = [event for event in events if event.get('type') in
                        ('response.completed', 'response.failed', 'response.incomplete', 'error')]
            if len(terminal) != 1 or events[-1].get('type') != 'response.completed':
                return False
            response = terminal[0]['response']
            if ''.join(event.get('delta', '') for event in events
                       if event.get('type') == 'response.output_text.delta') != CONTENT_SENTINEL + ':' + request_id:
                return False
        else:
            response = json.loads(result['data'])
        return (result['status'] == 200 and response['status'] == 'completed'
                and response['id'] == 'resp_' + request_id and not response.get('error')
                and response['output'][0]['content'][0]['text'] == CONTENT_SENTINEL + ':' + request_id
                and response['usage'] == pilot.completed()['usage'])
    except (KeyError, TypeError, ValueError, IndexError):
        return False


def request_checks(runs, records):
    expected = {run.request_id: run.body for run in runs}
    return {
        'no_unexpected_upstream_requests': all(record['request_id'] in expected for record in records),
        'upstream_uses_synthetic_key': all(record['synthetic_key_correct'] for record in records),
        'native_path_and_payload_preserved': all(record['path'] == '/openai/v1/responses'
            and record['body'] == expected.get(record['request_id']) for record in records),
    }


def evaluate_queue(runs, results, before_release, final, baseline_count=0):
    ids = [run.request_id for run in runs]
    records = final['requests'][baseline_count:]
    initial = before_release['requests'][baseline_count:]
    counts = {request_id: sum(r['request_id'] == request_id for r in records) for request_id in ids}
    try:
        error = json.loads(results[2]['data'])['error']
        queue_error = error.get('type') == 'request_dropped' and error.get('message') == 'request dropped: queue is full'
    except (KeyError, TypeError, ValueError):
        queue_error = False
    checks = {
        'only_held_request_reached_upstream_before_release': [r['request_id'] for r in initial] == ids[:1],
        'overflow_is_queue_full_503': results[2].get('status') == 503 and queue_error,
        'overflow_never_reached_upstream': counts[ids[2]] == 0,
        'accepted_requests_once_each': [counts[ids[0]], counts[ids[1]]] == [1, 1],
        'accepted_responses_match_callers': all(response_matches(results[i], ids[i]) for i in (0, 1)),
        'clients_finished_within_deadline': all(r['elapsed_ms'] < (pilot.DEADLINE + 1) * 1000 for r in results),
        **request_checks(runs, records),
    }
    released_at = final['released'].get(ids[0])
    second = next((r for r in records if r['request_id'] == ids[1]), None)
    checks['queued_request_started_after_release'] = (released_at is not None and second is not None
                                                      and second['at'] >= released_at)
    return {'case': 'queue_capacity', 'passed': all(checks.values()), 'checks': checks,
            'held_request_id': ids[0], 'queued_request_id': ids[1], 'rejected_request_id': ids[2],
            'upstream_attempts': counts, 'total_upstream_attempts': len(records),
            'http_statuses': [r.get('status') for r in results],
            'scope': 'one held, one queued, one rejected; follower identities observed, FIFO not asserted'}


def evaluate_streams(runs, results, snapshot, overlap_observed, baseline_count=0):
    ids = [run.request_id for run in runs]
    records = snapshot['requests'][baseline_count:]
    counts = {request_id: sum(r['request_id'] == request_id for r in records) for request_id in ids}
    first_release = snapshot['released'].get(ids[0])
    second = next((r for r in records if r['request_id'] == ids[1]), {})
    upstream_overlap = (first_release is not None and second.get('stream_started_at') is not None
                        and second['stream_started_at'] < first_release)
    client_overlap = (first_release is not None and runs[1].output_at is not None
                      and runs[1].output_at < first_release)
    checks = {
        'one_attempt_per_request': list(counts.values()) == [1, 1],
        'complete_correlated_streams': all(response_matches(result, request_id, True)
                                          for result, request_id in zip(results, ids)),
        'first_stream_was_established': runs[0].output_at is not None,
        'overlap_observation_matches_both_ends': overlap_observed == upstream_overlap == client_overlap,
        'clients_finished_within_deadline': all(r['elapsed_ms'] < (pilot.DEADLINE + 1) * 1000 for r in results),
        **request_checks(runs, records),
    }
    valid = all(checks.values())
    return {'case': 'stream_lifetime', 'passed': valid, 'checks': checks,
            'upstream_attempts': counts, 'total_upstream_attempts': len(records),
            'overlap_observed': overlap_observed,
            'observation': ('overlap_observed' if overlap_observed else 'no_overlap_during_window') if valid else 'inconclusive',
            'observation_window_seconds': OVERLAP_WINDOW,
            'pacing_verified': False,
            'active_stream_bound_disproved': valid and overlap_observed,
            'scope': 'worker concurrency versus two open streams; a passing row means valid evidence, not a pacing guarantee'}


def release_all(control, runs):
    errors = []
    for run in runs:
        try:
            control.release(run.request_id)
        except Exception as error:
            errors.append(type(error).__name__)
    if errors:
        raise RuntimeError('Synthetic release failed: ' + ','.join(errors))


def queue_phase(host, control, port=8080):
    baseline_count = len(control.snapshot()['requests'])
    runs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        try:
            first = RequestRun(pool, pilot.request('queue_first', surface='azure-passthrough'), host, port)
            runs.append(first)
            control.wait_for_request(first.request_id)
            barrier = threading.Barrier(2, timeout=STEP_TIMEOUT)
            for name in ('queue_follower_a', 'queue_follower_b'):
                runs.append(RequestRun(pool, pilot.request(name, surface='azure-passthrough'),
                                       host, port, barrier))
            done, pending = concurrent.futures.wait([run.future for run in runs[1:]],
                timeout=STEP_TIMEOUT, return_when=concurrent.futures.FIRST_COMPLETED)
            if len(done) != 1 or len(pending) != 1:
                raise TimeoutError('Expected one completed follower while the first request was held')
            rejected = next(run for run in runs[1:] if run.future in done)
            accepted = next(run for run in runs[1:] if run.future in pending)
            rejected_result = rejected.result()
            before_release = control.snapshot()
        finally:
            release_all(control, runs)
        ordered = [first, accepted, rejected]
        results = [first.result(), accepted.result(), rejected_result]
    return evaluate_queue(ordered, results, before_release, control.snapshot(), baseline_count)


def stream_phase(host, control, port=8080):
    baseline_count = len(control.snapshot()['requests'])
    runs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        try:
            first = RequestRun(pool, pilot.request('stream_first', True, 'azure-passthrough'), host, port)
            runs.append(first)
            if not first.output.wait(STEP_TIMEOUT):
                raise TimeoutError('First synthetic stream never delivered a complete delta')
            second = RequestRun(pool, pilot.request('stream_second', True, 'azure-passthrough'), host, port)
            runs.append(second)
            second.wait_sent()
            overlap = second.output.wait(OVERLAP_WINDOW)
            control.release(first.request_id)
            if not second.output.wait(STEP_TIMEOUT):
                raise TimeoutError('Second synthetic stream did not deliver after first release')
        finally:
            release_all(control, runs)
        results = [run.result() for run in runs]
    return evaluate_streams(runs, results, control.snapshot(), overlap, baseline_count)


def suite(host, cases, control=None, port=8080):
    control = control or MockControl()
    rows = []
    for case in cases:
        try:
            row = {'queue_capacity': queue_phase, 'stream_lifetime': stream_phase}[case](host, control, port)
        except Exception as error:
            row = {'case': case, 'passed': False, 'checks': {'experiment_completed': False},
                   'harness_error_type': type(error).__name__}
        rows.append(row)
    return {'cases': rows, 'study': NAME, 'pacing_verified': False,
            'provider_limits': {'concurrency': 1, 'buffer_size': 1, 'drop_excess_requests': True}}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == '--mock':
        with PressureAzure(('0.0.0.0', 8081)) as server:
            server.serve_forever()
    elif mode == '--health':
        try:
            with urllib.request.urlopen('http://' + sys.argv[2] + ':8080/health', timeout=2) as response:
                result = {'ready': response.status == 200}
        except OSError:
            result = {'ready': False}
        print(json.dumps(result))
    elif mode == '--suite':
        if sys.argv[3] != 'azure-passthrough':
            raise SystemExit('Pressure study requires native Azure passthrough')
        print(json.dumps(suite(sys.argv[2], json.loads(sys.argv[4]))))
    else:
        pilot.main(study=sys.modules[__name__])


if __name__ == '__main__':
    main()
