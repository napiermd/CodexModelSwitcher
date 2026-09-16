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
2. Select the Grok tab and use **Sign in to Grok** and finish the official browser authorization. Your password stays with the provider. Confirm that Harbor displays the signed-in account and available models.
3. If using Baseten, complete the next section before reopening Harbor.
4. Choose a **New task default** under **Settings → Advanced**. This writes the stable `model-harbor` provider and catalog settings to your Codex configuration.
5. Reopen Codex **once** to load the new catalog. Start a task using Harbor and choose a named model in that task's picker.
6. Choose another model in another task. Return to the first task; its choice stays with it. Changing the new-task default does not change existing tasks.

A task created with a direct provider retains that provider. Selecting a Harbor model in an older OpenAI task changes the model name but does not migrate its provider. Repair that task using the next section, or start a Harbor task. You do not need a new task for every model change within Harbor. Separate saved Codex accounts are shared connection settings, not per-task identities; switching those accounts still requires restarting Codex.

## Connect OpenRouter

1. Choose **Add provider → OpenRouter → Connect**.
2. Enter your OpenRouter API key and choose **Verify key & load models**. Harbor checks the key with OpenRouter before displaying its current tool-capable catalog.
3. Search and select the models you want in Codex, then choose **Save connection**. The API key is stored in macOS Keychain; the local bridge holds its working copy in memory.
4. Reopen Codex once to discover the newly added model names. Select an explicit OpenRouter model in a Harbor task.

Requests use your OpenRouter account and billing. Model availability, reasoning support, and inference compatibility depend on the selected model and provider. Catalog discovery is not a live inference check of every model. Harbor requests support for the parameters it sends and keeps the chosen model ID fixed.

Use **Manage connection → Choose models** to update the list, or **Disconnect** to remove the saved key while keeping model choices for reconnecting. An authentication rejection clears the bridge's working credential and changes its connection state.

## Read connection status

Select a provider tab for its connection, model list, requests in progress, and last completed response. **Bridge online** means the local process is available. **Connected** means the provider credential or session is ready; a provider can still return capacity or billing errors. Codex starts at **Configured** and becomes **Connected** after a completed response through Harbor. Completed-request counts reset when Harbor restarts.

The panel expands to fit content. It uses scrolling only when the available screen height is insufficient. Settings contains menu-bar text modes, appearance, visible providers, saved accounts, and advanced routing controls.

## Repair an older OpenAI task

If Codex reports **"The 'harbor/...' model is not supported when using Codex with a ChatGPT account,"** the task may still have its original `openai` provider. Both the provider and the model determine the connection. The picker changes the model only.

Model changes in an existing native task do not update its saved provider. Repeatedly selecting models or signing in again will not repair it.

Enable **Repair inactive task routes automatically** under **Settings → Advanced**. Harbor checks saved routes every ten seconds. It repairs only tasks saved under `openai` whose selected model is in your installed Harbor catalog. It preserves the task ID, selected model, title, and conversation. The setting survives Harbor restarts.

A loaded task holds Codex's writer lock, even between turns. Harbor waits for that lock rather than modifying a live task. To release an affected task sooner:

1. Let its work finish, then archive it in Codex. Archiving can also archive its spawned child tasks.
2. Wait for Harbor's pending-repair count to clear.
3. Restore the task and any child tasks you want visible. Continue using its existing conversation.

Codex can stay open during this repair. Once migrated, the task can switch among loaded Harbor models normally. Alternatively, quit Codex and let Harbor repair the saved tasks automatically before reopening it.

### Command-line repair

Preview every affected task without changing anything:

```sh
python3 scripts/repair-task-provider.py --all
```

Repair an unloaded task using Codex's own per-task writer lock:

```sh
python3 scripts/repair-task-provider.py TASK_ID --apply --unloaded
```

For versions without that lock namespace, quit every Codex/ChatGPT and Codex CLI process, then use the offline fallback:

```sh
python3 scripts/repair-task-provider.py --all --apply
```

The engine saves the complete original rollout and routing metadata under `~/.codex/model-harbor-task-backups/`. It verifies the conversation's SHA-256 digest, replaces only the provider in the rollout metadata, and updates the local task index. The private repair journal lets a subsequent automatic pass finish an interrupted file/index commit only when the exact expected states match. Conflicting state stops automatic repairs and retains the backup. **Retry repairs** retries after the underlying issue has been reviewed.

The private report is `~/.codex/model-harbor-repair-report.json`. Backups contain conversation history; do not attach them to public issues. No credentials or model requests are involved in repairs.

This changes Codex's local storage and uses its writer-lock protocol, tested with Codex CLI 0.150.1. It is not an official provider-migration API. Unknown history formats, inconsistent metadata, custom database locations, and unavailable lock protocols fail closed. The offline fallback remains available.

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
| Baseten returns 429 or 529 | Harbor now paces requests across tasks and waits before retrying. Cached tokens count toward the token limit. A local queue timeout is identified separately. Persistent upstream 429s may need a higher token allowance; persistent 529s mean provider capacity is unavailable. See [pacing and recovery](architecture.md#baseten-pacing-and-overload-recovery). |
| Baseten is missing | Check both its provider section and the catalog's exact filename. |
| A canceled fingerprint prompt keeps the connection paused | Click Reconnect Baseten when you are ready to unlock. |
| macOS asks again for Keychain access after rebuilding | Check whether the signing identity changed. Use one stable certificate for ongoing use. |
| Local connection is unavailable | Keep Harbor running, check Python's version, and make sure another Harbor instance is not using port 48118. |

## Return to your previous setup

Quit Harbor before changing its config. Harbor creates private backups beside Codex configuration files. Review the backup you want to restore, or set your previous model/provider in Codex's config while preserving unrelated settings. Reopen Codex after removing the Harbor provider/catalog settings. Keep backups, account files, and Keychain entries until you have confirmed the old setup works. Deleting the app alone does not restore the config.
