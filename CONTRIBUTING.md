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

For UI changes, check light/dark macOS appearance and keyboard access. For website changes, check desktop and narrow screens, keyboard focus, and reduced motion. The demo must stay clearly labeled and must not make provider API requests.

## Live verification

`python3 scripts/verify-live-switch.py --live --installed` explicitly opts into provider usage with synthetic tasks and the running installed app. It requires configured accounts and may request one Baseten unlock if the session is not already unlocked. `--without-baseten` avoids requesting a Baseten credential. Omit `--installed` to exercise the source bridge.

Do not run live account verification in a pull-request workflow. Report the exact models, checks, and limitations you verified. An automated test pass does not establish provider entitlement or every tool's compatibility.

## Pull requests

Explain the problem, the resulting behavior, and how you checked it. Include screenshots for visual changes and update docs when behavior changes. Keep unrelated refactors separate. Do not remove the upstream authorship notices.

Contributions are made under the project's [MIT license](LICENSE). Respect other contributors, assume good intent, and keep technical disagreement about the work. Maintainers may remove abusive or off-topic content. Report credential or security issues through [the security policy](SECURITY.md), not a public issue.
