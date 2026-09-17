# Bifrost evaluation — SAY-3344

## Decision

**No-go for production replacement.** The pinned Bifrost v2.2.0 ARM64 container ran against an isolated synthetic Azure Responses server on September 17, 2026. The initial 17-case request matrix reported 14 passes and three failed checks. One reported pass, cancellation, remains provisional until its stricter attribution check is rerun. A focused rerun confirmed that encrypted reasoning content was not preserved and that a 429 retry happened before Retry-After elapsed. No live Azure credential, request, or performance comparison was used.

Bifrost remains a candidate for an Azure transport behind Harbor. Harbor would still own desktop integration, per-task model identities, subscription adapters, and safe admission/retirement. This pilot does not resolve the separate blocker: no verified lifecycle source for existing desktop tasks. Keep the active gateway and existing runtime ownership intact; use coordinated maintenance until authoritative turn events and reconciliation are available.

## Reproduce the candidate

`experiments/bifrost/pin.json` records image tag v2.2.0, immutable ARM64/AMD64 digests, source tag `transports/v2.2.0`, and source commit `ed79592fc4771f12f2717dd7c9ab668e663a08f3`. The ARM64 digest was pulled and executed; its startup banner identified v2.2.0. The Python helper image is also pinned. AMD64 was not tested. See `experiments/bifrost/README.md` for commands.

This is the OSS image without an enterprise license. The source license is Apache 2.0. The release README describes clustering and other advanced deployment features as enterprise capabilities. This experiment does not depend on, test, or establish licensing for those features.

## Observed request matrix

The full-run evidence is `experiments/bifrost/results.synthetic.json`. These selected fields were recovered from the completed suite's stdout after Docker cleanup timed out before the earlier driver saved its JSON. The provenance is recorded in the file. The completed focused diagnostic run is recorded separately in `experiments/bifrost/results.focused.json`; it does not replace the full run or erase its failures.

| Case | Result | Upstream attempts | Observed behavior |
|---|---|---:|---|
| JSON Responses | Pass | 1 | Correct Azure path, deployment alias, synthetic API key, completed response |
| Streamed Responses | Pass | 1 | Output delta and completed event reached the client |
| Image | Pass | 1 | Data URL and low-detail input preserved |
| Custom tool | Pass | 1 | Definition and streamed custom-tool output preserved |
| Custom-tool history | Pass | 1 | Prior call/output preserved |
| Function history | Pass | 1 | Prior call/output preserved |
| Encrypted reasoning history | Failed check | 1 | Exact input JSON differed; `include` survived. This alone does not establish loss of encrypted content |
| Previous response ID | Pass | 1 | `previous_response_id` forwarded |
| 429 / Retry-After | Failed check | 2 | At least 0.95 seconds between attempts, but HTTP 504 after 7,482 ms instead of recovery |
| 500, 502, 503, 529 | Pass | 2 each | One retry followed by HTTP 200 |
| Failure before output | Pass | 1 | Failure surfaced as HTTP 400 |
| Failure after output | Pass | 1 | Failure remained visible; no completed response or retry |
| Cancellation | Provisional pass | 1 | No replay; shared mock disconnect flag was set within the polling window; stricter case attribution remains to be rerun |
| Stalled upstream | Failed check | 1 | HTTP 504 after 14,301 ms, exceeding the 13-second acceptance threshold |

### Focused rerun

- **Reasoning incompatibility reproduced:** `/input/1/encrypted_content` differed between the request and the mock upstream capture. The synthetic encrypted value was absent from upstream reasoning items; the other input fields and `include` matched. This proves failure to preserve the tested field, without asserting whether a real Azure encrypted blob would be accepted.
- **Retry-After gate failed:** the mock returned HTTP 429 with `Retry-After: 1`; Bifrost made its one retry after **152.98 ms**, then received HTTP 200. This disproves honoring that header under the tested configuration. Keep retries disabled in a future Bifrost transport unless Harbor alone owns and verifies retry policy, or fix and validate Bifrost's behavior first.
- **Stall passed on rerun:** HTTP 504 arrived after **11,707.97 ms**. The first run's 14,301 ms remains evidence that this environment did not establish a consistent bound.
- **State evidence:** both focused-run containers were running with `OOMKilled=false`, `Dead=false`, and `ExitCode=0` when inspected. The candidate's Docker health metadata also recorded slow startup health checks. This does not retrospectively establish the earlier removed containers' OOM state.
- **Content checks passed for the focused run:** the synthetic prompt sentinel was absent from console logs and copied runtime files. Only configuration SQLite files, the fixture configuration, and local catalog files were found. This is a limited sentinel audit of these requests and sinks.

