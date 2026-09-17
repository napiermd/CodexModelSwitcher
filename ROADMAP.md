# Roadmap

Current direction, not a release promise. Discuss priorities in [issues](https://github.com/napiermd/model-harbor/issues).

## Working in the source preview

- Named model choices that persist independently in Codex tasks.
- Current Codex subscription, official Grok browser OAuth, direct Baseten routing, and optional OpenRouter connections.
- Shared in-memory Baseten credential caching with explicit reconnect after failure.
- Provider connection and request activity, plus customizable menu-bar quota, spend, and reset displays.
- Codex and Grok usage, Baseten organization costs, OpenRouter key spend, and optional CodexBar cached history.
- Dock/menu-bar choices, remembered close behavior, launch at login, appearance, and optional bounded Codex warm-up.
- Fixed-model coding roles with a launcher for isolated worktrees.
- Native connection management, MIT license, public docs, and local automated checks.

## Next

- A signed and notarized macOS release, with a documented reproducible packaging process.
- First-run connection checks and guided Baseten catalog setup.
- Broader compatibility coverage across Codex releases, provider models, tools, and modalities.
- Live verification of login startup after reboot and opt-in warm-up.
- Native local token history to reduce dependence on optional CodexBar snapshots.
- Clearer handling when an account loses access to a model.
- A supported process for proposing and validating more live providers.

## Still constrained

Codex caches its model catalog, so adding catalog entries requires reopening it. Switching a saved Codex account is separate from model selection and still needs a restart. Account identities are shared connections rather than isolated per task. The bridge depends on provider protocols and account access it does not control.
