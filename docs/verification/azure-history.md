# Azure encrypted-history provenance

Date: 2026-09-19. Branch: `fix/openrouter-parameters`.

## Incident

A task switched from a Codex subscription model to Azure `gpt-5.6-sol`. Harbor forwarded the earlier provider's encrypted reasoning item. Azure could not authenticate that ciphertext and rejected the request. The same invalid cross-provider history can also terminate a streamed request before completion.

The failure was not an Azure subscription limit, a context-window limit, or the removed 32 MiB Harbor request ceiling.

## Fix

Azure history now uses item-level provenance:

- Harbor records a SHA-256 digest for each encrypted item returned by an Azure deployment.
- The digest is scoped to the Azure resource endpoint and deployment that produced it.
- A later request to that exact binding retains the encrypted reasoning item unchanged.
- Encrypted reasoning from another provider, Azure resource, or Azure deployment is removed before dispatch.
- Portable messages, tool calls, tool results, and their `call_id` links remain in order.
- Unknown non-encrypted item types remain unchanged.

Harbor does not rewrite saved task history and does not retry a rejected or partially delivered request. A key rotation on the same Azure resource and deployment retains compatibility because the API key is not part of the history binding.

The independent gateway stores only 64-character binding and item digests in a private, bounded `opaque-history.sqlite` database. It never stores ciphertext, prompts, or response content. The registry survives gateway maintenance and remains separate from the version-1 ownership journal so the retained previous runtime can still roll back safely. Legacy runtimes use a bounded in-memory registry.

The first request after this upgrade conservatively drops encrypted items produced before provenance tracking began. Its portable conversation and tool history still continues. New Azure encrypted items are then tracked for subsequent same-deployment turns.

## Verification

- A direct cross-provider HTTP regression proves unknown encrypted reasoning and compaction are absent from the one Azure request while portable messages and tool links remain.
- A streaming HTTP regression proves the same filter runs before streamed dispatch and a normal terminal completion reaches the client.
- Endpoint and deployment isolation tests prove ciphertext registered under another Azure binding is still removed.
- JSON and SSE round-trip tests prove Azure-produced encrypted reasoning is retained unchanged in the next tool-result request.
- A known-ciphertext rejection still makes exactly one upstream request; Harbor does not hide provider errors with replay.
- Registry tests cover restart persistence, the 100,000-item bound, private file mode, and rejection of raw ciphertext values.

`scripts/verify-azure-history.py` performs two real Azure tool continuations, one JSON and one streaming. It then modifies the captured encrypted item and verifies that the unknown ciphertext is removed before one successful Azure request. The script emits aggregate evidence only and does not print credentials, prompts, or ciphertext.

## Integrated candidate results

- The full Python suite passed 561 tests.
- The Swift suite passed 108 tests with no failures.
- The model-picker suite passed with an empty temporary home, proving catalog tests do not depend on a developer machine's `~/.codex/models_cache.json`.
- Focused Azure-history, admission, deadline, gateway-runtime, provider-restore, and maintenance tests passed.
- The complete retained-entrypoint Bifrost composition case passed with the new unknown-history removal policy.
- `git diff --check` and Python bytecode compilation passed.

The feature branch includes current `main`; Git reports no unmerged paths or conflict markers. Installed-runtime evidence must be recorded only after a matching build, coordinated maintenance activation, and live verification.
