# Model Harbor

A community fork maintained by Andrew Napier, based on [hieunc229/CodexModelSwitcher](https://github.com/hieunc229/CodexModelSwitcher), retaining its SwiftUI menu, provider editor, and Codex browser-login flow.

## Use

Open **Model Harbor** in Applications or click **Harbor** in the menu bar.

- **Codex · Current subscription:** choose an OpenAI model in the live picker. Harbor imports visible models from Codex's official local catalog and uses the ChatGPT account currently signed in to Codex. No OpenAI API key is used.
- **Saved Codex accounts:** the separate direct-connection controls retain account import and browser sign-in; switching this direct connection still needs a restart.
- **Baseten:** direct inference using your existing provider authentication, including 1Password. No OpenRouter integration is added.
- **Grok:** your official Grok browser session. Models are fetched from that account. **Sign in** runs the installed official `grok login --oauth` flow; credentials remain in Grok's own private store. Model Harbor never receives your password.
- **Per-task model switching:** choose a named model in each Codex task's picker. The choice belongs to that task. Switching task A from Grok to Baseten leaves task B unchanged. Existing conversation history and tool results continue across models; an in-progress turn stays on its starting model.
- **New task default:** the menu in Harbor changes the default for future tasks. Existing tasks retain their model. Provider account sign-ins remain shared connections; this feature does not create a separate OAuth account per task.
- **One-time upgrade:** reopen Codex to load the new picker catalog. Live `model/list` and `config/batchWrite` with `reloadUserConfig` both retain the old catalog in the installed CLI. Once loaded, model changes within and between tasks need no restart. The hidden old `harbor-selected` entry is frozen to the pre-upgrade model, persisted as `legacyModel`, until the task chooses a named entry.
- A task created under a direct provider retains that provider. Use a task under `model-harbor` for cross-provider changes. Saved Codex OAuth account switching still requires a restart. Keep Model Harbor open.

## Authentication

Codex account credentials are consolidated in one macOS Keychain entry, under `dev.napier.ModelHarbor`. Metadata excludes credentials. The active Codex `auth.json` remains owner-only. Account switching captures credentials refreshed by Codex before replacing them.

Startup checks Keychain without displaying permission prompts. A legacy vault that cannot be read presents **Unlock saved accounts**. New builds must use the same Apple signing identity and bundle identifier. `scripts/build-app.sh` accepts a stable signing identity or reuses the installed app's identity. It fails if that identity is unavailable. `--ad-hoc` explicitly builds a local evaluation copy. This is a locally signed application, not a notarized public release.

The one-time `--migrate-vault` command can recover previously imported accounts from the existing Codex Switcher sessions, preferring the current Codex credential. It verifies every account with OpenAI before creating the new vault and never overwrites an existing vault. Old Keychain entries are retained.

Grok OAuth uses `https://cli-chat-proxy.grok.com/v1`. The installed official Grok client owns login and token refresh, including its refresh lock. Model Harbor reads the resulting session and runs `grok models` if refresh is needed. An expired or denied OAuth session fails without falling back to API billing. Available models and account permissions are controlled by xAI. See [Grok authentication](https://docs.x.ai/build/enterprise).

The stable provider is `model-harbor`. Each catalog entry has a route ID of `harbor/<provider>/<model>`, its own model metadata, and a provider label. The bridge validates the requested route against configured models; it never chooses a named model from a global selection or silently falls back. Codex owns task persistence. Turn IDs keep tool continuations on their original route. No credentials are stored in route metadata. The authenticated `/harbor/status` endpoint reports the most recent forwarded route and credential-cache state without prompt content.

Native subscription requests retain Codex's namespace and custom tools. Grok and Baseten use tool translation. Full replayed history preserves tool call/result links and removes incompatible provider-owned item IDs and opaque reasoning where needed.

The shared provider sets `requires_openai_auth = true`, using [Codex's documented proxy authentication](https://learn.chatgpt.com/docs/auth#alternative-model-providers). Codex owns subscription sign-in and refresh. For the subscription route, Harbor forwards the incoming access token and account ID only to `https://chatgpt.com/backend-api/codex/responses`. It rejects API keys and missing subscription credentials and never falls back to OpenAI API billing. A separate random `X-Model-Harbor-Token` header authenticates the local connection; it is stored in the owner-only config and is never forwarded upstream. Subscription credentials are not written by the bridge, exposed in metadata, or sent to Grok or Baseten.

The loopback adapter listens on `127.0.0.1:48118`. OAuth requests require a random owner-only local bridge credential; Codex never receives the Grok upstream OAuth token. The adapter rejects browser-origin requests and redirects and does not log tokens, headers, or request content. Baseten requests go directly to `https://inference.baseten.co/v1/responses` using the existing credential helper, including 1Password. Harbor calls that helper once per app session, retains the key only in memory, and shares it across turns, tool continuations, and simultaneous requests. Switching models or saving unrelated config does not unlock again. Failed, canceled, timed-out, or rejected credentials pause automatic unlocks. **Reconnect Baseten** explicitly clears the session credential and reads the current key; changing the configured helper also invalidates the cache. Quitting Harbor discards it. 1Password security settings are unchanged. They never receive the Grok OAuth credential. The old Grok OAuth and API routes remain fixed to Grok for existing tasks and are not silently retargeted.

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

The suite now includes 12 Swift checks and 54 Python checks. Additional Baseten checks cover shared queues, cached-token accounting, adaptive account limits, bounded overload retries, provider error/header preservation, cancellation, and refusal to replay partial streams. Routing tests cover independent tasks, turn pinning, restart recovery, frozen legacy routing, native/custom tool history, and credential separation.

The installed signed build passed ten live turns across three tasks and four models: GPT-6-Astra, Grok 4.6, Kimi K3, and DeepSeek V4.1 Flash. Every turn verified a new MCP tool marker, retained conversation context, and the actual upstream route. Tasks continued without model overrides and a resumed task retained its model. One Codex process handled the entire run with zero restarts. Baseten performed one credential-helper lookup and reused it for five subsequent requests. The new-task default control was also exercised in the installed UI; the legacy route remained frozen, and the original Kimi default was restored.

Run `python3 scripts/verify-live-switch.py --live --installed` to test the running app with an isolated Codex home and synthetic prompts. The test checks the actual picker catalog, alternates three tasks across Codex, Grok, Kimi and DeepSeek, changes models within a task, continues tasks without model overrides, resumes a stored task, and verifies a fresh MCP tool result plus conversation context on each turn. It also checks that the credential helper runs at most once and zero times if already unlocked. `--without-baseten` verifies subscription and Grok without requesting a Baseten unlock. Omit `--installed` to test a source bridge. It never prints credentials or changes the user's task history or model default.

After one successful reconnect, the installed credential-cache verification completed four turns (Kimi K3 twice, DeepSeek V4.1 Flash, then Kimi K3) in one Codex process and conversation. All four live tool calls, fresh markers, and recalled context passed. Eight upstream requests reused the cached credential with zero new helper lookups and zero restarts. The failed-unlock path was also checked in the installed app: three subsequent requests made zero additional helper calls.

`MODEL_SWITCHER_CONFIG_DIR` and `MODEL_SWITCHER_KEYCHAIN_SERVICE` isolate installation tests. The `--verify-accounts` mode requires both an isolated config directory and a `dev.napier.switcher.verification.*` Keychain namespace. Never commit credentials, account metadata, private config, or Keychain exports.

On September 16, 2026, the installed signed pacing build passed three synthetic live tool-call checks: Kimi K3 twice and DeepSeek V4.1 Flash once. It learned the 500,000/1,000,000 token-per-minute and 240/120 request-per-minute account limits from real provider response headers. One credential-helper read served all three calls, with two cache reuses and no Codex restart. No live overload occurred during these small probes; injected 429/529 tests separately verified the retry schedule and preserved cooldowns without deliberately exhausting the account quota.
