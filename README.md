# Model Harbor

![Model Harbor](icon.png)

A macOS menu-bar app for switching Codex accounts and model providers, forked from [hieunc229/CodexModelSwitcher](https://github.com/hieunc229/CodexModelSwitcher).

- Your current Codex subscription in the live model picker.
- Saved Codex accounts using browser OAuth for the separate direct connection.
- Direct Baseten inference with existing authentication.
- Grok browser sign-in, account-specific model discovery, and a local Responses adapter.
- A signed app and consolidated Keychain storage for Codex accounts.

Open **Model Harbor** in Applications, or click **Harbor** in the menu bar. Choose a model under **Codex · Current subscription**, Grok, or Baseten here, then choose **Model Harbor selection** in Codex. After that, changes in Harbor take effect on your next turn without restarting Codex. Keep Model Harbor open. The **Current subscription** section uses the ChatGPT account signed in to Codex. The separate saved-account/direct-connection controls still require restarting Codex. When upgrading from the earlier Grok/Baseten-only bridge, start a new Model Harbor task so it loads subscription authentication. Codex can stay open.

See [FORK.md](FORK.md) for authentication, installation, verification, and limitations. Build with `scripts/build-app.sh`; the signing identity can be set through `MODEL_HARBOR_SIGNING_IDENTITY` and must stay consistent across updates.

The upstream SwiftUI interface, provider editor, and Codex login flow are retained. See [LICENSE](LICENSE) for the original license.
