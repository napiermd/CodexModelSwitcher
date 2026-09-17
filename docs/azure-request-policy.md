# Azure request deadlines and retry ownership

Status: implemented and tested in the isolated candidate on September 17, 2026. Installation and active desktop configuration are separate, unverified steps.

## Request behavior

Each accepted Azure inference request has one 180-second monotonic deadline. It begins after input parsing and route preparation, before admission. Queueing, DNS resolution, TCP connect, TLS handshake, request upload, response headers, body/SSE reads, and downstream delivery consume that same budget. A trickle of bytes does not reset it. Verification probes use the same transport with a ten-second budget.

Admission remains process-local: two active requests per endpoint/deployment, sixteen FIFO waiters, and at most thirty seconds waiting. The total deadline can expire earlier. A permit stays held until the upstream response closes. Verification shares this queue. Queue rejection or cancellation before dispatch sends no provider request and preserves prior readiness proof.

Cancellation shuts down the request's upstream socket, including while a read or TLS handshake is blocked. DNS uses four bounded resolver slots. Python cannot forcibly stop an operating-system resolver call; an abandoned resolver keeps its slot until it exits. Its late result cannot open a socket or dispatch a request. TCP setup is nonblocking, and TLS uses system trust and hostname verification.

On deadline expiry, the provider work stops. Harbor allows up to 250 milliseconds of additional time to report the failure to the client; this budget covers the whole error write. An unfinished SSE response receives `response.failed` if delivery remains possible. A nonstream response is read before sending success headers, so a stalled body can return a JSON 504. An already-started nonstream response is closed without mixing SSE into JSON. A disconnected or nonreading client may receive no final error.

## Delivery and task ownership

Harbor records the possibility of dispatch immediately before sending HTTP request data. DNS, TCP, TLS, and proxy CONNECT setup do not count as provider inference dispatch.

A dispatched request whose outcome was not fully delivered leaves the turn uncertain. That includes interrupted output, stalled error bodies, premature EOF against a declared content length, errors larger than the 64 KiB forwarding cap, and failed downstream delivery. The runtime refuses another request for that uncertain turn before dispatch. This prevents replay; it does not reconstruct a missing answer or assert that the provider cancelled work it already received.

The gateway closes the response before releasing admission. A known completed response can still ask Codex to run tools, so its task ownership remains retained through that gap. [Desktop completion and update handoff](desktop-lifecycle-evidence.md) remain a separate gate.

## Retry policy

Azure performs one upstream attempt per accepted gateway request. It does not automatically replay 429, 503, a transport failure, or partial output. A complete provider error preserves its status, body, and supported headers such as `Retry-After`. Forwarding that header is not an automatic backoff scheduler.

Generated `[model_providers.model-harbor]` configuration now explicitly sets both `request_max_retries = 0` and `stream_max_retries = 0`. Managed team workers already used those values. The shared stanza applies to all models routed through Harbor when Codex loads it. Baseten's internal gateway retry/pacing policy is unchanged.

An isolated test of installed `codex-cli 0.150.1` observed exactly one POST for each of four cases: 429, 503, an interrupted SSE delta, and accepted output followed by EOF before response completion. See the [repeatable harness and evidence](../experiments/codex-retries/README.md). This proves the fresh CLI process's behavior. It does not prove effective settings or reload behavior in an existing desktop task.

## Verification and limits

- `Tests/test_azure_transport.py`: bounded DNS, late results, connect/cancel races, TLS stall, trickling data, dispatch tracking, and cleanup.
- `Tests/test_azure_handler_deadline.py`: real loopback provider and Harbor handlers; queue plus transport deadline, partial output, nonstream timeout, cancellation, nonreading client, readiness, truncated verification framing, and no replay.
- `Tests/test_azure_error_budget.py`: truncated/oversized error framing and a header write that exhausts the budget before the body can be written.
- `Tests/SafetyTests.swift`: parsed TOML scope, all routed providers, repeated generation, replacement of prior managed retry values, and preservation of unrelated provider settings.

No live provider key or request is used by these tests. They do not establish Azure throughput, quota, cost, or latency. The deadline does not include an inbound request that has not finished uploading, and it does not add a deadline to other provider adapters. Admission is not coordinated between separate gateway processes. A composed Harbor–Bifrost–Azure route and real Azure continuation remain unverified. [Safe update rules](safe-updates.md) still govern installation.
