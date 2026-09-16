# Model Harbor

![Model Harbor](icon.png)

A macOS menu-bar app for switching Codex accounts and model providers, forked from [hieunc229/CodexModelSwitcher](https://github.com/hieunc229/CodexModelSwitcher).

- Your current Codex subscription in the live model picker.
- Saved Codex accounts using browser OAuth for the separate direct connection.
- Direct Baseten inference with existing authentication.
- Grok browser sign-in, account-specific model discovery, and a local Responses adapter.
- A signed app and consolidated Keychain storage for Codex accounts.

Open **Model Harbor** in Applications, or click **Harbor** in the menu bar. Harbor manages connections. Choose a named model in **each Codex task's model picker**. Each task remembers its own model; changing task A does not change task B. You can change models in an existing Harbor task and keep its conversation, without restarting Codex. Keep Harbor open.

**Upgrading from the shared picker:** reopen Codex once to load the new entries. Its model catalog is cached at startup. The old “Model Harbor selection” entry stays fixed to its pre-upgrade model until you choose a named entry. Harbor's **New task default** affects future tasks only. The current subscription uses the account signed in to Codex; saved-account/direct-connection switching still requires a restart.

See [FORK.md](FORK.md) for authentication, installation, verification, and limitations. Build with `scripts/build-app.sh`; the signing identity can be set through `MODEL_HARBOR_SIGNING_IDENTITY` and must stay consistent across updates.

The upstream SwiftUI interface, provider editor, and Codex login flow are retained. See [LICENSE](LICENSE) for the original license.
