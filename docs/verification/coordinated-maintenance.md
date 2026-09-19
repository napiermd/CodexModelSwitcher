# Coordinated gateway activation — September 17, 2026

## Installed result

The first successful coordinated activation installed the signed app at `/Applications/Model Harbor.app` and runtime `12b4255cc0d7183bba97a4ce017a2d8fdda1e4b670c070f063571a5ec5477a5c`. The prior runtime was retained for rollback. The installed signature used the same signing authority as before. Later maintenance records identify their own exact installed digest; this section records the initial live proof.

The successful private transaction ended in `resumed`. All captured Codex processes resumed after **6.84 seconds**; none remained stopped. Codex was not quit or restarted. Two Codex-subscription requests completed through the new gateway immediately afterward, with zero failed requests on that provider at the observation point. Current-boot live probes verified Azure `gpt-5.6-sol` and OpenRouter `anthropic/claude-fable-5.1`.

A separate active desktop task retained its existing in-progress turn and reported no turn error after activation.

The actual Harbor interface displayed **Azure — Connected**, **Ready for the next request**, and **Bridge online — 3 connections** after reopening. Its Codex and OpenRouter tabs also displayed Connected.

## Preservation checks

- Same configuration revision before and after activation.
- Identical hashes for tracked Codex configuration, authentication, saved provider metadata, model catalogs, bridge token, and server proof.
- Every captured ownership row retained its route, account binding, and uncertainty flag; only the runtime field transferred.
- Request rows and counters matched the captured snapshot immediately before client resumption.
- Three pre-existing uncertain turns remained uncertain; no requests or tools were replayed to hide them.
- The temporary maintenance marker was removed only after the committed receipt and readiness checks. Later normal settings changes are therefore not trapped behind a stale maintenance revision.

The first attempt detected a request racing the process pause, aborted before replacing the service, and resumed clients. The second attempt passed the pause checks and completed. This demonstrates the refusal path as well as the successful transfer; it does not establish zero-delay rolling updates.

## Tests and review

- Full local Python suite: **406 tests passed** before the final two cleanup tests were added. GitHub subsequently passed **408 Python tests, 97 Swift tests, and 10 packaged gateway tests** on commit `0b32ab0958aaf15e6ff3edb481b01b30c4f40681`, plus website and native Azure checks on AMD64/ARM64.
- Final controller suite: **19 tests passed**, including both new cleanup cases, with resource warnings treated as errors.
- Maintenance gateway and adjacent routing suites: **131 tests passed**, including fourteen new gateway maintenance cases.
- Signed macOS build, strict signature verification, staged bundle inventory, and installed inventory verification passed.
- Independent review found recovery gaps in marker publication ordering, watchdog completion, prior-marker rollback, and lock failure. Fixes and regression tests preceded the live activation.

The controller tests exercise stream-race refusal, fixed route/account continuation through a tool gap, uncertainty preservation, idempotent ownership transfer and rollback, exclusive journal locking, PID reuse, crash phases, prior completed-marker rollback, and final marker cleanup. Live preflight and post-start restoration use the production gateway entrypoint with real saved provider credentials kept in memory.

## Follow-up: consistent connection-check deadlines

A repeated live check exposed a separate OpenRouter probe limit: the manual check still used a two-second socket timeout, while saved-connection restoration used the ten-second absolute verification budget. Both supported API providers now use the same cancellable absolute budget; Azure retains its deployment admission control. This does not add retries or change inference routing.

Two new real HTTP fixture cases verify that delayed headers and delayed bodies arriving after two seconds can succeed within the budget, while a response exceeding the absolute deadline is stopped and never marked verified. All 29 provider-connection and handler-deadline tests passed after the change; the signed build passed.

## Limits

This is explicit maintenance with a brief process pause. Automatic rolling promotion, old-worker draining using authoritative desktop turn-completion signals, and multi-runtime admission remain separate work. The controller supports retained Azure, OpenRouter, and Codex-subscription routes with unchanged account bindings. Bifrost remains an isolated evaluation outside the installed production path. This successful activation does not establish new latency or cost claims for providers, nor does it repair unrelated previously uncertain conversations.
