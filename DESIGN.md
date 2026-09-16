# Model Harbor

Model Harbor is Andrew's personal fork of CodexModelSwitcher. Its identity is distinct while retaining the upstream native SwiftUI controls and provider editor.

The macOS utility is an Operate surface: select a Codex account or provider, see the authentication method, and understand which tasks follow the live model selection. Use the system typeface, native keyboard behavior, and system appearance.

The icon is an authored branching route on a deep teal rounded square. Pale routes terminate in two destinations; an amber arrival point anchors the mark. It is generated from `scripts/render-icon.swift`, with no third-party imagery. The header uses the same mark. The menu bar reads Harbor beside an SF Symbols signpost.

Account methods appear in provider headings: Codex current subscription, saved Codex accounts, Baseten direct API, and Grok browser sign-in. Grok's account email and Sign in button sit with its model list. The app loads available OAuth models from the signed-in account. Keychain migration is an explicit recovery action; startup does not present permission prompts.

The model picker explains that tasks using “Model Harbor selection” follow the chosen Codex subscription, Grok or Baseten model on their next turn. The footer names the shared Model Harbor connection. Provider-specific task selections remain explicit; never claim an existing OpenAI task has switched just because the global selection changed.

Current subscription models appear first. Their caption explains that the account is the one signed in to Codex. The older saved-account controls explicitly identify the direct connection and its restart requirement, avoiding a false promise that account switching is live.
