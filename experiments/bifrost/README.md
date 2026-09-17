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

The default `--surface converted` uses `/openai/v1/responses`, a provider-prefixed model alias, and one Bifrost retry. It retains the original comparison behavior.

The native comparison uses the Azure deployment name and disables all configured Bifrost retries:

```sh
python3 experiments/bifrost/pilot.py --surface azure-passthrough --output /tmp/harbor-bifrost-native-results.json
```

It sends `/azure_passthrough/openai/v1/responses`. The client also never retries. A passing error case on this surface means one attempt and the original error returned to the caller; it does not claim recovery or a tested Harbor retry loop. The 429 case must preserve `Retry-After` for the caller. Rejected encrypted history must cause one attempt without a hidden rewrite/retry.

`--cases basic stream image` selects a smaller diagnostic run. The complete current 24-case suite is required for any full-suite claim. Historical result files retain their original 17-case matrix and provenance. The original study exercised ARM64; the subsequent native-surface CI run exercised AMD64. Their separate result files identify the tested surface and platform.

The driver creates uniquely named containers and an internal Docker network. It publishes no host ports. A probe inside the mock container sends requests to Bifrost; Bifrost can only reach the synthetic upstream inside that network. Docker copies the configuration and test scripts into containers, so Colima or a remote daemon need not share the client filesystem. Both HOME locations and Bifrost's data volume are temporary. The `finally` block attempts to remove only containers and the network created by this invocation, including Bifrost's anonymous volume. Results are saved before cleanup; cleanup failures remain in the report for targeted recovery.

The image and synthetic request fixtures are the only inputs. The configuration uses local empty pricing/model/MCP catalogs, a synthetic Azure key, disabled request/response body logging (metadata access logs may remain), disabled content override headers, disabled semantic cache and telemetry plugins, two model-scoped synthetic Azure keys (one deliberately invalid), and no fallbacks. The selected surface defines the retry policy described above. The Python client never retries and schedules a 12-second total request deadline; the report separately checks observed elapsed time, which can exceed that target under scheduling or transport delays. The test additionally checks observed upstream attempt counts.

## Provider pressure study

Run the separate two-case admission experiment against the same pinned native transport:

```sh
python3 -B experiments/bifrost/pressure_pilot.py --surface azure-passthrough --output /tmp/harbor-bifrost-pressure.json
```

This sets provider concurrency to one, buffer size to one, and `drop_excess_requests=true`. The first case holds a request before headers while two followers compete for the single buffer slot. It requires one queued completion, one queue-full 503 with no upstream attempt, and exact response correlation. The second case holds a stream open after a delivered text delta and observes whether another stream delivers before the first is released.

A passing pressure report means the observation is complete. `pacing_verified` remains false. If both upstream and client observe overlapping streams, `active_stream_bound_disproved` is true: the worker setting did not limit established streams to one. An observation of no overlap within two seconds alone does not prove a lifetime bound. This experiment does not claim fairness, admission deadlines for real workloads, or a production scheduler. The CI matrix retains `pressure.json` beside the three native contract samples and hashes it in `provenance.json`.

The actual hosted study passed evidence gates on AMD64 and ARM64 in run `35279368950`: upstream counts were 1/1/0 for held/queued/rejected requests, and two streams overlapped with concurrency=1. Both reports set `active_stream_bound_disproved=true`. See `results.pressure-*-ci.json` and adjacent provenance.

## What is checked

- JSON and streamed Responses, Azure path and deployment alias, and API-key forwarding.
- Data-URL image input and low image detail.
- Custom tool definitions, tool-call output streaming, and prior custom-tool history.
- Function call/output history, encrypted reasoning history, and `previous_response_id` propagation.
- Synthetic 429 with Retry-After, 500, 502, 503, and 529 responses followed by success.
- Failure before output, failure after an output delta, cancellation, and a stalled upstream.
- Opaque author, recipient, nested unknown fields, and encrypted reasoning in both requests and JSON/streamed response items.
- An actual incorrect synthetic API key reaching the mock and receiving 401 without a retry.
- Three workers crossing a bounded upstream barrier and receiving their own response IDs and text. This tests simultaneous requests and correlation, not queue bounds or fairness.
- Literal input/output/cached/reasoning token-count preservation. Monetary cost remains unverified because pricing catalogs are empty.
- Complete SSE frames and ordered events through EOF, including rejection of duplicate/post-terminal events and corrupted intermediate opaque events.
- Cancellation after a complete nonempty delta, a request-specific upstream disconnect within two seconds, and no replay observed throughout that two-second window. This does not prove that real Azure computation or billing stops.
- A synthetic content sentinel absent from Bifrost console logs and files copied from its temporary data volume.

The mock records synthetic requests in memory to test what reaches the upstream. The output report contains checks, counts, timings, and event names, without request or response bodies. Startup failures can include Bifrost's synthetic-only diagnostic logs. This is not a packet-level or whole-system audit for every possible content sink.

`response.completed` is not a Codex turn-completion signal. The experiment does not establish desktop lifecycle observation, safe runtime replacement, provider affinity after a turn, or lossless recovery from delivered partial output.

## Interpretation

The JSON report records failed checks and missing gates directly. `all_requested_case_checks_passed` describes only the requested rows. `all_synthetic_gates_passed` also requires the complete 24-case matrix for the selected surface, the expected version banner, and passing console/runtime-file content checks. A partial diagnostic run cannot satisfy that full gate. The report destination is checked before resources are created, and cleanup still runs if later report writes fail. A recovered mock status does not establish that Azure retries are appropriate for a particular production operation. Existing Harbor semantics reject `previous_response_id`; Bifrost forwarding it would demonstrate a different capability, not full compatibility with current switching behavior.

Twenty-seven hermetic tests cover the driver and protocol assertions. The first September 17 native-surface attempt on local ARM64 failed during `docker start`, before any test request; `results.passthrough-startup-failed.json` records that failure and the verified cleanup follow-up.

The subsequent isolated GitHub Actions run `35275679952` passed all 24 native-surface cases on the pinned AMD64 image, including content/version and cleanup gates. `results.passthrough-ci.json` is the unchanged report artifact, and its adjacent provenance JSON records its hash and run identity. The workflow is `.github/workflows/bifrost-pilot.yml`; it runs when the pilot changes in a pull request and is also manually dispatchable after the workflow is on the default branch. These are synthetic transport measurements, not real Azure model latency or billed-cost measurements. A later hosted Linux ARM64 run passed three complete matrices; the local Colima startup failure remains unresolved.

The current CI workflow assigns separate native AMD64 and ARM64 runners. Each runs three complete matrices sequentially with a fresh candidate per sample. Artifacts are named `bifrost-native-contract-amd64` and `bifrost-native-contract-arm64`; each includes the unchanged sample reports and a generated provenance file with the actual checkout SHA and report hashes. The gate requires the assigned architecture, matching immutable image, all three complete passes, and verified cleanup. Run `35277669718` passed all three samples on each architecture, or 144 case executions. `results.passthrough-matrix-ci.json` retains run/checkout provenance, six report hashes, and selected original timing observations; `results.passthrough-arm64-ci.json` is the unchanged first ARM64 report. Repeated synthetic measurements do not establish real Azure latency distributions.

Read `docs/bifrost-evaluation.md` for the decision and limitations. Keep production routing unchanged until the required live Azure comparison and runtime/lifecycle work are complete.
