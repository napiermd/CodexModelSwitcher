"""Run HTTP probes inside the isolated container network, with no host ports."""
import json
import sys
import time
import urllib.request
from pilot import perform, request, evaluate

mode = sys.argv[1]
if mode == '--snapshot':
    with urllib.request.urlopen('http://127.0.0.1:8081/snapshot', timeout=3) as response:
        result = json.load(response)
elif mode == '--health':
    try:
        with urllib.request.urlopen('http://' + sys.argv[2] + ':8080/health', timeout=2) as response:
            result = {'ready': response.status == 200}
    except OSError:
        result = {'ready': False}
elif mode == '--suite':
    def snapshot():
        with urllib.request.urlopen('http://127.0.0.1:8081/snapshot', timeout=3) as response:
            return json.load(response)
    result = {'cases': []}
    for case in json.loads(sys.argv[3]):
        body = request(case, case in ('stream', 'custom_tool', 'partial_failure', 'pre_output_failure', 'cancel'))
        actual = perform(8080, body, case == 'cancel', host=sys.argv[2])
        if case == 'cancel':
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not snapshot()['cancelled']:
                time.sleep(.1)
            result['cancellation_observed_at_upstream'] = snapshot()['cancelled']
        row = evaluate(case, body, actual, snapshot()['requests'])
        if case == 'cancel':
            row['checks']['upstream_cancel_within_two_seconds'] = result['cancellation_observed_at_upstream']
            row['passed'] = all(row['checks'].values())
        result['cases'].append(row)
elif mode == '--request':
    result = perform(8080, json.loads(sys.argv[3]), sys.argv[4] == 'True', host=sys.argv[2])
else:
    raise SystemExit('Unknown probe mode')
print(json.dumps(result))
