# Model Harbor

Model Harbor is Andrew's personal fork of CodexModelSwitcher. Its identity is distinct while retaining the upstream native SwiftUI controls and provider editor.

The macOS utility is an Operate surface: select a Codex account or provider, see the authentication method, and understand when a restart is required. Use the system typeface, native keyboard behavior, and system appearance.

The icon is an authored branching route on a deep teal rounded square. Pale routes terminate in two destinations; an amber arrival point anchors the mark. It is generated from `scripts/render-icon.swift`, with no third-party imagery. The header uses the same mark. The menu bar reads Harbor beside an SF Symbols signpost.

Account methods appear in provider headings: Codex accounts, Baseten direct API, and Grok browser sign-in. Grok's account email and Sign in button sit with its model list. The app loads available OAuth models from the signed-in account. Keychain migration is an explicit recovery action; startup does not present permission prompts.
