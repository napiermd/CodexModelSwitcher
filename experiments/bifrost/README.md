# Isolated Bifrost transport pilot

This experiment runs the pinned Bifrost v2.2.0 OSS container against a synthetic Azure Responses endpoint. It never connects to the installed Harbor gateway, opens a credential store, loads Codex configuration, or changes a task's route. No real Azure performance or availability conclusion follows from this experiment.

## Run

Docker must already be running. Pull the exact platform image from `pin.json` and its pinned Python mock image, then run:

```sh
python3 experiments/bifrost/pilot.py --output /tmp/harbor-bifrost-results.json
```

For ARM64:

```sh
docker pull maximhq/bifrost@sha256:d2c81d1dbfb11e0e0deea4a7bc6eea3b49503f05b76b4d18d7b34dcb9a24369b
docker pull python@sha256:b64631e04e4920160c50fbe8d8df828f7f35f06f425cb44aa09bca53e708a35a
```

`--cases basic stream image` selects a smaller diagnostic run. The complete suite is required for any full-suite claim. The AMD64 digest is recorded but was not exercised in the September 17 run.

The driver creates uniquely named containers and an internal Docker network. It publishes no host ports. A probe inside the mock container sends requests to Bifrost; Bifrost can only reach the synthetic upstream inside that network. Docker copies the configuration and test scripts into containers, so Colima or a remote daemon need not share the client filesystem. Both HOME locations and Bifrost's data volume are temporary. The `finally` block attempts to remove only containers and the network created by this invocation, including Bifrost's anonymous volume. Results are saved before cleanup; cleanup failures remain in the report for targeted recovery.

The image and synthetic request fixtures are the only inputs. The configuration uses local empty pricing/model/MCP catalogs, a synthetic Azure key, disabled request/response body logging (metadata access logs may remain), disabled content override headers, disabled semantic cache and telemetry plugins, one Azure key, and no fallbacks. Only Bifrost retries, at most once. The Python client never retries and schedules a 12-second total request deadline; the report separately checks observed elapsed time, which can exceed that target under scheduling or transport delays. The test additionally checks observed upstream attempt counts.

## What is checked

- JSON and streamed Responses, Azure path and deployment alias, and API-key forwarding.
- Data-URL image input and low image detail.
- Custom tool definitions, tool-call output streaming, and prior custom-tool history.
- Function call/output history, encrypted reasoning history, and `previous_response_id` propagation.
- Synthetic 429 with Retry-After, 500, 502, 503, and 529 responses followed by success.
- Failure before output, failure after an output delta, cancellation, and a stalled upstream.
- A synthetic content sentinel absent from Bifrost console logs and files copied from its temporary data volume.

The mock records synthetic requests in memory to test what reaches the upstream. The output report contains checks, counts, timings, and event names, without request or response bodies. Startup failures can include Bifrost's synthetic-only diagnostic logs. This is not a packet-level or whole-system audit for every possible content sink.

`response.completed` is not a Codex turn-completion signal. The experiment does not establish desktop lifecycle observation, safe runtime replacement, provider affinity after a turn, or lossless recovery from delivered partial output.

## Interpretation

The JSON report records failed checks and missing gates directly. `all_requested_case_checks_passed` describes only the requested rows. `all_synthetic_gates_passed` also requires the complete 17-case matrix, the expected version banner, and passing console/runtime-file content checks. A partial diagnostic run cannot satisfy that full gate. The report destination is checked before resources are created, and cleanup still runs if later report writes fail. A recovered mock status does not establish that Azure retries are appropriate for a particular production operation. Existing Harbor semantics reject `previous_response_id`; Bifrost forwarding it would demonstrate a different capability, not full compatibility with current switching behavior.

Read `docs/bifrost-evaluation.md` for the decision and limitations. Keep production routing unchanged until the required live Azure comparison and runtime/lifecycle work are complete.
