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
- [ ] Create focused Linear issues for items 1-10 with acceptance criteria and tests.
- [ ] Implement in order on `codex/router-adoption-audit`.
- [ ] Full Python and Swift suites, build, signature verification.
- [ ] Independent review; fix findings.
- [ ] Update Linear issues to Done with evidence.
