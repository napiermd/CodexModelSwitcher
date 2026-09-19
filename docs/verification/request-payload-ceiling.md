# Request payload ceiling removal

Date: 2026-09-18. Branch: `codex/remove-request-payload-ceiling`.

## Fix

Commit `7da218f` removes the adapter's hard-coded 32 MiB request ceiling. Responses requests and native image requests no longer reject a body because of its encoded or decoded size, and zstd decompression no longer supplies a 32 MiB output cap. Required-body checks, exact body framing, local authorization, provider authorization, JSON validation, and content-encoding validation remain in place.

Commit `70436e0` updates coordinated maintenance to preserve all supported owned routes. Azure and OpenRouter are still live-probed. Codex subscription, Baseten, and Grok OAuth keep their exact route and account bindings without maintenance-time provider calls. Unknown providers still stop activation.

This was a Model Harbor adapter limit. It was not a paid-subscription limit.

## Local verification

- Full Python suite: 435 passed.
- Swift suite: 104 passed.
- Regression coverage includes an uncompressed Responses body larger than 32 MiB, a native image body larger than 32 MiB with exact-byte forwarding, and a small zstd request that expands beyond 32 MiB.
- `git diff --check` passed.
- The signed Xcode build passed with the existing Apple Development identity.
- The staged adapter exactly matched the committed source and contained none of the removed constant or rejection messages.

Signed stage:

- App: `build/staged-updates/request-payload-ceiling-20260918-v2/Model Harbor.app`
- Checkout recorded by the stage: `70436e00d139b00bb34cdc68faec7d9284feeae8`, clean
- Bundle inventory SHA-256: `ec066ab0784488b90fc8dd3232152c33a3d6623725b1fa80717d60c07e7a5159`
- Signature CDHash: `9eb4c007ef17c031a15bb2b34231af70d0f6ffb1`
- Team identifier: `U7FYRC56QD`
- Candidate runtime: `c18852e7f30443ae8e30411667b2b42d91c16cb7567c5b1d0df6087c61e9fc96`

## Installation and activation

The signed interface was installed at `/Applications/Model Harbor.app`. Its complete inventory and signature match the stage. The prior signed interface and installation record are retained at:

`~/Library/Application Support/Model Harbor/updates/request-payload-ceiling-20260918-ui-91d63cdd/`

Replacing and reopening only the interface preserved the existing independent gateway identity and configuration revision. The gateway was not terminated during interface installation.

Two maintenance attempts safely waited without pausing or replacing the live gateway because active transports did not drain inside their bounded windows:

- Transaction `1087627e-8843-4984-a587-88beb96488a4`: 180-second wait, phase `aborted`.
- Transaction `4c9fa820-3012-40ac-8329-90bd5eb14e53`: 600-second wait, phase `aborted`.

Both attempts completed isolated candidate preflight for Azure Sol and OpenRouter Fable and left the old runtime active.

Transaction `38507e8f-4478-4bd6-a1a2-32bb6eb12090` later reached a transport-free point and completed in phase `resumed`:

- Active runtime: `c18852e7f30443ae8e30411667b2b42d91c16cb7567c5b1d0df6087c61e9fc96`
- Boot: `fdae8511-70b7-4969-9d23-c2527949685e`
- Configuration revision preserved: `b94994faae2e17661639dc1f2b65aa3d67588ec705e9cbff9dbbf9d44a38a0a3`
- Azure Sol and OpenRouter Fable passed isolated preflight and current-boot restoration probes.
- Four captured local issuer processes were resumed; no captured issuer remained stopped.
- Maintenance admission blocking is false.
- Ownership is preserved on the new runtime for Azure, OpenRouter, Codex subscription, Baseten, and Grok OAuth.

## Live request larger than 32 MiB

A synthetic request was sent to the installed gateway at `/harbor/v1/responses` through `harbor/openrouter/anthropic/claude-fable-5.1` with a one-token output cap.

- Request body: 34,603,150 bytes
- Removed ceiling: 33,554,432 bytes
- Amount over the removed ceiling: 1,048,718 bytes
- Result: HTTP 200 in 15.732 seconds
- Provider response: model `anthropic/claude-fable-5.1`, with generated output and `incomplete_details.reason` equal to `max_output_tokens` because the probe intentionally capped output at one token
- Former local 413 messages: absent
- Runtime before and after: unchanged at `c18852e7f30443ae8e30411667b2b42d91c16cb7567c5b1d0df6087c61e9fc96`
- OpenRouter activity accounted for the request
- A fresh current-boot OpenRouter route verification then returned `verified: true`, leaving readiness true after the intentionally incomplete one-token probe

The HTTP 200 provider response with generated output proves the 34.6 MB body passed through the live Harbor adapter rather than being stopped by the former local 32 MiB check.
