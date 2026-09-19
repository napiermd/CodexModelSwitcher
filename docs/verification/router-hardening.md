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

The signed candidate is staged under `build/staged-updates/router-hardening-20260918/`. The backend and matching signed interface were activated after Andrew explicitly approved coordinated maintenance. Existing credentials, model selections and conversation files were not modified.

Per `AGENTS.md` and `docs/safe-updates.md`, activation requires explicit coordinated maintenance and the tested `scripts/gateway-maintenance.py` controller. A quiet request counter does not authorize replacement. After activation, verify the new runtime identity, provider readiness, a real response stream, and built-in image generation before claiming the live issue resolved.


## Installed activation

- Commit: `679c35f`.
- Active runtime: `c40778835d0f26fc46e91a4deb66e7a4576a29b16017fc5f0ce733cb5d2184bd`.
- Boot: `c3313230-10c2-4d0c-9f43-fdf5c5f4ed22`.
- Coordinated transaction `2797773b-d2fa-48ea-8f82-9f585a1d3bd9` ended in `resumed`, preserving ownership and shared configuration. Pause through final record was approximately 6.59 seconds. All inspected local Codex processes resumed.
- An earlier attempt `90ef93cc-29d6-4fc8-aa8f-aef71dee9a27` safely aborted when a request raced the pause. It made no service replacement.
- Authenticated status confirms the new identity, Azure and OpenRouter readiness true, and no remaining maintenance admission gate.
- `scripts/verify-openrouter-tools.py --live --installed` passed actual tool selection and a streamed tool-result continuation on Fable 5.1.
- Subsequent activity snapshot recorded eight successful Azure requests, six Codex-subscription requests, two OpenRouter requests and no failures since activation.
- `/Applications/Model Harbor.app` matches the staged manifest. Signature and inventory were reverified. Updating the interface preserved gateway boot identity and configuration at the check.
- Previous interface retained under `~/Library/Application Support/Model Harbor/updates/router-hardening-20260918-ui/Model Harbor.app`. The maintenance transaction retains the previous backend and recovery information.
- Freshness diagnostics correctly report the existing native client-version mismatch and old published catalog without provenance. New provenance is written when Harbor next publishes its catalog. The desktop's loaded catalog remains unknown.
- Task `01a0b592-189e-7be3-8813-c33d4601de65` reported successful built-in `image_gen__imagegen` generation after activation in 35.8 seconds, then submitted an image-edit request. The original Harbor 404 is resolved end to end using the Codex subscription, without an external API key. Generation success is reported by the originating task; edit completion was not separately reported.
