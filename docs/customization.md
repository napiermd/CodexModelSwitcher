# Customize Model Harbor

[← Model Harbor](../README.md)

Model Harbor is MIT licensed. Fork it, inspect the routes, change the interface, or contribute improvements back. Keep the license and upstream notices when redistributing.

## Customize the menu bar

Use **Settings → Menu bar** for display mode, icon visibility, provider visibility, usage refresh, and optional CodexBar history. Display modes include remaining quota, today's spend, and the next reset alongside connection, activity, model, count, name, and icon choices. At least one provider remains visible. Provider connection follows the selected Harbor tab. Activity and last requested model refer to real bridge traffic across all tasks, not the foreground Codex task.

## Customize startup and window behavior

Use **Settings → General** for System/Light/Dark appearance, Dock and menu-bar presence, the close dialog, and whether manual launches open a window. Enable **Launch at login** to register Harbor with macOS. The status beside it reflects the OS registration and approval state.

Warm-up is optional and off by default. Its editable prompt goes to the current Codex account as a real request that consumes quota. After-startup and daily modes have a once-per-day automatic limit. See [lifecycle and startup](lifecycle.md) for scheduling, closure, and explicit Codex restart behavior.

## Choose available models

Codex subscription models come from Codex's local catalog. Grok models come from the signed-in account. These lists reflect provider access; editing a label does not unlock a model.

Baseten reads `~/.codex/model-catalogs/baseten-frontier.json`. Start with [the example](../examples/baseten-models.json), change the `slug` to an actual model ID, and set its capabilities accurately. Restart Harbor to read changes and reopen Codex to reload newly added catalog entries. Switching among entries already loaded does not require restarting Codex.

The bundled [Baseten capability manifest](../ModelHarbor/Support/baseten-models.json) refreshes the five supported Baseten entries when Harbor starts; unknown custom entries are retained. Edit that manifest in source to change the built-in capabilities or fixed [agent roles](agent-teams.md).

Harbor generates `model-harbor.json`; edit the source provider catalog instead of that generated file. Route IDs must stay unique and stable if existing tasks are to retain their selection.

## Add another provider

Use **Add provider** for the supported connection flows. OpenRouter verifies a key and discovers tool-capable models from its live catalog; selected entries are saved to `~/.codex/model-catalogs/openrouter.json`.

The **Custom provider** editor retains the upstream direct-provider workflow. It expects a compatible Responses endpoint. Arbitrary Chat Completions providers are not supported. A custom direct provider does not automatically become a live Harbor route, and changing direct providers may require restarting Codex.

To extend live routing in source:

1. Register its presentation in `ProviderPresentation.swift` and its catalog discovery in `AppStore.swift`.
2. Add its stable ID and display label in `LiveRouting.swift`.
3. Add a fixed, validated destination and provider-specific credential handling in `grok_adapter.py`.
4. Test that credentials never cross provider boundaries, unknown models fail, and parallel tasks remain independent.
5. Verify text, streaming, tool calls/results, and conversation replay with that provider before claiming compatibility.

Do not turn the bridge into an arbitrary authenticated URL forwarder or reuse another provider's token. Keep login and API credentials separate. Account-provider terms still apply to any extension.

## Change the app or identity

Open `ModelHarbor.xcodeproj` in Xcode. `ContentView.swift` owns the native interface. The image-generation master is `assets/brand/harbor-icon-master.png`, with the exact prompt beside it and embedded in the image. `scripts/render-icon.swift` derives the shipping icon sizes from that master.

```sh
swift scripts/render-icon.swift
./scripts/build-app.sh --ad-hoc
```

The renderer updates the macOS icon sizes, app header mark, repository icon, and website PNG. Keep the master and its prompt together when changing the identity. Use your own bundle ID and signing identity for a separately distributed fork. Existing users of this project should retain their current identity to preserve Keychain trust.

## Change the website

Edit `site/index.html`, `site/styles.css`, and `site/demo.js`. The site uses self-hosted fonts and no analytics or external JavaScript. The interactive example is a browser-only demo; it never reads or changes the app.

```sh
python3 -m http.server 4173 --directory site
python3 scripts/check-site.py
```

Open `http://localhost:4173`. GitHub Pages deploys `site/` when changes reach `main`. If you fork this repo, replace the repository and Pages URLs before enabling the Pages workflow in your own settings.

## Build configuration

| Variable | Purpose |
| --- | --- |
| `MODEL_HARBOR_SIGNING_IDENTITY` | Stable local certificate name or SHA |
| `MODEL_HARBOR_BUILD_DIR` | Xcode derived-data destination, defaults to `build/` |
| `MODEL_SWITCHER_CONFIG_DIR` | Isolated Codex directory for development/tests |
| `MODEL_SWITCHER_KEYCHAIN_SERVICE` | Isolated Keychain service for tests |

The historical test variable names are retained for compatibility. Never point an automated test at your everyday Keychain namespace. See `InstallationVerification.swift` for its explicit isolation checks.