These are synthetic contract assertions, not full protocol conformance. They do not test every event order, custom-tool grammar, image transport, error body/header, malformed stream, connection-affinity rule, or provider-side side effect. The original cancellation assertion used a shared mock disconnect flag. The final harness restricts that flag to cancellation requests, but the stricter assertion has not been rerun through Bifrost; treat the original pass as provisional. Neither assertion establishes an Azure-side billing or cancellation guarantee. Forwarding a previous-response ID also differs from current Harbor semantics, which reject that field.

The 429 and stall results occurred during a run with slow Docker operations. The evidence does not isolate a Bifrost defect from host scheduling, timeout configuration, or test effects. The initial timing failures remain unresolved; the focused rerun supplies stronger field-parity and Retry-After evidence. Timings from one mock run must not be used as an Azure latency benchmark.

## Isolation, retries, and content

The harness creates uniquely named containers and an internal Docker network with no published host ports. It copies only fixture scripts and configuration into temporary containers. It does not mount real HOME, credential stores, Codex state, conversation files, or application configuration. Initialization uses local empty catalogs; the candidate has no external network route.

Bifrost's HTTP handlers required a configuration store. The experiment therefore uses temporary SQLite configuration storage, with request/response content logging, per-request content-storage overrides, semantic caching, and logging/telemetry plugins disabled by configuration. Metadata access logs may still be produced. No fallback key/model is configured. The settings do not constitute a completed audit of every possible content sink: the full run's final console/runtime-file sentinel audit was not durably captured. The focused rerun durably captured passing sentinel checks.

Bifrost owns the only retry in this experiment: `max_retries=1`; the Python client never retries. All 17 cases stayed at two or fewer upstream attempts. The client schedules a 12-second total timeout and tests a 13-second observed limit; the stalled case exceeded that limit. A production gate requires a demonstrated end-to-end bound, including time spent queued and backing off, plus no replay after delivered output.

The driver now saves results before diagnostics and cleanup, records available container state, and records cleanup errors without losing the request evidence. Review found and fixed two driver bugs: report-write failures could skip cleanup, and partial runs could claim aggregate success. Ten hermetic regressions cover output preflight, unconditional owned-resource cleanup, full-matrix identity, version, and content gates. These changes do not rewrite the historical result files or constitute a new container run. Cleanup addresses only resources created by the invocation. The focused run recorded a 15-second mock-removal command timeout; follow-up inspection verified both of its containers absent. Its network-removal command succeeded. The earlier full-run network was also removed after confirming it had zero containers.

## Infrastructure attempts and limits

- The exact native download path returned HTTP 403, so the actual transport was obtained and run through the pinned official container. An npm wrapper version was not treated as a transport pin.
- Initial Colima attempts could not read Mac temporary bind mounts; an internal network also did not expose the requested host ports. The final harness uses `docker cp` and container-internal probes.
- Disabling the configuration store prevented HTTP-handler startup. Temporary SQLite storage allowed v2.2.0 to become healthy.
- An earlier five-case run ended with Docker exec exit 137 after the experiment controller intentionally stopped **that run's own mock container** to replace slow per-case Docker exec calls with one batch. This was an experiment-control interruption, not evidence of a Bifrost/provider crash or OOM. Its report is `results.interrupted.json`.
- The complete batch returned all 17 rows, then `docker rm -f -v harbor-bifrost-pilot-7a430183a4-azure` exceeded its 60-second command timeout. A later prefix-filtered container listing was empty, and exact mock-container inspection returned `no such object`. OOMKilled/exit state could not be recovered after removal. No pre-existing containers or live applications were stopped or changed.

## Native Azure passthrough investigation

A source audit at the same exact commit identified a narrower explanation for the converted-route encryption failure. `core/providers/openai/responses.go:345–347` resolves model capabilities, and `:444–491` clears encrypted reasoning content for models classified as non-reasoning (except compaction). Azure's normalized handler calls that shared conversion through `core/providers/azure/azure.go:563`. The synthetic `pilot-model` deployment has no canonical reasoning-model metadata. The historical missing-schema report in upstream issue #4608 is not the cause established for this pin: its schema already contains author, recipient, and encrypted content.

