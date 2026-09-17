# Update safety audit and execution plan

Audited September 17, 2026, before implementation. Source: `80429fddf8e3f2675a609b0500d3818a445bca51`, merged to `main` as `ba8966e17751ca9f823ad797719e636bfc332471`.

Tracking: [Model Harbor — safe updates and gateway evaluation](https://linear.app/sayvant/project/model-harbor-safe-updates-and-gateway-evaluation-0a945e5e65c4).

## Root issue

The update interrupted the shared gateway because its lifetime belongs to the menu app. The installer treated a momentarily empty HTTP request counter as proof that work had finished. A Codex turn can be running tools between requests at that exact moment. Terminating the bridge then interrupts its next request as well as any streams caught by the race.

Two related weaknesses made the update appear healthier than it was:

- `provider_status()` calls credential presence `azure_ready` / `openrouter_ready`. The retired installer accepted the presence of the Azure field even when its value was false.
- Turn routes, provider connections, and activity live in process memory. There is no candidate admission transaction, retained old runtime, verified turn-completion source, or handoff recovery journal.

The ad hoc installer was retired before this audit. Operational instructions are in [Protect tasks during Harbor updates](safe-updates.md). Neither measure implements interruption-free runtime replacement.

## Source map

| Concern | Files and observed behavior |
| --- | --- |
| UI owns gateway | `ModelHarbor/AppDelegate.swift`: `applicationWillTerminate` calls `stopAdapter`. `GrokAdapter.swift` owns and terminates the Python `Process`; its health check requires that child to be running. |
| App startup/readiness | `ModelHarbor/AppStore.swift`: process health sets the proxy active before provider synchronization; provider flags come from bridge status. |
| Gateway shutdown | `ModelHarbor/Support/grok_adapter.py`: production entrypoint watches the original parent; server handler threads are daemon threads. |
| Routing ownership | `grok_adapter.py`: `route_for_turn` stores `(thread, turn)` pins in an in-memory `OrderedDict`, evicting entries above 4096 without completion evidence. |
| Credentials | `CredentialStore.swift` uses Keychain and stable signing trust. The bridge's Grok auth path derives from `HOME`, so a separate port/config directory alone does not isolate a test. |
| Stable model selection | `LiveRouting.swift` defines per-task `harbor/<provider>/<model>` identifiers; `CodexConfigWriter.swift` writes the shared catalog/config. Preserve these interfaces during runtime changes. |
| Build/release | `scripts/build-app.sh` builds and signs, with no tracked installation or staged-release workflow at audit time. |
| Verification gap | Provider tests and `scripts/verify-live-switch.py` import `Handler`; they bypass the production parent watcher. The harness sees lifecycle events only on the app-server it launches. |

## What remains unknown

Codex documents `turn/started` and `turn/completed`, but that does not establish a supported way for Harbor to subscribe to the existing desktop host. Prove the integration before enabling turn-aware retirement. If reliable completion is unavailable, retain the old runtime and defer replacement to coordinated maintenance. Quiet counters, elapsed time, and LRU eviction cannot establish completion.

Also verify whether provider-side response IDs or other conversational state require affinity beyond a single turn. A successfully completed HTTP stream alone does not answer that question.

## Bifrost's possible role

Evaluate Bifrost as the API-provider gateway behind Harbor, starting with Azure. Harbor would retain desktop integration and existing subscription adapters unless those flows are separately demonstrated to work through Bifrost.

The pilot must pin the release and image digest, record edition constraints, disable content logging and semantic caching, and measure actual Azure behavior against the existing route. Keep retries owned by one layer, with a bounded total deadline. Bifrost documents a boundary after which delivered text, reasoning, or tool output prevents stream retry; it cannot transparently repair every interrupted turn.

No decision to install Bifrost or route production traffic follows from the audit.

## Focused implementation sequence

| Issue | Deliverable | Gate |
| --- | --- | --- |
| [SAY-3338](https://linear.app/sayvant/issue/SAY-3338) | Stage a signed build with hashes and an honest provenance manifest, without installation or process control. | First safe implementation unit. |
| [SAY-3339](https://linear.app/sayvant/issue/SAY-3339) | Gateway supervisor independent of GUI lifetime, with attachment and duplicate-start handling. | Staging first; initial installed migration remains maintenance work. |
| [SAY-3340](https://linear.app/sayvant/issue/SAY-3340) | Separate process health, credential availability, verified route readiness, and runtime/config identity. | Required before promotion. |
| [SAY-3341](https://linear.app/sayvant/issue/SAY-3341) | Prove desktop lifecycle events and retain route ownership through tool gaps and reconnects. | Stop live-handoff work if reliable completion cannot be established. |
| [SAY-3342](https://linear.app/sayvant/issue/SAY-3342) | Hermetic production-entrypoint integration harness with controllable stream/tool/lifecycle barriers. | Extend with runtime work; pass before handoff is enabled. |
| [SAY-3343](https://linear.app/sayvant/issue/SAY-3343) | Verified candidate promotion, turn-aware draining, and reversible new-turn admission. | Blocked by SAY-3338 through SAY-3342. |
| [SAY-3344](https://linear.app/sayvant/issue/SAY-3344) | Isolated pinned Bifrost/Azure comparison and a documented adoption decision. | Independent experiment; no live route change. |

## Risks and controls

- **Another outage:** no shared process termination, installed bundle replacement, or live configuration changes during development. Build and stage only.
- **False readiness:** missing, false, stale, or configuration-mismatched provider verification blocks promotion. Credentials alone are insufficient.
- **Lost turn ownership:** retain owners through tool gaps. Missing completion evidence prevents retirement. Do not silently evict unfinished turns.
- **Credential prompts or leakage:** preserve signing/Keychain trust and session caches. Do not put keys, prompts, private endpoints, or account metadata in release manifests or issue evidence.
- **Duplicate tools or output:** never retry a partially delivered turn by replaying visible output or tool executions.
- **Long nested retries:** choose one retry owner and measure the total attempt/deadline budget, including client behavior.
- **Migration incompatibility:** keep stable model IDs and the public endpoint; make catalog/config changes atomic and compatible with retained workers.
- **Overstated verification:** separate fixture results, real provider probes, desktop integration, staging, and installation in reports.

## Required tests

The pre-change baseline passed eight provider-connection tests and seven Azure tests. They establish existing request behavior, not update safety.

Release staging must test byte/signature integrity, malformed input, overwrite refusal, concurrent publication, path/symlink boundaries, partial-copy cleanup, truthful provenance, and an unaffected mock gateway plus state sentinels. Verify one real signed artifact without executing it.

Runtime work must additionally demonstrate:

1. Two concurrent model streams survive candidate preparation and admission changes.
2. A turn with zero HTTP requests during a delayed tool resumes on its original owner.
3. UI exit/reopen preserves the independent gateway.
4. Failed candidate credentials, startup, port binding, or readiness leave old work usable.
5. Rollback changes new-turn admission while respecting established ownership.
6. Cancellation and partial-stream failures cause no duplicate tool execution or replayed output.
7. Missing/reordered lifecycle events and ownership pressure cannot trigger unsafe retirement.
8. Task history, selected models, provider/account choices, and unrelated configuration remain unchanged.

Tests must use isolated `HOME`, config, token, state, and ports. No automated test registers a real login service or kills an active user's gateway.

## Execution checklist

- [x] Read-only audit and baseline checks before implementation.
- [x] Create focused Linear issues with dependencies and acceptance tests.
- [x] Deliver and locally verify stage-only release tooling (SAY-3338; review pending).
- [ ] Separate gateway ownership and implement verified readiness.
- [ ] Establish reliable desktop turn lifecycle and retained ownership.
- [ ] Pass the production-entrypoint lifecycle test matrix.
- [ ] Implement and verify promotion, draining, and rollback.
- [ ] Complete the isolated Bifrost pilot and record go/no-go.

## Implementation evidence, September 17, 2026

The independent runtime, authenticated control connection, configuration-bound readiness, and durable unfinished-turn ownership are implemented in the staged candidate. The local suite passes 231 Python tests and 71 Swift tests. All 10 production-entrypoint lifecycle tests also pass against the signed app resources, and the candidate passes signature/inventory staging. See [verification results](verification/safe-runtime-results.md).

The original completion gates above remain unchanged. Actual desktop completion observation is still unproven, so promotion, retirement, rollback, and shutdown refuse. The production-entrypoint matrix covers these refusals, not successful rolling handoff. The [Bifrost pilot](bifrost-evaluation.md) produced a no-go decision after reasoning-history and retry-policy failures. Its live Azure, concurrency, queue, and cost comparison requirements remain open. No installed app, live routing, or user task history was changed.

## Primary references

- [Codex app-server events](https://learn.chatgpt.com/docs/app-server)
- [Bifrost repository](https://github.com/maximhq/bifrost)
- [Bifrost Azure provider](https://docs.getbifrost.ai/providers/supported-providers/azure)
- [Bifrost retries and fallback boundaries](https://docs.getbifrost.ai/features/retries-and-fallbacks)
