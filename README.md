# Model Harbor

![Model Harbor](icon.png)

A macOS menu-bar app for switching Codex accounts and model providers, forked from [hieunc229/CodexModelSwitcher](https://github.com/hieunc229/CodexModelSwitcher).

- Multiple Codex accounts using browser OAuth.
- Direct Baseten inference with existing authentication.
- Grok browser sign-in, account-specific model discovery, and a local Responses adapter.
- A signed app and consolidated Keychain storage for Codex accounts.

Open **Model Harbor** in Applications, or click **Harbor** in the menu bar. Choose an account or model, then restart Codex and start a new task. Keep Model Harbor open when using Grok.

See [FORK.md](FORK.md) for authentication, installation, verification, and limitations. Build with `scripts/build-app.sh`; the signing identity can be set through `MODEL_HARBOR_SIGNING_IDENTITY` and must stay consistent across updates.

The upstream SwiftUI interface, provider editor, and Codex login flow are retained. See [LICENSE](LICENSE) for the original license.
