# Customize Model Harbor

[← Model Harbor](../README.md)

Model Harbor is MIT licensed. Fork it, inspect the routes, change the interface, or contribute improvements back. Keep the license and upstream notices when redistributing.

## Choose available models

Codex subscription models come from Codex's local catalog. Grok models come from the signed-in account. These lists reflect provider access; editing a label does not unlock a model.

Baseten reads `~/.codex/model-catalogs/baseten-frontier.json`. Start with [the example](../examples/baseten-models.json), change the `slug` to an actual model ID, and set its capabilities accurately. Restart Harbor to read changes and reopen Codex to reload newly added catalog entries. Switching among entries already loaded does not require restarting Codex.

The bundled [Baseten capability manifest](../ModelHarbor/Support/baseten-models.json) refreshes the five supported Baseten entries when Harbor starts; unknown custom entries are retained. Edit that manifest in source to change the built-in capabilities or fixed [agent roles](agent-teams.md).

Harbor generates `model-harbor.json`; edit the source provider catalog instead of that generated file. Route IDs must stay unique and stable if existing tasks are to retain their selection.

## Add another provider

The **+** provider editor retains the upstream direct-provider workflow. It expects a compatible Responses endpoint. Arbitrary Chat Completions providers are not supported. A custom direct provider does not automatically become a live Harbor route, and changing direct providers may require restarting Codex.

To extend live routing in source:

1. Define the provider and its catalog discovery in `AppStore.swift`.
2. Add its stable ID and display label in `LiveRouting.swift`.
3. Add a fixed, validated destination and provider-specific credential handling in `grok_adapter.py`.
4. Test that credentials never cross provider boundaries, unknown models fail, and parallel tasks remain independent.
5. Verify text, streaming, tool calls/results, and conversation replay with that provider before claiming compatibility.

Do not turn the bridge into an arbitrary authenticated URL forwarder or reuse another provider's token. Keep login and API credentials separate. Account-provider terms still apply to any extension.

## Change the app or identity

Open `ModelHarbor.xcodeproj` in Xcode. `ContentView.swift` owns the native interface. The icon is authored in `scripts/render-icon.swift`; its vector counterpart is `branding/mark.svg`.

```sh
swift scripts/render-icon.swift
./scripts/build-app.sh --ad-hoc
```

The renderer updates the macOS icon sizes, app header mark, repository icon, and website PNG. Keep the vector asset consistent when changing its geometry. Use your own bundle ID and signing identity for a separately distributed fork. Existing users of this project should retain their current identity to preserve Keychain trust.

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
