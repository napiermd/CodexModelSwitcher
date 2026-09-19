"""Run HTTP probes inside the isolated container network, with no host ports."""
import concurrent.futures
import json
import sys
import time
import urllib.request
from pilot import perform, request, evaluate, DEADLINE


def snapshot():
    with urllib.request.urlopen('http://127.0.0.1:8081/snapshot', timeout=3) as response:
        return json.load(response)


def concurrent_workers(host, surface):
    bodies = [request('worker_' + str(i), surface=surface) for i in range(3)]
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        pending = [pool.submit(perform, 8080, body, host=host, surface=surface) for body in bodies]
        responses = [future.result(timeout=DEADLINE + 2) for future in pending]
    elapsed_ms = round((time.monotonic() - start) * 1000, 2)
    seen = snapshot()['requests']
    records = [r for r in seen if r['case'].startswith('worker_')]
    checks = {'three_upstream_requests': len(records) == 3,
              'three_simultaneous_upstream_requests': len(records) == 3 and all(r.get('worker_barrier_passed') for r in records),
              'bounded_deadline': elapsed_ms < (DEADLINE + 1) * 1000}
    for i, (body, actual) in enumerate(zip(bodies, responses)):
        request_id = body['metadata']['harbor_pilot_request']
        try:
            response = json.loads(actual['data'])
            text = response['output'][0]['content'][0]['text']
        except (ValueError, KeyError, IndexError, TypeError):
            response, text = {}, None
        checks['worker_' + str(i) + '_own_response'] = (
            actual['status'] == 200 and response.get('status') == 'completed'
            and response.get('id') == 'resp_' + request_id and text == request_id)
        checks['worker_' + str(i) + '_one_attempt'] = sum(r['request_id'] == request_id for r in records) == 1
    return {'case': 'concurrent_workers', 'passed': all(checks.values()), 'checks': checks,
            'upstream_attempts': len(records), 'elapsed_ms': elapsed_ms,
            'http_statuses': [r['status'] for r in responses],
            'scope': 'three concurrent requests; queue bounds, fairness and billing unverified'}


mode = sys.argv[1]
if mode == '--snapshot':
    result = snapshot()
elif mode == '--health':
    try:
        with urllib.request.urlopen('http://' + sys.argv[2] + ':8080/health', timeout=2) as response:
            result = {'ready': response.status == 200}
    except OSError:
        result = {'ready': False}
elif mode == '--suite':
    result = {'cases': []}
    surface = sys.argv[3]
    for case in json.loads(sys.argv[4]):
        if case == 'concurrent_workers':
            result['cases'].append(concurrent_workers(sys.argv[2], surface))
            continue
        body = request(case, case in ('stream', 'custom_tool', 'partial_failure', 'pre_output_failure',
                                     'cancel', 'opaque_stream', 'partial_disconnect'), surface)
        actual = perform(8080, body, case == 'cancel', host=sys.argv[2], surface=surface)
        disconnect = None
        if case == 'cancel':
            # Observe the entire window, including after the first disconnect, to detect replay.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                disconnect = snapshot()['cancelled'].get(body['metadata']['harbor_pilot_request'])
                time.sleep(.1)
        row = evaluate(case, body, actual, snapshot()['requests'], surface)
        if case == 'cancel':
            cancelled_at = actual.get('cancelled_at')
            row['checks']['upstream_cancel_within_two_seconds'] = (
                cancelled_at is not None and disconnect is not None and 0 <= disconnect - cancelled_at <= 2)
            row['no_replay_observation_seconds'] = 2
            row['upstream_disconnect_after_cancel_ms'] = (round((disconnect - cancelled_at) * 1000, 2)
                if cancelled_at is not None and disconnect is not None else None)
            row['passed'] = all(row['checks'].values())
        result['cases'].append(row)
elif mode == '--request':
    result = perform(8080, json.loads(sys.argv[3]), sys.argv[4] == 'True', host=sys.argv[2])
else:
    raise SystemExit('Unknown probe mode')
print(json.dumps(result))
