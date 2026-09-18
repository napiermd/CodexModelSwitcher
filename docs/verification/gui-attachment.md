# Interface update without changing the running gateway

On September 17, 2026, the signed Model Harbor interface was replaced in `/Applications/Model Harbor.app` while its independent gateway remained running. The signing authority was identical to the installed app. A verified copy and the original app were retained under the private application-support updates directory before replacement.

## Observed result

Authenticated checks before GUI quit, after replacement, and after reopening showed the same gateway boot ID and retained runtime digest. The gateway configuration revision was unchanged. SHA-256 comparisons of Codex configuration, authentication, the bridge token, Model Harbor metadata, and every model catalog found no changes. No Codex restart or gateway replacement occurred.

The reopened interface displayed `Bridge online` and the pending gateway-update warning. A fresh Azure verification initially returned unavailable. A later explicit check completed in 1.83 seconds, and the interface displayed `Azure Connected` and `Ready for the next request`. This verifies a stateless request after interface replacement. It does not establish uninterrupted Azure availability or encrypted-history continuation.

## Changes exercised

- Existing-gateway startup loads display metadata without Keychain hydration, credential synchronization, catalog installation, or default-route writes.
- Usage polling continues. Automatic inference warm-up runs only during fresh-service bootstrap.
- Explicit credential operations hydrate saved values before mutation. A failed hydration exposes the unlock action; unlocking while attached hydrates GUI memory without rewriting the live gateway.
- The bundled runtime digest is compared with the running digest. Health polling preserves the pending-update warning while they differ.
- Expired Azure/OpenRouter readiness retains connection-management controls and requests another check. Unchecked Grok sign-in is distinguished from a confirmed connection.

## Final compaction and review update

A later signed interface replacement on September 17 included the tool-free compaction fix and Azure automatic-review catalog hardening. The installed Python resources match the candidate source. Their inventory digest is `653756631b471200ce4fc94e3738ab20baa9defc5ff289e07347092afc07d647`.

The replacement passed 351 Python tests, 82 Swift tests, 10 packaged gateway lifecycle tests, and site checks. One completed-error test initially raced against client socket closure; the corrected fixture passed 30 repeated runs before the complete Python suite passed. It required no production-code change.

Before and after interface replacement and reopening, the gateway identity, configuration revision, and shared file hashes were unchanged. A final explicit Azure route verification passed in 2.21 seconds through the retained service.

After those preservation checks, the production `LiveRouting.catalog` generator regenerated the installed catalog. Structural comparison allowed exactly one metadata change: `harbor/azure/gpt-5.6-sol` now has `auto_review_model_override` equal to that same route. Every other catalog field and model route was preserved. A private rollback copy and installation record were retained locally. Codex was not restarted, so this does not establish that its already-loaded catalog has refreshed.

## Limits

The installed interface includes a newer gateway candidate, but the running service still uses retained runtime `5da659334bfd7bbd5af5456e9dd7761ea7b9c94f7e9310d447506900c42b3ad5`, from checkpoint `85040f6`. The Azure encrypted-history and tool-free compaction changes have not been activated in that service. See [Azure history verification](azure-history.md).

This is evidence for replacing and reopening the interface. It does not prove automatic rolling gateway promotion, authoritative desktop turn completion, or safe retirement of an active gateway. Those remain separate requirements.
