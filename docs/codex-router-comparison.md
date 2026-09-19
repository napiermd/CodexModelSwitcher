# Codex Router comparison and context repair

Reviewed 2026-09-18 against [codex-router commit 5e1b49e](https://github.com/duolahypercho/codex-router/tree/5e1b49e6e3fea1ada57f0e7fa6ca7612e9d17300). This is a source review, not a claim that its adapters pass Harbor’s provider/account tests. No upstream code was copied.

## Confirmed local defects

Azure setup assigned 128,000 to both context fields by default, even when Codex’s native cache supplied 272,000 and 872,000 for the exact Sol identity. The Grok catalog importer also discarded a distinct maximum. Both are repaired. Azure automatic context now matches deployment identity first and discovered base identity second, preserves manual limits, and migrates legacy default entries when publishing the shared catalog. The UI labels the automatic choice and the unknown-model fallback. Existing entries cannot distinguish an intentional legacy 128,000 override from the old default; the new explicit manual mode can.

The Azure and merged live catalogs were backed up and corrected to 272,000 / 872,000. These are observed native metadata values, not an independent measurement of Azure’s deployment capacity. Automatic lookup follows the existing Codex metadata; deployment-specific restrictions still require a manual override. A running Codex process may retain the previous catalog until it reloads. No task history or model selection was edited.

The stream bridge could close after premature EOF, socket timeout, or an exception without emitting a terminal failure. The repair emits a failure when delivery is possible, preserves already delivered output, and never invents completion or retries a partial stream. It improves failure reporting; it cannot reconstruct lost provider output. The screenshot alone does not establish the original disconnect cause.

## Relevant differences

| Area | Codex Router | Harbor / next action |
| --- | --- | --- |
| Capability metadata | `src/model-capabilities.mjs` merges defaults, live metadata, verified presets, and user overrides per field. Its conservative context default is 131,072. | Harbor has separate provider builders and a generic 128,000 fallback. This repair addresses Azure/Grok; centralize capability provenance and expose fallback warnings next. |
| Context choices | `src/native-context-variants.mjs` offers a hidden, opt-in larger-context Sol alias. `src/catalog.mjs` still assigns one generic context value to both fields. | Preserve distinct default/maximum metadata. A wholesale fork would not itself guarantee correct context. Larger context must be an explicit, provider-verified choice. |
| Catalog freshness | `src/native-catalog-freshness.mjs` compares captured and current Codex versions; native drift/source modules support reconciliation. | Harbor does not have equivalent version-aware native capture reconciliation. Add catalog source/version tracking and a loaded-versus-published diagnostic. |
| Silent reasoning | `src/responses-heartbeat.mjs` relays lifecycle heartbeats only after a response announces its identity and before a terminal event. | Harbor only emits comments during Baseten capacity waits. Those do not provide equivalent lifecycle coverage. Add bounded heartbeats without extending provider deadlines or faking output. |
| Broken streams | `src/http-utils.mjs` explicitly signals stream errors; `src/empty-completion-guard.mjs` guards empty success. | This patch adds missing EOF/timeout/exception failure signals. Empty-success validation remains a separate gap. |
| Request formats | API, OAuth, and provider-specific forwarders plus Chat Completions/Responses compatibility modules. | Harbor primarily requires Responses support. Keep working subscription/account and tool translation paths until replacement adapters pass round-trip tests. |
| Retry policy | `src/upstream-retry.mjs` offers bounded retries before any downstream bytes, plus provider policies. | Installed Harbor deliberately avoids Azure replay and records uncertain dispatches. No downstream bytes does not prove the upstream did no work. Preserve that protection when adopting retry behavior. |
| Safe runtime updates | Router has a different service/control-center architecture. This review did not verify transfer of active Harbor turns into it. | Installed Harbor has independent runtime ownership, dispatch uncertainty, and coordinated maintenance. Replacing it with the older task checkout would regress those safeguards. |

## Recommendation

Keep Harbor’s current signed interface, subscription credentials, per-task routing, and runtime ownership. Adopt the specific missing behaviors with regression tests. Evaluate codex-router on an isolated port and synthetic tasks before considering a backend migration. Stars are not evidence of compatibility with the current live tasks.

Prioritize capability provenance/freshness, loaded-catalog diagnostics, bounded lifecycle heartbeats, and empty-completion tests. Avoid a broad gateway replacement during active work.

## Build and activation

The task initially ran from `fix/openrouter-parameters` at `cedc468`, while the installed app uses the later independent gateway. The final candidate is based on `b72ad38` in branch `codex/context-stream-fix`, preserving those newer changes. The original checkout retains the equivalent source repair.

A signed candidate is staged under `build/staged-updates/context-stream-20260918/`. Staging does not activate its backend. Follow `docs/safe-updates.md` for coordinated activation and provider verification. Corrected on-disk catalogs do not establish an in-memory desktop reload.

Verification completed: 103 Swift tests and 421 Python tests passed on the integrated source. The signed Xcode build and strict signature verification passed. A separate real-loopback Azure EOF regression was added afterward to verify terminal failure and retention of uncertain delivery. All 12 Azure deadline/EOF tests passed, including the new EOF regression.
