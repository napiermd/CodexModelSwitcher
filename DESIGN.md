# Model Harbor

Model Harbor is Andrew's personal fork of CodexModelSwitcher. Its identity is distinct while retaining the upstream native SwiftUI controls and provider editor.

The macOS utility is an Operate surface: manage provider connections and understand where each task selects its model. Use the system typeface, native keyboard behavior, and system appearance.

The icon is an authored branching route on a deep teal rounded square. Pale routes terminate in two destinations; an amber arrival point anchors the mark. It is generated from `scripts/render-icon.swift`, with no third-party imagery. The header uses the same mark. The menu bar reads Harbor beside an SF Symbols signpost.

Account methods appear in provider headings: Codex current subscription, saved Codex accounts, Baseten direct API, and Grok browser sign-in. Grok's account email and Sign in button sit with its model list. The app loads available OAuth models from the signed-in account. Keychain migration is an explicit recovery action; startup does not present permission prompts.

The main window shows Codex subscription, Baseten, and Grok connections. Model lists are read-only disclosure groups. Model selection lives in Codex's per-task picker. The header says Connections rather than implying that one model is active everywhere. A separate New task default menu affects future tasks only.

Saved Codex accounts and other direct providers are under a collapsed disclosure group. Their controls retain the restart warning for account changes. Do not imply that the current task uses a particular model based on Harbor's default.

Baseten displays real cache state from the local status endpoint, which never invokes 1Password. A visible Reconnect Baseten button provides recovery after a canceled unlock or key change. Requests never relaunch the unlock prompt after failure. Grok displays verified browser sign-in status and its account action.

A one-time migration message explains that Codex must reopen to load the new model list. Each model thereafter stays with its task. The proposed Codex sidebar from the design mockup is illustrative; this fork does not modify Codex's native interface.


## Public website and repository

Extend Model Harbor's teal and amber mark onto a warm paper background. Use Newsreader for large editorial headings, Instrument Sans for navigation and reading text, and system monospace only for code. Self-host fonts. The public page gives task-level selection the most visual space through an interactive, clearly labeled demonstration. Avoid a generic feature-card grid.

The homepage leads with the benefit and a source-build link. Follow with the routing demo, connection methods, source transparency, setup, limitations, and contribution links. Each model selection changes only its own demonstration task. Preserve that behavior on narrow screens. Use visible keyboard focus, sufficient contrast, reduced-motion support, and native selects. Do not present the demo as a screenshot of the Codex interface.

The mark remains a pale branching route in a deep teal macOS icon shape, with one amber node. Master geometry lives in the icon renderer and the vector brand asset. No third-party logo or upstream screenshot is used as the Model Harbor identity.
