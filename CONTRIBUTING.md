# Contribute to Model Harbor

Thanks for helping improve Model Harbor. Andrew Napier maintains this community fork. Small changes with clear evidence are the easiest to review.

## Start with a focused problem

Check [existing issues](https://github.com/napiermd/model-harbor/issues) before opening a new one. Describe the task you are trying to complete and what happens instead. For large provider integrations, discuss the intended authentication and routing behavior in an issue first.

Bug reports should include macOS, Xcode/Python, Codex and Grok CLI versions where relevant, the Harbor commit, and reproducible steps. Remove credentials, account emails, prompts, and private paths from screenshots and logs.

## Local workflow

```sh
git clone https://github.com/napiermd/model-harbor.git
cd model-harbor
git switch -c my-change
swift test
python3 -m unittest discover -s Tests -p 'test_*.py'
python3 scripts/check-site.py
./scripts/build-app.sh --ad-hoc
```

Use Xcode 16+ and Homebrew Python 3.14 on macOS. No paid provider account is needed for the local test suites or CI. The app build requires macOS. Linux can run Python and website checks.

Test behavioral changes at their boundary: independent tasks, config preservation, credential routing, cancellation, or tool results. Do not commit your `.codex` directory, account metadata, generated credentials, Keychain exports, or local build outputs. Use isolated test directories and the explicit test-only Keychain namespace when exercising account operations.

For UI changes, check light/dark macOS appearance and keyboard access. Use injected login, process, and request services for lifecycle tests. A fixture pass does not establish real reboot startup, account entitlement, or warm-up success; report live checks separately. Do not register login items or terminate a developer's active Codex session as part of an automated test. For website changes, check desktop and narrow screens, keyboard focus, and reduced motion. The demo must stay clearly labeled and must not make provider API requests.

## Offline task-repair verification

With the Codex CLI on PATH, run `python3 scripts/verify-task-repair.py`. It creates a synthetic task with a mismatched provider in a temporary Codex home and demonstrates both inference and remote-compaction failures against a local mock endpoint. It archives the task to release its writer lock, repairs the saved provider, and resumes the same task without restarting Codex. It verifies paginated conversation bytes survive repair, then checks that compaction uses Harbor's normal Responses route with the same model and that another turn completes. It uses synthetic keys and makes no real inference requests. Verified with Codex CLI 0.150.1; protocol changes may require updating this optional check.

## Installing updates

Read [Protect tasks during Harbor updates](docs/safe-updates.md) before touching a running installation. Builds stay staged during active work; zero active HTTP requests does not mean a task has finished. The app currently owns the shared gateway, so an app restart affects routed tasks.

After building, run `python3 scripts/stage-update.py --app "build/Build/Products/Debug/Model Harbor.app"` to create a verified copy and manifest under `build/staged-updates/`. This does not launch or install it. The manifest identifies the copied bytes and labels the checkout revision as contextual, not verified build provenance. See the [audit and execution plan](docs/update-safety-audit.md) for remaining runtime work.

## Live verification

`python3 scripts/verify-live-switch.py --live --installed` explicitly opts into provider usage with synthetic tasks and the running installed app. It requires configured accounts and may request one Baseten unlock if the session is not already unlocked. `--without-baseten` avoids requesting a Baseten credential. Omit `--installed` to exercise the source bridge.

Do not run live account verification in a pull-request workflow. Report the exact models, checks, and limitations you verified. An automated test pass does not establish provider entitlement or every tool's compatibility.

`python3 scripts/verify-openrouter-tools.py --live` exercises a synthetic Fable 5.1 tool call and streamed tool-result continuation through the source bridge using the saved OpenRouter credential. Add `--installed` to test the running gateway. Both consume OpenRouter credit; neither reads or resumes real tasks. The check includes Codex's `tool_choice: auto` and `parallel_tool_calls: false`, which a text-only connection probe would miss.

## Pull requests

Explain the problem, the resulting behavior, and how you checked it. Include screenshots for visual changes and update docs when behavior changes. Keep unrelated refactors separate. Do not remove the upstream authorship notices.

Contributions are made under the project's [MIT license](LICENSE). Respect other contributors, assume good intent, and keep technical disagreement about the work. Maintainers may remove abusive or off-topic content. Report credential or security issues through [the security policy](SECURITY.md), not a public issue.
