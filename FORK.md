# Andrew's Codex Model Switcher fork

This fork retains hieunc229/CodexModelSwitcher's SwiftUI menu, provider editor, and Codex browser-login flow.

## Using the app

Open **Codex Model Switcher** in Applications, then click **Models** in the menu bar.

- Choose a saved Codex account or a provider model, then restart Codex to apply it. Existing tasks retain their original model/provider settings; use a new task after switching providers.
- The import menu beside Codex can import all accounts saved by Codex Switcher or save the current Codex account.
- The plus button runs Codex's browser sign-in to add another account. Complete the sign-in yourself, then select the saved account.
- Keep this app open while using Grok. Its local adapter stops when the app exits.

The app does not terminate Codex or interrupt active tasks automatically. Local builds are ad-hoc signed; macOS may request Keychain approval after a rebuild. Approve that prompt locally to load saved accounts.

## Credentials and configuration

Saved OAuth credentials and entered API keys use macOS Keychain. JSON metadata excludes tokens and API keys. The active auth.json remains the credential file consumed by Codex and is written with owner-only permissions.

New API providers read Keychain using an auth command. The app does not export keys into shell profiles, launchctl, or environment files. Linked Baseten and xAI providers preserve their existing authentication, including 1Password commands. Existing plaintext exports or backups made by older tools are not deleted automatically.

Account switching captures refreshed credentials from the active account before replacing auth.json. The switcher does not independently refresh OAuth tokens. Reimporting existing accounts does not overwrite credentials already saved in Keychain.

Config changes take a switcher lock, validate TOML with Python's standard parser, compare unrelated configuration semantically, check for intervening writes, and make private backups before replacing the file. Other applications do not honor the switcher lock, so avoid simultaneous switches in two account managers. Switching to Codex removes custom provider/catalog overrides and restores its native model list.

## Grok support

The adapter binds only to 127.0.0.1:48118. It forwards authenticated requests only to https://api.x.ai/v1/responses. It rejects browser-origin requests, does not store API keys, and does not log request headers or content. The app must remain open.

Each Codex tool namespace becomes one xAI function with an explicit tool-name enum and JSON argument string. This keeps all tools available while avoiding xAI's 350-tool limit. Responses and subsequent tool history are translated back to the original namespace/name. Text streaming is retained; grouped tool calls appear once their complete arguments arrive. Custom tools preserve their raw input.

Grok 4.20 uses its model's fixed reasoning behavior, so unsupported reasoning-effort settings are omitted. xAI web search uses its own live search service; Codex's cached-search flag is not available there. Opaque reasoning items are omitted on replay because xAI rejects Codex's replay format; conversation text, calls, and tool results are retained.

The old Chat Completions proxy, which silently discarded namespace tools, has been removed. Arbitrary Chat Completions-only providers are not supported by this build.

## Sign-ins versus API access

- Codex supports multiple accounts through its own browser OAuth login.
- Baseten uses API authentication, including existing 1Password commands.
- This build connects Grok using an xAI API key. Grok Build also supports browser OAuth and device login, but using that session as a Codex model provider has not been implemented or verified. See https://docs.x.ai/build/enterprise.
- OpenRouter is not included among the default linked providers.

## Verification on this Mac

- Eight Swift tests cover secret-free serialization, native-picker restoration, preservation of linked authentication, refusal to overwrite unowned providers, repeated Grok switches, malformed or unrelated config edits, and lock contention.
- Six Python tests cover all 446 fixture tools in a group, function-call/result replay, streamed namespace calls, custom tools, reasoning replay, and invalid dispatcher calls.
- The built app imported and switched among all three existing Codex accounts in an isolated directory and Keychain namespace. OpenAI accepted each selected account with HTTP 200. Active credential permissions were 0600 and metadata excluded tokens.
- Keychain create/read/update/delete checks passed using disposable values.
- The installed Codex CLI executed a Grok shell call. A separate live MCP namespace call returned its marker through Codex, and Grok produced the correct final reply after receiving the result.
- New browser sign-in remains an interactive flow. The verification used existing signed-in accounts rather than logging the user out or creating new sessions.

## Development

Requires macOS 13+, Xcode, and Homebrew Python 3.11+. Python 3.14 is required if the client sends zstd-compressed requests.

```sh
swift test
python3 -m unittest discover -s Tests -p 'test_*.py'
xcodebuild -project CodexModelSwitcher.xcodeproj -scheme CodexModelSwitcher \
  -configuration Debug -derivedDataPath build CODE_SIGNING_ALLOWED=NO build
```

MODEL_SWITCHER_CONFIG_DIR selects an isolated config directory. MODEL_SWITCHER_KEYCHAIN_SERVICE selects a test Keychain namespace. The --verify-accounts app argument requires both an isolated directory and a dev.napier.switcher.verification.* Keychain namespace; its report contains statuses, not credentials. It removes its test Keychain entries and auth.json afterward.

Do not commit auth files, account metadata, private configuration, or Keychain exports. This is a locally tested build, not a general security certification or a guarantee about every provider/tool combination.
