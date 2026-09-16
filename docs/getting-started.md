# Get started

[← Model Harbor](../README.md)

Model Harbor is a source preview for macOS 13 or later. It is intended for people comfortable building an app and reviewing their local Codex configuration. No notarized installer is published yet.

## Prerequisites

- Xcode **16 or newer** with its command-line tools selected. Run `xcodebuild -version` to check.
- Homebrew Python **3.14** at `/opt/homebrew/bin/python3` or `/usr/local/bin/python3`. The bridge needs 3.11 for TOML and 3.14 for compressed Zstd requests.
- Codex installed and signed in with your ChatGPT account. Open it once before Harbor so its configuration and `models_cache.json` exist.
- For Grok, the official Grok CLI installed at `~/.local/bin/grok`, `/opt/homebrew/bin/grok`, or `/usr/local/bin/grok`, with OAuth access for your account. Follow [xAI's current setup documentation](https://docs.x.ai/build/enterprise).
- For Baseten, a Baseten API credential and the configuration below. If using 1Password, install its CLI and enable its desktop integration.

## Build

```sh
git clone https://github.com/napiermd/model-harbor.git
cd model-harbor
./scripts/build-app.sh --ad-hoc
open "build/Build/Products/Debug/Model Harbor.app"
```

`--ad-hoc` is suitable for local evaluation. For a stable installation, choose one of your own signing identities:

```sh
security find-identity -v -p codesigning
MODEL_HARBOR_SIGNING_IDENTITY="Apple Development: Your Name (YOUR_ID)" \
  ./scripts/build-app.sh
```

The script also reuses the signing identity of `/Applications/Model Harbor.app` when no identity is supplied. It fails clearly if that certificate is unavailable. It never creates certificates, alters Keychain permissions, or silently changes the signer. Keep the same bundle ID and signer across updates. You can copy the built app into Applications yourself after quitting an older Harbor instance. Run only one instance at a time.

## Connect

1. Open Harbor from the menu bar. The current Codex subscription's models load from Codex's local catalog.
2. Use **Sign in** under Grok and finish the official browser authorization. Your password stays with the provider. Confirm that Harbor displays the signed-in account and available models.
3. If using Baseten, complete the next section before reopening Harbor.
4. Choose a **New task default** in Harbor. This writes the stable `model-harbor` provider and catalog settings to your Codex configuration.
5. Reopen Codex **once** to load the new catalog. Start a task using Harbor and choose a named model in that task's picker.
6. Choose another model in another task. Return to the first task; its choice stays with it. Changing the new-task default does not change existing tasks.

A task created with a direct provider retains that provider. Create a Harbor task for cross-provider switching. You do not need a new task for every model change within Harbor. Separate saved Codex accounts are shared connection settings, not per-task identities; switching those accounts still requires restarting Codex.

## Configure Baseten

Harbor discovers Baseten from two files: a `model_providers.baseten` section in `~/.codex/config.toml` and a catalog at `~/.codex/model-catalogs/baseten-frontier.json`.

Review [the example provider](../examples/baseten-provider.toml) and add its section to your existing config. **Edit an existing Baseten section instead of adding a duplicate.** Replace the example helper path and 1Password item reference with your own values. The helper must print only the API credential.

Then install and review the example catalog:

```sh
mkdir -p ~/.codex/model-catalogs
cp examples/baseten-models.json ~/.codex/model-catalogs/baseten-frontier.json
```

The catalog contains example model IDs previously verified with this project. Confirm their availability and capabilities for your Baseten account. Open Harbor again to read the catalog, choose a default if necessary, then reopen Codex to load any new picker entries.

On the first Baseten request, approve the credential helper once. Harbor keeps the credential in process memory for that app session. A failed or canceled unlock pauses further attempts. Click **Reconnect Baseten** when ready; it deliberately requests the credential again. Quitting Harbor clears the in-memory credential.

An `env_key` may be used instead of a helper. A Finder-launched app does not normally inherit your shell's exported variables; use a helper for that setup. Never paste a real API key into an issue, example file, or Git commit.

## Troubleshooting

| What you see | What to check |
| --- | --- |
| No Harbor models in Codex | Choose a new-task default, then reopen Codex to load its catalog. |
| No subscription models | Sign in to Codex, run it once, and reopen Harbor so it can read the catalog. |
| Browser says signed in, but Harbor does not | Confirm the official Grok CLI can list your models. Account access is controlled by xAI. |
| Baseten is missing | Check both its provider section and the catalog's exact filename. |
| A canceled fingerprint prompt keeps the connection paused | Click Reconnect Baseten when you are ready to unlock. |
| macOS asks again for Keychain access after rebuilding | Check whether the signing identity changed. Use one stable certificate for ongoing use. |
| Local connection is unavailable | Keep Harbor running, check Python's version, and make sure another Harbor instance is not using port 48118. |

## Return to your previous setup

Quit Harbor before changing its config. Harbor creates private backups beside Codex configuration files. Review the backup you want to restore, or set your previous model/provider in Codex's config while preserving unrelated settings. Reopen Codex after removing the Harbor provider/catalog settings. Keep backups, account files, and Keychain entries until you have confirmed the old setup works. Deleting the app alone does not restore the config.
