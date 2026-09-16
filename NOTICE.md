# Attribution

Model Harbor is maintained by **Andrew Napier** at [napiermd/model-harbor](https://github.com/napiermd/model-harbor).

## Upstream

Model Harbor began as a fork of [CodexModelSwitcher](https://github.com/hieunc229/CodexModelSwitcher) by **Hieu Nguyen (Jack)**. The original SwiftUI app, provider editor, configuration utilities, and Codex account flow are the foundation of this project. The Git history preserves that work.

The upstream [README at commit 3daf5c8](https://github.com/hieunc229/CodexModelSwitcher/blob/3daf5c889387c96aebf421db14d12493267f10ed/README.md#license) declares **MIT License**. This fork supplies the full MIT text in [LICENSE](LICENSE) and credits both upstream and Model Harbor contributors.

Model Harbor adds independent model routes per Codex task, a local bridge for the current Codex subscription and Grok OAuth, direct Baseten routing, session credential caching, credential isolation, tests, and its own branding and documentation.

## Brand and third-party names

The Model Harbor icon and website are original project assets, available under the MIT license. Codex, ChatGPT, OpenAI, Grok, xAI, Baseten, and 1Password are names of their respective owners. Model Harbor is an independent community project and is not endorsed by those companies. Provider access remains subject to each provider's account terms.

The website self-hosts Instrument Sans by the Instrument Sans Project Authors and Newsreader by the Newsreader Project Authors. Both fonts use the SIL Open Font License 1.1. Their notices are included beside the font files in `site/assets/fonts/`.

## Model Harbor icon and interface

The current navy/platinum monogram was produced with OpenAI image generation for Model Harbor. The master and exact generation prompt are in `assets/brand/`; shipping icon sizes are derived by `scripts/render-icon.swift`. The generation tool did not expose a selectable version number.

[CodexBar](https://github.com/steipete/CodexBar) informed the native provider inspector, settings separation, and menu-bar customization patterns. The original visual redesign did not copy CodexBar source, branding, or its provider-usage collectors. The subsequent usage integration is credited below.

## CodexBar usage interoperability

The account-usage integration references CodexBar's MIT-licensed Codex OAuth
usage reader, Grok credits billing parser, and widget snapshot format.
Copyright (c) 2026 Peter Steinberger. The complete license is retained in
[LICENSES/CodexBar-MIT.txt](LICENSES/CodexBar-MIT.txt).

CodexBar is an independent project: https://github.com/steipete/CodexBar.
Its cached local cost estimates are labeled as estimates and are never treated
as subscription invoices or attributed to an individual saved Harbor account.
