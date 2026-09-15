# Andrew's Codex Model Switcher fork

This is a fork of hieunc229/CodexModelSwitcher. The existing SwiftUI menu, provider editor, and Codex browser-login flow remain the foundation.

## What this branch changes

- Saved OAuth credentials and entered API keys use macOS Keychain. The JSON metadata encoder excludes secrets, including when called outside the persistence code.
- Existing JSON credentials migrate only after loading succeeds. A Keychain error stops loading instead of replacing saved accounts with defaults.
- New API providers use an auth command that reads Keychain. The app no longer writes API keys to shell profiles, launchctl, environment files, or bearer-token config fields.
- Linked Baseten, OpenRouter, and xAI entries use existing provider definitions and catalogs. Existing 1Password authentication stays intact.
- The Codex entry restores its native picker instead of a hard-coded model list. Provider switches preserve the global reasoning preference; explicit reasoning edits still work.
- Configuration changes create private backups. New config and auth files are written atomically with owner-only permissions.
- Adding an account uses Codex's own browser login. A separate action saves the current Codex account. Selecting an account restores its credentials and the native provider.
- The app captures refreshed credentials from the active auth file before account switches. It no longer rotates refresh tokens independently just to display account status.
- The installed ChatGPT.app Codex executable is supported alongside Codex.app.

## Current limits

This is a development branch, not an installed or fully verified release.

The five automated tests exercise config rewriting and secret-free serialization. The Xcode app builds. Live browser login, Keychain prompts, and switching among real accounts have not been tested in this fork. Concurrent config writes and account switches while Codex is running still need integration testing. Do not describe this as a completed security audit.

Grok is present as a linked provider but disabled in the UI. A live probe of the installed Codex CLI and xAI Responses endpoint returned HTTP 422 because xAI rejected a namespace tool. Disabling multi-agent support did not resolve it; the namespaces also contain MCP tools. The upstream chat compatibility proxy silently drops those tools, so this branch disables that proxy pending a verified adapter. No working Grok-in-Codex claim is made.

The current Codex auth.json remains the file consumed by Codex. Keychain protects the switcher's saved copies; this does not mean there are no credentials on disk. Existing plaintext exports or historical backups created by other tools are not automatically deleted.

Only provider definitions already present in config.toml are automatically linked. Imported provider authentication is configured externally; the editor does not replace it. Catalog model IDs come from those local catalogs and have not all been probed for current availability.

## Sign-in support

- Multiple Codex accounts: Codex's own browser OAuth login, with saved copies in Keychain. Each account still requires the user to complete sign-in.
- Baseten: existing API authentication, including 1Password auth commands. A console sign-in is not an inference credential.
- Grok: xAI API authentication is separate from the Grok subscription. Using a Grok account to sign into the xAI console does not transfer subscription usage to the API. See https://docs.x.ai/console/faq/accounts.
- Claude: the linked entry uses OpenRouter API access. Claude subscription credentials are intended for native Anthropic applications; using the unmodified Claude Code binary is a separate integration from replacing Codex's model. See https://code.claude.com/docs/en/legal-and-compliance.

## Verification

```sh
swift test
xcodebuild -project CodexModelSwitcher.xcodeproj -scheme CodexModelSwitcher \
  -configuration Debug -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
```

Set MODEL_SWITCHER_CONFIG_DIR to an isolated fixture directory when testing the app. Do not use live accounts in automated fixtures. Do not commit configuration, auth files, Keychain exports, or real account metadata.

## Remaining work before installation

1. Exercise Keychain failure, migration, save, deletion, and recovery with disposable credentials.
2. Test real Codex login and refreshed-token switching with the user's participation, including interaction with the existing Codex Switcher.
3. Add config concurrency protection and full TOML validation; the inherited line-based rewrite is not a general TOML parser.
4. Implement and verify Grok namespace translation, streamed tool calls, and tool-result round trips without silently removing tools.
5. Test the menu against real account and provider states, then package and sign a local build.
