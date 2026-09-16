<p align="center"><img src="icon.png" width="104" alt="Model Harbor navy and platinum monogram"></p>
<h1 align="center">Model Harbor</h1>
<p align="center"><strong>Keep your tasks. Change your models.</strong></p>
<p align="center">A native macOS companion for Codex, maintained by Andrew Napier.</p>
<p align="center"><a href="https://napiermd.github.io/model-harbor/">Website & interactive demo</a> · <a href="docs/getting-started.md">Get started</a> · <a href="docs/architecture.md">How it works</a> · <a href="CONTRIBUTING.md">Contribute</a></p>
<p align="center"><a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-075154"></a> <img alt="macOS 13 or later" src="https://img.shields.io/badge/macOS-13%2B-075154"> <a href="https://github.com/napiermd/model-harbor/actions/workflows/ci.yml"><img alt="Checks" src="https://github.com/napiermd/model-harbor/actions/workflows/ci.yml/badge.svg"></a></p>

![An illustrated example of separate models staying with separate tasks](branding/overview.svg)

## Usage and spend

Track Codex account limits and resets, Grok subscription quota, Baseten
organization API spend, and OpenRouter key usage from **Usage & spend**.
Daily history and optional CodexBar token-value estimates keep their source
and account scope visible. Usage checks reuse existing credentials without
opening 1Password. See [Usage and spend](docs/USAGE.md) for coverage and setup.

## One model choice per task

Choose a model in **each Codex task's picker**. Model Harbor connects the current Codex subscription, Grok browser sign-in, Baseten, and OpenRouter through a local bridge. Task A can use Grok while task B keeps its Codex model. You can change a model inside an existing Harbor task and continue the conversation.

Harbor manages connections and a **new task default**. Each task keeps its own model. An in-progress turn stays on the model it started with. Keep Harbor running.

> **Source preview.** Build locally with Xcode. There is no notarized public installer yet. Reopen Codex once to load Harbor's model catalog. After that, model changes within Harbor tasks do not require a restart. Switching a saved Codex account through the separate direct connection still does.

Older OpenAI tasks that select a Harbor model can use [automatic task route repair](docs/getting-started.md#repair-an-older-openai-task). Harbor waits for Codex to release the task, preserves its conversation, and reports any repair that needs attention.

> **Older tasks:** A task created with the native OpenAI provider does not switch providers when you choose a Harbor model. If you see "model is not supported when using Codex with a ChatGPT account," use the [one-time task repair](docs/getting-started.md#repair-an-older-openai-task). It preserves the conversation and requires closing Codex once.

## Bring your connections

| Connection | Authentication | What Harbor does |
| --- | --- | --- |
| Current Codex subscription | ChatGPT sign-in managed by Codex | Uses the account already signed in to Codex and its available model catalog. |
| Grok | Official Grok CLI browser OAuth | Loads the account's models and uses the official client's session and refresh flow. |
| Baseten | Direct API credential or credential helper | Calls Baseten directly. A helper such as 1Password unlocks once per Harbor session. |
| OpenRouter | API key in macOS Keychain | Verifies your key, discovers tool-capable models, and routes selected models through your OpenRouter account. |
| Saved Codex accounts | Browser OAuth and macOS Keychain | Retains the separate account-switching workflow, which requires restarting Codex. |

Provider eligibility and model availability depend on your accounts. Grok OAuth is not a guarantee that every Grok subscription includes this access. Harbor does not provide provider credits. OpenRouter is optional and uses its own account billing.

## A useful menu bar

The provider inspector shows connection state, requests in progress, and the last completed response. Its height fits the selected provider; it scrolls only when the content exceeds the available screen.

Open **Settings → Menu bar** to choose provider connection, provider activity, last requested model, connected-provider count, Harbor name, or icon only. Customize appearance and visible providers there. Activity covers Harbor requests across tasks; it does not identify the task currently in the foreground. Codex shows **Configured** until Harbor verifies a completed response.

Use **Add provider → OpenRouter** to verify a key and choose models from the live tool-capable catalog. Other Responses-compatible endpoints remain available through **Custom provider**. New catalog entries require reopening Codex once; switching among already loaded Harbor models does not.

## Mixed-model coding teams

Use Kimi K3 for planning, with GLM 5.3, DeepSeek V4 Pro 0813, and Kimi K2.7 Code in separate coding worktrees. The bundled launcher pins each worker’s model and verified reasoning setting, then returns results to your coordinating task. Codex can stay open.

See [agent teams](docs/agent-teams.md) for reusable roles, installation, concurrent workers, review, and the distinction between native subagents and worktree isolation.

## Build and run

Install Xcode 16+ and Homebrew Python 3.14. Sign in to Codex and run it once to populate its local configuration and model catalog.

```sh
git clone https://github.com/napiermd/model-harbor.git
cd model-harbor
./scripts/build-app.sh --ad-hoc
open "build/Build/Products/Debug/Model Harbor.app"
```

This builds a local evaluation app. For ongoing use with saved accounts, use a **stable signing certificate** instead of `--ad-hoc`. Changing a signature can trigger Keychain approval again. The build script can reuse the identity of an installed Model Harbor app, or you can set `MODEL_HARBOR_SIGNING_IDENTITY`. It does not install over your existing app or change your signing certificates.

Follow [the setup guide](docs/getting-started.md) to connect providers, configure Baseten, load the Codex picker, and recover from a canceled unlock.

## Read, change, verify

The app is SwiftUI. The local Responses bridge is Python, with no Python package dependencies. The public website is plain HTML, CSS, and JavaScript. There is no Model Harbor cloud service in the request path.

- [Architecture and data flow](docs/architecture.md): where a request goes, where credentials live, and how tasks retain their models.
- [Customization](docs/customization.md): edit model catalogs, add a direct provider, change the UI, or extend the bridge.
- [Verification and known limits](FORK.md): automated checks and the scope of live testing.
- [Security policy](SECURITY.md): report privately and keep credentials out of issues.
- [Contributing](CONTRIBUTING.md): build, test, and submit a focused change.
- [Roadmap](ROADMAP.md): public packaging, onboarding, and compatibility work.

```sh
swift test
python3 -m unittest discover -s Tests -p 'test_*.py'
python3 scripts/check-site.py
```

Live verification is opt-in, uses your provider accounts, and may incur provider usage. It is not part of public CI.

## Ownership and license

Model Harbor is **Andrew Napier's** community fork of [CodexModelSwitcher](https://github.com/hieunc229/CodexModelSwitcher), created by **Hieu Nguyen (Jack)**. The upstream app and Git history remain credited. Model Harbor adds its own routing, authentication protections, tests, visual identity, and documentation.

[MIT licensed](LICENSE). You can use, modify, fork, and redistribute the software under that license. See [NOTICE.md](NOTICE.md) for upstream provenance, font licenses, and third-party names. Model Harbor is independent of OpenAI, xAI, Baseten, OpenRouter, and 1Password.
