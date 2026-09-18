# Router hardening verification

Date: 2026-09-18. Branch: `codex/router-hardening`, based on `68fafc2`.

## Implemented behavior

- Catalog publication records source paths and SHA-256 digests. Azure automatic context records native per-field provenance, capture time and client version. Default and maximum remain distinct; manual limits retain their existing behavior.
- Authenticated gateway status reports native version mismatch, source drift, missing publication provenance and fallback model IDs. The backend reads the installed Codex version at startup. Loaded desktop state is reported as unknown.
- SSE heartbeats require a provider-issued response ID. The single downstream writer stops them at terminal events, preserves sequence numbers until insertion, and then maintains monotonic numbering. A bounded queue and consumption acknowledgement prevent the reader racing past terminal delivery. Azure deadlines and one-attempt dispatch remain unchanged.
- Named SSE events work when JSON omits type. Explicit empty completed output is rejected unless output already streamed. Tool-call completions remain valid.
- The last 32 failed requests are retained in memory for authenticated diagnostics. They record provider/model, failure class, duration and timestamp, never credentials or prompts. They reset when the gateway restarts.
- Native image POST endpoints preserve subscription/account headers, body bytes, content encoding, response status and image request IDs. Local and subscription authorization are both required. Runtime leases cover active image requests without changing text-model turn bindings. There is no automatic retry.

## Evidence

- Swift suite: 104 tests passed, including source-digest drift, fallback warnings and preserved distinct context limits.
- Signed Xcode build and strict code-signature verification passed using the existing Apple Development identity.
- Full Python suite: 430 tests passed. The first run exposed sequence-number rewriting before heartbeat insertion, a status-path subprocess interaction, and success fixtures with empty output. These were corrected before the passing run.
- Focused real HTTP heartbeat test receives an in-progress frame during upstream silence and the real tool-call completion afterward, with one upstream request.
- Azure deadline/EOF suite: 12 tests passed after reader synchronization. The existing regression verifies partial output retention and refusal to replay uncertain delivery.
- Image generation/edit loopback tests verify exact upstream path, unchanged bytes/account, local-secret exclusion, and authorization rejection.
- Isolated live image probes used `{}` on both endpoints. Both returned HTTP 400 from upstream instead of Harbor’s 404. No completed image generation or specific image-model entitlement is claimed.
- Native capture observed at `0.154.0`; installed Codex CLI observed at `0.155.0-alpha.9.2`.

## Activation boundary

The signed candidate is staged under `build/staged-updates/router-hardening-20260918/`. This patch has not replaced the live backend. Existing tasks, credentials, model selections and conversation files were not modified.

Per `AGENTS.md` and `docs/safe-updates.md`, activation requires explicit coordinated maintenance and the tested `scripts/gateway-maintenance.py` controller. A quiet request counter does not authorize replacement. After activation, verify the new runtime identity, provider readiness, a real response stream, and built-in image generation before claiming the live issue resolved.
