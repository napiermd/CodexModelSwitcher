# Model Harbor

A personal fork of [hieunc229/CodexModelSwitcher](https://github.com/hieunc229/CodexModelSwitcher), retaining its SwiftUI menu, provider editor, and Codex browser-login flow.

## Use

Open **Model Harbor** in Applications or click **Harbor** in the menu bar.

- **Codex:** choose one of your saved OAuth accounts. The add-account button opens Codex's browser sign-in.
- **Baseten:** direct inference using your existing provider authentication, including 1Password. No OpenRouter integration is added.
- **Grok:** your official Grok browser session. Models are fetched from that account. **Sign in** runs the installed official `grok login --oauth` flow; credentials remain in Grok's own private store. Model Harbor never receives your password.
- After selecting an account or model, restart Codex and start a new task. Existing tasks keep their original settings. Keep Model Harbor open for Grok.

## Authentication

Codex account credentials are consolidated in one macOS Keychain entry, under `dev.napier.ModelHarbor`. Metadata excludes credentials. The active Codex `auth.json` remains owner-only. Account switching captures credentials refreshed by Codex before replacing them.

Startup checks Keychain without displaying permission prompts. A legacy vault that cannot be read presents **Unlock saved accounts**. New builds must use the same Apple signing identity and bundle identifier. `scripts/build-app.sh` signs with Andrew's development certificate; it fails if that certificate is unavailable rather than silently using ad-hoc signing. This is a locally signed application, not a notarized public release.

The one-time `--migrate-vault` command can recover the three previously imported accounts from the existing Codex Switcher sessions, preferring the current Codex credential. It verifies every account with OpenAI before creating the new vault and never overwrites an existing vault. Old Keychain entries are retained.

Grok OAuth uses `https://cli-chat-proxy.grok.com/v1`. The installed official Grok client owns login and token refresh, including its refresh lock. Model Harbor reads the resulting session and runs `grok models` if refresh is needed. An expired or denied OAuth session fails without falling back to API billing. Available models and account permissions are controlled by xAI. See [Grok authentication](https://docs.x.ai/build/enterprise).

The loopback adapter listens on `127.0.0.1:48118`. OAuth requests require a random owner-only local bridge credential; Codex never receives the upstream OAuth token. The adapter rejects browser-origin requests and redirects and does not log tokens, headers, or request content. The legacy API route remains for existing configurations but is not offered in Model Harbor's provider list.

## Tool compatibility

Each Codex namespace becomes one function with a tool-name enum and argument string, avoiding xAI's function-count limit while preserving all tools. Calls, results, custom input, and streaming are translated back to Codex. Opaque reasoning items are omitted during replay. Grok 4.20's unsupported reasoning controls are stripped on the legacy API route. xAI web search does not support Codex's cached-search flag.

Config changes use a lock, validate TOML and unrelated settings, check intervening writes, create private backups, and replace files atomically. Other account managers do not share this lock. Arbitrary Chat Completions providers are not supported.

## Development and verification

Requires macOS 13+, Xcode, Homebrew Python 3.11+, and the official Grok CLI for OAuth. Zstd requests require Python 3.14.

```sh
swift test
python3 -m unittest discover -s Tests -p 'test_*.py'
scripts/build-app.sh
```

The tests cover credential-free metadata, provider preservation, native model restoration, configuration locking, namespace translation, local OAuth credentials, official-client refresh, and refusal to fall back to API keys.

On this Mac, nine Swift tests and ten Python tests pass. All three Codex sessions authenticated with HTTP 200. Real Grok OAuth inference and a namespaced tool/result round trip through the installed Codex CLI passed. A changed, re-signed build read the existing consolidated Keychain vault without another approval. Browser sign-in uses the official client; the live OAuth tests reuse the existing signed-in session.

`MODEL_SWITCHER_CONFIG_DIR` and `MODEL_SWITCHER_KEYCHAIN_SERVICE` isolate installation tests. The `--verify-accounts` mode requires both an isolated config directory and a `dev.napier.switcher.verification.*` Keychain namespace. Never commit credentials, account metadata, private config, or Keychain exports.
