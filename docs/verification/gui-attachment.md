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

## Limits

The installed interface includes a newer gateway candidate, but the running service still uses retained runtime `5da659334bfd7bbd5af5456e9dd7761ea7b9c94f7e9310d447506900c42b3ad5`, from checkpoint `85040f6`. The Azure encrypted-history change has not been activated in that service. See [Azure history verification](azure-history.md).

This is evidence for replacing and reopening the interface. It does not prove automatic rolling gateway promotion, authoritative desktop turn completion, or safe retirement of an active gateway. Those remain separate requirements.
