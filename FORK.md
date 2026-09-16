# Model Harbor

A personal fork of [hieunc229/CodexModelSwitcher](https://github.com/hieunc229/CodexModelSwitcher), retaining its SwiftUI menu, provider editor, and Codex browser-login flow.

## Use

Open **Model Harbor** in Applications or click **Harbor** in the menu bar.

- **Codex · Current subscription:** choose an OpenAI model in the live picker. Harbor imports visible models from Codex's official local catalog and uses the ChatGPT account currently signed in to Codex. No OpenAI API key is used.
- **Saved Codex accounts:** the separate direct-connection controls retain account import and browser sign-in; switching this direct connection still needs a restart.
- **Baseten:** direct inference using your existing provider authentication, including 1Password. No OpenRouter integration is added.
- **Grok:** your official Grok browser session. Models are fetched from that account. **Sign in** runs the installed official `grok login --oauth` flow; credentials remain in Grok's own private store. Model Harbor never receives your password.
- **Live model switching:** select **Model Harbor selection** in a task using the Model Harbor provider. Codex subscription ↔ Grok ↔ Baseten and model changes then apply on the next turn, without restarting Codex. All tasks using this option follow the same Harbor selection. A turn already in progress, including tool continuations, stays on its original model.
- A task previously created under OpenAI or a direct provider keeps that provider. On first setup, create a task using the Model Harbor provider; if the new provider/catalog is not visible yet, reload Codex once. After upgrading the earlier bridge, start a new Model Harbor task so it loads subscription authentication; the running Codex process can stay open. Subsequent Codex/Grok/Baseten model switches need no reload. Native Codex OAuth account switching still requires restart. Keep Model Harbor open for all live routes.

## Authentication

Codex account credentials are consolidated in one macOS Keychain entry, under `dev.napier.ModelHarbor`. Metadata excludes credentials. The active Codex `auth.json` remains owner-only. Account switching captures credentials refreshed by Codex before replacing them.

Startup checks Keychain without displaying permission prompts. A legacy vault that cannot be read presents **Unlock saved accounts**. New builds must use the same Apple signing identity and bundle identifier. `scripts/build-app.sh` signs with Andrew's development certificate; it fails if that certificate is unavailable rather than silently using ad-hoc signing. This is a locally signed application, not a notarized public release.

The one-time `--migrate-vault` command can recover the three previously imported accounts from the existing Codex Switcher sessions, preferring the current Codex credential. It verifies every account with OpenAI before creating the new vault and never overwrites an existing vault. Old Keychain entries are retained.

Grok OAuth uses `https://cli-chat-proxy.grok.com/v1`. The installed official Grok client owns login and token refresh, including its refresh lock. Model Harbor reads the resulting session and runs `grok models` if refresh is needed. An expired or denied OAuth session fails without falling back to API billing. Available models and account permissions are controlled by xAI. See [Grok authentication](https://docs.x.ai/build/enterprise).

The shared provider is `model-harbor`, with the model alias `harbor-selected`. Its catalog uses the smallest configured context window across the supported providers. It reads the selected model from the existing atomic metadata file for each new turn. Codex turn IDs pin tool continuations to that model; requests without turn IDs take a per-request snapshot. No provider credentials are stored in the routing metadata. The authenticated `/harbor/status` endpoint reports the selected route and the most recent forwarded route, without prompt content.

The shared provider sets `requires_openai_auth = true`, using [Codex's documented proxy authentication](https://learn.chatgpt.com/docs/auth#alternative-model-providers). Codex owns subscription sign-in and refresh. For the subscription route, Harbor forwards the incoming access token and account ID only to `https://chatgpt.com/backend-api/codex/responses`. It rejects API keys and missing subscription credentials and never falls back to OpenAI API billing. A separate random `X-Model-Harbor-Token` header authenticates the local connection; it is stored in the owner-only config and is never forwarded upstream. Subscription credentials are not written by the bridge, exposed in metadata, or sent to Grok or Baseten.

The loopback adapter listens on `127.0.0.1:48118`. OAuth requests require a random owner-only local bridge credential; Codex never receives the Grok upstream OAuth token. The adapter rejects browser-origin requests and redirects and does not log tokens, headers, or request content. Baseten requests go directly to `https://inference.baseten.co/v1/responses` using the existing credential helper, including 1Password. They never receive the Grok OAuth credential. The old Grok OAuth and API routes remain fixed to Grok for existing tasks and are not silently retargeted.

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

On this Mac, eleven Swift tests and eighteen Python tests pass. All three Codex sessions authenticated with HTTP 200. Real Grok OAuth inference and a namespaced tool/result round trip through the installed Codex CLI passed. A changed, re-signed build read the existing consolidated Keychain vault without another approval. Browser sign-in uses the official client. A live five-turn test used one Codex app-server process and one conversation, switching Grok 4.6 → Kimi K3 → DeepSeek V4.1 Flash → Grok 4.5 → Grok 4.6. Each turn called a real MCP test tool and recalled prior conversation context, with zero restarts. The test also passed against the installed signed app while selections were changed through its actual menu. Run `python3 scripts/verify-live-switch.py --live` to repeat this opt-in test; it uses synthetic prompts and existing credentials in an isolated temporary configuration. Add `--subscription` to verify Astra on the current subscription → Grok → Kimi → Astra in one conversation. This uses the existing official authentication cache without printing credentials. Add `--verify-upgrade` with `--subscription` to start Codex with the previous provider config, update it without restarting, and verify subscription inference in a new task. Add `--installed` to exercise the running app; the verifier waits for each selection in Harbor and leaves the menu changes to the operator.

`MODEL_SWITCHER_CONFIG_DIR` and `MODEL_SWITCHER_KEYCHAIN_SERVICE` isolate installation tests. The `--verify-accounts` mode requires both an isolated config directory and a `dev.napier.switcher.verification.*` Keychain namespace. Never commit credentials, account metadata, private config, or Keychain exports.
