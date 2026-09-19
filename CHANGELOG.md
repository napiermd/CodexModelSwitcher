# Change log

This tracks changes to the source preview. It does not announce a notarized installer or a numbered public release.

## Unreleased

- Azure now forwards encrypted reasoning only to the exact Azure resource and deployment that produced it. Cross-provider ciphertext is removed before JSON or streamed dispatch, while portable messages and tool-result links remain.
- Fixed OpenRouter Fable 5.1 tool-request 404s by using normal endpoint selection in inference and saved-connection verification. Added a live synthetic tool round trip and HTTP regression coverage. See [verification](docs/verification/openrouter-tools.md).
- Task-repair batches now report loaded and invalid tasks individually and continue repairing other eligible tasks. Added a Codex 0.150.1 end-to-end check for the unsupported Harbor model error during remote compaction, followed by provider repair, successful compaction, and continuation. README and recovery docs distinguish this saved-provider mismatch from tool-choice validation failures.

- Azure setup can discover ready deployments using the resource key, explains missing fields, and shows elapsed progress with cancellation and bounded network timeouts. Saved deployment options are restored when editing.
- Tool-free Codex compaction requests now discard stale tool choices before provider routing, preventing long-running tasks from entering an unrecoverable retry loop at the context limit.
- Azure routes now select themselves for Codex automatic review, so `--approve-for-me` does not depend on a missing Azure review model.

### Update safety foundation

- Stage signed artifacts with a content manifest, without changing the installed app or running gateway.
- Prepare a per-user gateway service with retained Python resources and exclusive startup ownership. GUI quit no longer terminates this independent runtime.
- Persist unfinished turn ownership through tool gaps; refuse duplicate dispatch, changed account bindings, and replay after uncertain delivery. Storage pressure retains existing owners.
- Separate saved credentials from route verification tied to runtime, configuration, and deployment. Status polling does not trigger paid inference.
- Exercise the production entrypoint and packaged runtime with isolated state and synthetic providers.
- Keep live promotion, retirement, and rollback disabled until actual desktop completion signals are verified. The independent service and signed interface have separate local installation evidence; newer gateway code remains pending maintenance. See [installed interface verification](docs/verification/gui-attachment.md). No uninterrupted gateway replacement is claimed.

### Azure OpenAI

- Remove stale tool choices from tool-free compaction requests across native and translated routes, while preserving opaque Azure history.
- Pin each Azure catalog entry's automatic-review model to its own Harbor deployment route. This hardens routing without claiming that a missing override caused the reported failure.

- Added direct Azure Responses routing with resource endpoint and exact deployment names, macOS Keychain storage, and a billable tool-call connection check.
- Deployment options explicitly select image input, context limits, and the reasoning setting tested during setup. Azure requests do not use Baseten's queue.
- Setup can make Azure the new-task default and hide Baseten. Provider visibility now lives in Settings → Providers and applies to the usage picker too.
- Restricted credential transmission to direct HTTPS Azure resource domains and blocked redirects. Azure authentication failures clear the bridge's working credential; quota errors preserve the connection.
- Azure spend and quotas remain available through the Azure dashboard; Harbor does not report invented billing totals.

### Dock, startup, and warm-up

- Keep Harbor in the Dock and menu bar, or use the menu bar alone.
- Choose how the window closes, with a native dialog and an optional remembered choice. Closing keeps the bridge running.
- Register launch at login through macOS and show approval or error states.
- Group window behavior, startup, appearance, and optional Codex warm-up in General settings.
- Add manual, after-startup, and daily warm-up with an editable prompt. Warm-up is off by default, uses subscription quota, and limits automatic attempts to once per local day.
- Add confirmed graceful or force Codex closing, with optional reopening. Graceful timeout never forces an exit.
- Correct login launch-event detection and test malformed credentials and incomplete warm-up streams.

Implementation and review: [#9](https://github.com/napiermd/model-harbor/pull/9).

### Usage, spend, and effort controls

- Show Codex account quota and resets, Grok subscription quota, Baseten organization costs, and OpenRouter key spend.
- Add menu-bar displays for remaining quota, today's spend, and next reset.
- Import optional CodexBar cached history with source labels. Token-value estimates remain separate from provider charges.
- Order provider effort choices from lower to higher, including Grok.

Implementation and review: [#8](https://github.com/napiermd/model-harbor/pull/8).

### Existing source-preview capabilities

- Independent model choices within Codex tasks, using Codex subscription, Grok OAuth, Baseten, or OpenRouter routes.
- Shared Baseten credential caching, queued dispatch, token-aware pacing, and bounded overload retries.
- Screenshot handling for compatible Baseten models and preservation of tool results.
- Automatic repair for inactive OpenAI tasks that selected a Harbor model.
- Fixed-model planning, coding, and review roles with separate worktrees for concurrent writers.

See [verification scope](FORK.md), [setup](docs/getting-started.md), and [known constraints](ROADMAP.md#still-constrained).