The pin also supports `POST /azure_passthrough/openai/v1/responses`. That route assigns the original request bytes to `BifrostPassthroughRequest.Body` (`transports/bifrost-http/integrations/router.go:3381–3402`) and Azure sends them unchanged (`core/providers/azure/azure.go:3510`). Its body uses the native deployment name, without an `azure/` prefix. Server configuration supplies the Azure key. This is a different transport surface; enabling `send_back_raw_request` would only echo debug data and would not bypass conversion.

The passthrough comparison sets `providers.azure.network_config.max_retries` to zero and configures no fallback. This is necessary but insufficient for the normalized route: `core/bifrost.go:6250–6262` and `:6718–6723` can grant an additional attempt after stripping rejected encrypted history, outside the normal retry budget. The strip helper does not accept the passthrough request shape (`core/encryptedreasoning.go:181–194`, `:213–220`, `:526–529`). The experiment therefore also tests an `invalid_encrypted_content` rejection and observes the actual upstream attempt count. It does not infer one-attempt behavior from configuration alone.

The expanded matrix adds opaque input/output fields (including streamed terminal output), rejected synthetic credentials, rejected encrypted history, disconnect after delivered output, and three simultaneous requests. Stream checks decode complete SSE frames, validate response status and usage, and preserve event order. Cancellation requires a nonempty delta followed by a request-specific upstream disconnect within two seconds, with the complete two-second window observed for replay. The wrong-key case uses a separate model-scoped synthetic key and the mock validates the actual `api-key` header. Three workers must reach a bounded upstream barrier together and each receive its own correlation value. These checks do not establish queue capacity, Azure billing, or real Azure processing cancellation.

Twenty-seven hermetic harness tests pass, including the ten existing driver/cleanup regressions. Review also closed three evidence gaps: the client now reads past a terminal frame through EOF, intermediate opaque streaming items/deltas are checked, and worker overlap is attributed to the worker barrier rather than unrelated active requests. The historical 17-case evidence remains unchanged; the expanded 24-case matrix and native-surface attempt are recorded separately.

### Native-surface execution remains unverified

`experiments/bifrost/results.passthrough-startup-failed.json` records the September 17 attempt. Docker's start command exceeded 60 seconds even though later inspection showed the candidate running and still starting its health check. No test request was sent and zero matrix rows were produced. Subsequent host Docker inspections and cleanup commands timed out. A Docker query through the running Colima VM later confirmed both invocation-owned containers absent. The report retains original cleanup errors and records the follow-up separately.

The VM diagnostic at 14:06 PDT reported 7,922 MiB total memory, 308 MiB available, and no swap. This is a host constraint observed after the failure, not proof of its cause. No other containers, VM settings, installed apps, or live routes were changed. The native-surface matrix must still run successfully in a responsive isolated environment before it can change the production no-go decision.

## Remaining gates

1. Fix or safely bypass the encrypted-reasoning and Retry-After failures, explain the inconsistent deadline result, and rerun the complete matrix with durable diagnostics and the full content-sentinel audit.
2. Extend conformance coverage for the actual Codex request/event forms, invalid authentication, three concurrent workers, bounded queues, and usage/cost provenance. These original SAY-3344 requirements remain untested. Verify immutable pin provenance in the deployment process.
3. After conformance passes, compare an isolated Azure deployment against the existing Harbor route using synthetic content and secure credential handoff. Measure matched repeated first-output/total-latency distributions, errors, cancellation, and retry timing; record sample sizes. No such benchmark is claimed here.
4. Establish response-ID/connection affinity and one retry owner across the client, Harbor, Bifrost, and upstream. Never automatically retry after partial tool/text output.
5. Complete independent-runtime, authoritative desktop lifecycle, admission, and retirement work before replacing a live gateway.

## Follow-up issues

- [SAY-3345](https://linear.app/sayvant/issue/SAY-3345): preserve encrypted reasoning history.
- [SAY-3346](https://linear.app/sayvant/issue/SAY-3346): honor Retry-After and prove bounded retry/cancellation behavior.

## Primary sources

- `https://github.com/maximhq/bifrost/tree/transports/v2.2.0`
- `https://raw.githubusercontent.com/maximhq/bifrost/transports/v2.2.0/LICENSE`
- `https://raw.githubusercontent.com/maximhq/bifrost/transports/v2.2.0/README.md`
- `https://raw.githubusercontent.com/maximhq/bifrost/transports/v2.2.0/transports/config.schema.json`
- `https://docs.getbifrost.ai/providers/supported-providers/azure`
- `https://docs.getbifrost.ai/features/retries-and-fallbacks`

The immutable image, source commit, and observed results identify what was exercised. Live documentation is supporting context.
