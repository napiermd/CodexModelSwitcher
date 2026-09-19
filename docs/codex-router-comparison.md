# Codex Router comparison and context repair

Reviewed 2026-09-18 against [codex-router commit 5e1b49e](https://github.com/duolahypercho/codex-router/tree/5e1b49e6e3fea1ada57f0e7fa6ca7612e9d17300). This is a source review, not a claim that its adapters pass Harbor’s provider/account tests. No upstream code was copied.

## Confirmed local defects

Azure setup assigned 128,000 to both context fields by default, even when Codex’s native cache supplied 272,000 and 872,000 for the exact Sol identity. The Grok catalog importer also discarded a distinct maximum. Both are repaired. Azure automatic context now matches deployment identity first and discovered base identity second, preserves manual limits, and migrates legacy default entries when publishing the shared catalog. The UI labels the automatic choice and the unknown-model fallback. Existing entries cannot distinguish an intentional legacy 128,000 override from the old default; the new explicit manual mode can.

The Azure and merged live catalogs were backed up and corrected to 272,000 / 872,000. These are observed native metadata values, not an independent measurement of Azure’s deployment capacity. Automatic lookup follows the existing Codex metadata; deployment-specific restrictions still require a manual override. A running Codex process may retain the previous catalog until it reloads. No task history or model selection was edited.

The stream bridge could close after premature EOF, socket timeout, or an exception without emitting a terminal failure. The repair emits a failure when delivery is possible, preserves already delivered output, and never invents completion or retries a partial stream. It improves failure reporting; it cannot reconstruct lost provider output. The screenshot alone does not establish the original disconnect cause.

## Relevant differences

| Area | Codex Router | Harbor / next action |
| --- | --- | --- |
| Capability metadata | `src/model-capabilities.mjs` merges defaults, live metadata, verified presets, and user overrides per field. Its conservative context default is 131,072. | Azure/Grok preserve distinct context limits. Published catalogs now record source SHA-256 digests and native context provenance. Generic 128,000 fallbacks carry explicit warning metadata. |
| Context choices | `src/native-context-variants.mjs` offers a hidden, opt-in larger-context Sol alias. `src/catalog.mjs` still assigns one generic context value to both fields. | Preserve distinct default/maximum metadata. A wholesale fork would not itself guarantee correct context. Larger context must be an explicit, provider-verified choice. |
| Catalog freshness | `src/native-catalog-freshness.mjs` compares captured and current Codex versions; native drift/source modules support reconciliation. | The owner-authenticated status endpoint compares captured versus running-backend Codex CLI versions and published source digests. It flags missing provenance and source drift. Desktop loaded-catalog state remains explicitly unknown; no automatic refresh or restart is claimed. |
| Silent reasoning | `src/responses-heartbeat.mjs` relays lifecycle heartbeats only after a response announces its identity and before a terminal event. | The candidate sends response.in_progress after an upstream response ID exists, every ten seconds of silence, until a terminal event. One writer serializes events and adjusts sequence numbers after an inserted heartbeat. Existing provider deadlines remain in force. |
| Broken streams | `src/http-utils.mjs` explicitly signals stream errors; `src/empty-completion-guard.mjs` guards empty success. | EOF/timeout/exception failure signals preserve partial output. Explicit empty output at completion now becomes a failure unless useful output already streamed. The last 32 failed requests expose provider, model, elapsed time and failure class through authenticated status, without prompts or credentials. |
| Request formats | API, OAuth, and provider-specific forwarders plus Chat Completions/Responses compatibility modules. | The candidate accepts SSE event names when JSON omits type and forwards native Codex images/generations and images/edits to the same caller’s subscription account. Chat Completions-only providers remain unsupported; no blanket compatibility claim is made. |
| Retry policy | `src/upstream-retry.mjs` offers bounded retries before any downstream bytes, plus provider policies. | Installed Harbor deliberately avoids Azure replay and records uncertain dispatches. No downstream bytes does not prove the upstream did no work. Preserve that protection when adopting retry behavior. |
| Safe runtime updates | Router has a different service/control-center architecture. This review did not verify transfer of active Harbor turns into it. | Installed Harbor has independent runtime ownership, dispatch uncertainty, and coordinated maintenance. Replacing it with the older task checkout would regress those safeguards. |

## Recommendation

Keep Harbor’s current signed interface, subscription credentials, per-task routing, and runtime ownership. Adopt the specific missing behaviors with regression tests. Evaluate codex-router on an isolated port and synthetic tasks before considering a backend migration. Stars are not evidence of compatibility with the current live tasks.

The candidate implements freshness diagnostics, provenance, lifecycle heartbeats, explicit empty-completion rejection, named SSE events, and native image endpoints. An isolated Chat Completions adapter now covers text, function calls, visibly labeled image omission, and model-scoped reasoning replay. It is not part of production routing because Harbor has no concrete Chat-only provider need. Remaining work is automatic capture reconciliation, verified desktop catalog adoption, and live provider validation before promoting that adapter.

## Build and activation

The task initially ran from `fix/openrouter-parameters` at `cedc468`, while the installed app uses the later independent gateway. The final candidate is based on `b72ad38` in branch `codex/context-stream-fix`, preserving those newer changes. The original checkout retains the equivalent source repair.

A signed candidate is staged under `build/staged-updates/context-stream-20260918/`. The backend and matching signed interface were subsequently activated through authorized coordinated maintenance; Azure and OpenRouter passed live verification. See `verification/context-stream-repair.md`. Corrected on-disk catalogs do not establish an in-memory desktop reload.

Verification completed: 103 Swift tests and 421 Python tests passed on the integrated source. The signed Xcode build and strict signature verification passed. A separate real-loopback Azure EOF regression was added afterward to verify terminal failure and retention of uncertain delivery. All 12 Azure deadline/EOF tests passed, including the new EOF regression.


## Follow-up patch, 2026-09-18

Branch `codex/router-hardening` builds on the activated context repair. The follow-up is now active at runtime `c40778835d0f26fc46e91a4deb66e7a4576a29b16017fc5f0ce733cb5d2184bd` after user-authorized coordinated maintenance. The signed interface matches it. Azure/OpenRouter readiness and an actual OpenRouter tool round trip passed.

The reported “response connection was interrupted” text comes from Harbor’s BrokenPipeError/ConnectionResetError path. It does not identify which peer reset the connection. Inspection found the gateway still running, three failed Azure requests, and successful requests afterward. The old service discards stderr and has no per-failure history. Consequently the exact initiating peer for those historical failures cannot be reconstructed. Heartbeats address silence; they cannot repair an actual socket reset.

Native image generation had a separate reproducible cause: Harbor’s allowlist rejected the tool’s two image POST paths before forwarding. The installed Codex binary and [OpenAI’s ImagesClient source](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/endpoint/images.rs) identify `images/generations` and `images/edits`. The candidate forwards only those additional paths to `https://chatgpt.com/backend-api/codex`, with local authorization plus the caller’s ChatGPT subscription credentials. It preserves payload bytes and the model supplied by Codex. It never reads another account or substitutes an API-billed image model.

Isolated live probes using deliberately incomplete bodies reached OpenAI and returned HTTP 400. This establishes upstream endpoint reachability, not completed generation or access to a particular image-model version. After activation, the originating task reported a successful real built-in image generation in 35.8 seconds without an external API key. Edit completion and access to any specifically named image-model version remain unverified.

See [router hardening verification](verification/router-hardening.md) for test and staging evidence. The earlier “Build and activation” section describes the previous context patch, not this follow-up.

## Remaining compaction work

The deployed router hardening is complete for its stated scope. The read-only [context compaction audit](verification/context-compaction-audit.md) reproduces the earlier small-window loop and confirms the later 258,400 effective task window. Provider usage attributes the dominant local-estimate difference to a fixed 129,025-token cached prefix, not to a gateway crash. Harbor now has content-free request composition metrics, single-snapshot catalog reconciliation, task-observed context diagnostics, a fail-closed capacity verifier, a content-free tool-result aging audit mode, and an isolated Chat Completions pilot with model-scoped reasoning replay. Larger context remains unavailable because the exact Azure deployment has not passed the new bounded capacity gate. The compatibility pilot remains outside production routing because no concrete Chat-Completions-only provider need was identified. Forking codex-router would not change either conclusion.
