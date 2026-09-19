# Codex-router adoption audit, round 2

Date: 2026-09-18. Upstream: [codex-router 5e1b49e](https://github.com/duolahypercho/codex-router/tree/5e1b49e6e3fea1ada57f0e7fa6ca7612e9d17300), unchanged since the first review. Method: five parallel explorers read 87 source files completely; no upstream code is copied. Harbor invariants applied throughout: never edit live conversation history, never replay a request after uncertain dispatch, diagnostics carry no prompts/credentials, fail closed.

## What changed since round 1

Nothing upstream. HEAD is identical. What changed is coverage: round 1 reviewed the catalog/heartbeat/summary layer; this round read the stream pipeline, failover core, usage ledger, compaction engine, provider adapters, and tool-surface code that round 1 only named.

## Already in Harbor (do not re-port)

- SSE lifecycle heartbeats with sequence repair: `response_stream.py` (`Lifecycle`).
- Empty completion becomes `response.failed` with no replay: `Translation.validate_completion`.
- EOF before terminal event becomes a 502 terminal failure; partial output preserved: grok_adapter stream loop.
- Terminal failure frame writes, single-writer relay, bounded budgets.
- Catalog provenance, race rollback, native context resolution, image generation routing (rounds 1 and the context-audit branch).
- Empty-completion **detection**: done. codex-router's terminal-hold-then-retry is deliberately excluded (dispatch was consumed; Harbor records instead).

## Root gaps this audit found

1. **No append-only usage ledger.** Harbor keeps failed-request memory (32 entries) and CodexBar reads, but no durable per-request event record. Every observability feature codex-router has (drift detection, quota surfaces, TTFT, aging evidence) hangs off that ledger. Without it Harbor cannot prove window drift or answer "what did this model do yesterday."
2. **No context-window drift detection.** codex-router `context-window-drift.mjs` proves a declared window wrong from provider-accepted input tokens. Harbor's audit found the symptom (121,600 effective vs 272,000 declared) by hand; nothing automates it.
3. **No byte-based usage estimator.** When a provider reports zero/missing usage, Harbor records nothing. codex-router's `response-usage.mjs` estimates from visible bytes (3.3 bytes/token, per-image bounds, clamped to window). This is the input both the drift detector and compaction-threshold math need.
4. **Request composition metrics are in-memory only.** `request_metrics.py` retains 32 records and loses them at process exit. No persistence, no rollup.
5. **Tool-result aging.** codex-router's proven 86% input-token reduction on result-heavy requests. Porting the rewrite conflicts with Harbor's no-history-edit rule; only a non-destructive estimator/advisory is compatible.
6. **Stream validation of completed tool calls.** Harbor relays a completed `function_call` without validating its arguments are parseable JSON; a poisoned call stored in history breaks subsequent turns. codex-router fails the turn closed (502) instead.
7. **Reasoning replay for Chat-bridged thinking models.** Baseten routes carry reasoning models that require `reasoning_content` replay on the assistant turn; Harbor currently strips reasoning for Grok and handles Azure specially, but has no per-family replay table. codex-router's `chat-reasoning.mjs` table prevents both 400s and reasoning-as-prose loops.
8. **Codex app tool surface merge.** Deferred `codex_app`/`multi_agent` tools are invisible to routed models unless merged from a snapshot with namespace restore. codex-router ships the snapshot; Harbor has namespace flatten/restore for declared tools but no deferred-merge step. (Round 1 comparison noted namespace support; the merge-from-snapshot half is new information.)
9. **Image handling on text-only routes.** Harbor's `chat_compat` pilot rejects images outright. codex-router strips them to a stated "unreadable" block or transcribes via a vision bridge. The strip-to-notice baseline is safe to adopt; the transcription bridge is not (content mutation + engine trust).
10. **Per-workload transport discipline.** One connection pool per class (streaming vs probes), client idle below server keep-alive, and transport-failure classification into named diagnoses. Harbor has `NoRedirect` openers but no pooled-separation or diagnosis table.

## Explicitly rejected ports

- Mid-turn model failover (`model-failover.mjs` swap path): replays a consumed dispatch. Cooldown-store half may port later as next-request advisory only.
- Invisible empty-completion retry: same reason.
- `compaction-checkpoint.mjs` stream rewriting: designed to edit history. Only a produce-checkpoint-as-data variant could ever be considered.
- `early-tool-item-done.mjs` (authors done frames), `leaked-tool-call-recovery.mjs` (creates tool calls from text), `reasoning-tag-stripper.mjs` content deletion on arbitrary routes: mutation risk without a Harbor need.
- Vision transcription bridge, MiniMax media CLI, local Ollama vision host, WebSocket termination, search sidecar: no current Harbor need.
- Control Center/tray/desktop widget: different product.

## Implementation plan (issue order)

1. Usage-event ledger: append-only JSONL, 0600, bounded reads, rotation, content-free fields.
2. Byte-based usage estimator feeding the ledger (with image bounds) for zero/missing-usage responses; marked estimated, never used to disprove provider counts.
3. Context-window drift report over the ledger; warn-only; retry/estimate exclusion rules.
4. Tool-call argument validation at completion; fail closed with terminal error frame.
5. Reasoning replay table for Chat-bridged thinking models on Baseten/OpenRouter.
6. Request-metrics persistence: periodic rollup from memory to ledger; no raw content.
7. Deferred app-tool surface merge from a checked-in snapshot with namespace restore and fail-loud provider tool caps.
8. Image strip-to-notice on text-only Chat routes (pilot scope only).
9. Transport pools + failure diagnosis table.
10. Tool-result aging estimator/advisory (estimate only; no rewrite).

## Risks

- The ledger is a privacy surface: fields must stay counts/IDs/statuses; path must never include prompts, tool args, or response text. Rotation caps growth.
- Estimates must be labeled and never mixed into provider-reported totals; codex-router's own retry-exclusion comments show how a merged pair invents a 2x window.
- Tool-argument validation must respect custom/namespace codecs that legitimately carry non-JSON.
- Reasoning replay is per-family; a stale or over-broad table silently degrades non-thinking models. Evidence-backed entries only.
- App-tool snapshot staleness: merging definitions the installed Codex cannot dispatch produces calls that never execute. Snapshot must record the capture version and fail closed on drift.
- Image strip must be visibly labeled so the model never claims to have seen what it did not.

## Execution checklist

- [x] Verify upstream HEAD unchanged; clone and read 87 files across five explorers.
- [x] Map each candidate against Harbor invariants; reject replay/history-edit ports.
- [x] Write this audit and checklist.
- [x] Create focused Linear issues for items 1-10 with acceptance criteria and tests (SAY-3370 through SAY-3379).
- [x] Implement in order on `codex/router-adoption-audit`.
- [x] Full Python and Swift suites, build, signature verification.

## Outcome

All ten items implemented and verified on 2026-09-18:

1. Usage ledger: `ModelHarbor/Support/usage_ledger.py`, wired into `grok_adapter.py` completion paths. 11 tests.
2. Usage estimator: `ModelHarbor/Support/usage_estimate.py`, feeds the ledger on zero/missing provider usage. 8 tests.
3. Drift report: `scripts/report-context-drift.py`, read-only, retry/estimate exclusions intact. 7 tests.
4. Tool-call argument validation: completed function calls with unparseable arguments fail the turn with `invalid_function_call_arguments`; custom codecs exempt. 6 tests.
5. Reasoning replay table: `ModelHarbor/Support/chat_reasoning.py`, evidence-backed family matching, passthrough for unlisted models. 7 tests. Wired as a pure table; the Baseten Chat translation path consumes it when routes bridge.
6. Metrics rollups: hourly content-free aggregates flush through the ledger at shutdown when metrics are enabled; flushed sequences are deduplicated. 4 tests.
7. App-tool merge: `codex_app_tools.py` plus a versioned snapshot; client definitions win, namespace restore round-trips, gated by `MODEL_HARBOR_CODEX_APP_TOOLS=1` (off by default until the snapshot is validated against live captures). 7 tests.
8. Image strip-to-notice in the Chat compat pilot: labeled non-delivery notice in messages and tool results; content never leaks into the notice. 15 pilot tests.
9. Transport pools and diagnoses: `transport.py` separates stream and probe openers and classifies failures into stable named classes. 5 tests.
10. Tool-result aging estimator: `tool_result_aging.py` computes reclaimable bytes with the reference eligibility rules; pure functions, no mutation. 8 tests.

Final verification: 543 Python tests pass, 108 Swift tests pass, Xcode build succeeds, strict deep signature verification passes. One regression was caught during integration (missing `ssl` import in the new failure handler) and fixed in `0fe8a0d` before the final run.
