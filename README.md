# Andrew’s fork

Account and provider switching with Keychain storage, existing 1Password authentication, and a Grok Responses adapter. See [FORK.md](FORK.md) for usage, verification, and limitations.

![Codex Model Switcher](/icon.png)

# Codex Model Switcher

A small macOS (13+) menu bar app for managing Codex model provider configurations.

The app helps you add, edit, and switch between Codex providers such as OpenAI, OpenRouter, DeepSeek, or other OpenAI-compatible services. It writes the selected model and provider settings to `~/.codex/config.toml`, stores app data in `~/.codex/model-switcher.json`, and can manage provider API keys and saved OpenAI account credentials.

![Screenshot](/screenshot.png)

## Features

- Add, edit, and delete custom model providers.
- Manage multiple models per provider.
- Switch the active Codex model from the menu bar.
- Manage multiple OpenAI accounts.
- Write Codex config updates automatically.
- Built-in compatibility proxy for Chat Completions providers (ie Deekseek).
- Keep the app menu-bar only, without a Dock icon.

## Notes

Codex may need to be restarted after changing provider, model, or OpenAI account settings.

API keys and OpenAI credentials are sensitive. Treat files under `~/.codex/` like secrets.

## Provider Status

Codex now uses OpenAI's Responses API for custom providers and deprecated `wire_api = "chat"`. For providers that only support Chat Completions, enable the built-in compatibility proxy to translate Codex `/responses` calls to `/chat/completions`.

## License

MIT License.
