# Azure encrypted-history provenance

Date: 2026-09-18. Branch: `codex/cross-provider-encrypted-history`.

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

## Local candidate results

- The full Python suite passed 439 tests with `ResourceWarning` promoted to errors.
- The Swift suite passed 104 tests with no failures.
- The focused adapter, Azure-history, and gateway-runtime run passed 82 tests.
- The complete retained-entrypoint Bifrost composition case passed with the new unknown-history removal policy.
- `git diff --check` and Python bytecode compilation passed.

## Installed-runtime results

Commit `aa5e63fda247a8223b5b50bd0b78a57277a1237d` was built as a signed app and staged at `build/staged-updates/cross-provider-encrypted-history-20260918`.

- The stage contains 31 entries and has inventory SHA-256 `3565bf84bf6fe396033c9c5d228f75cf457bff77c27a26508f54ca6f7baf0925`.
- The signature verified with team identifier `U7FYRC56QD` and CDHash `0369b5bb822d70a0857e0c273f550fa0a13cb309`.
- The installed interface exactly matched that staged inventory. Its installation record is `~/Library/Application Support/Model Harbor/updates/cross-provider-encrypted-history-20260918-ui-09828c92/installation.json`.
- The interface update preserved the existing independent gateway, boot identity, and configuration. Its rollback app remains in the private installation-record directory.

The coordinated maintenance transaction at `~/Library/Application Support/Model Harbor/updates/gateway-6680eb9c-db6a-48cf-9f22-0b34c28d7448/maintenance.json` completed with phase `resumed` after active tasks reached a transport-free instant. The controller paused and resumed three local issuer processes. It preserved turn ownership and shared configuration, and it verified Azure and OpenRouter before resuming them.

- Active runtime: `37c5a7c595b84fbfaaddecd2ef186393a03f54d55f9c4564e6b0e2b1af4fbc7b`
- Boot ID: `66d88129-0159-47b8-89e1-ec12c8060142`
- Configuration revision: `b94994faae2e17661639dc1f2b65aa3d67588ec705e9cbff9dbbf9d44a38a0a3`
- Maintenance gate: clear
- Azure `gpt-5.6-sol`: ready and verified
- OpenRouter `anthropic/claude-fable-5.1`: ready and verified

`scripts/verify-azure-history.py --live --installed --deployment gpt-5.6-sol --effort xhigh` then sent five requests through the installed gateway using unique synthetic task and turn identifiers. The JSON and streamed tool continuations both returned the expected marker with same-binding encrypted reasoning retained. A continuation containing modified, unknown ciphertext completed with HTTP 200 after Harbor removed the foreign encrypted item. No credential, prompt, ciphertext, or response content was printed.

After the live proof, the private `opaque-history.sqlite` registry contained 20 rows. Its mode was `0600`, schema version was 1, and every binding and item value was a 64-character lowercase hexadecimal digest.
