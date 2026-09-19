# Azure stream-resume verification

Date: 2026-09-19. Tracking: [SAY-3386](https://linear.app/sayvant/issue/SAY-3386).

## Incident and root cause

The shared gateway remained alive while individual Azure OpenAI Sol SSE connections ended before a terminal Responses event. The affected requests had already been dispatched and had emitted output, so another inference POST could duplicate model or tool work. Automatic task compaction was also visible near the configured context window, but it was not the transport failure.

Harbor previously sent Azure streams with `store=false`. It could identify a premature EOF but had no provider cursor with which to recover. Azure's supported recovery contract requires a stored background response and resumes that same response by ID and raw sequence number.

## Implemented contract

- Resumability is an explicit Azure catalog capability (`harbor_resumable_streaming`). The live-proven `gpt-5.6-sol` identity defaults on; other deployments default off and require equivalent validation before enabling it.
- A capable stream uses `background=true`, `store=true`, and one inference POST.
- Harbor retains the provider response ID and raw Azure sequence before lifecycle heartbeats can renumber downstream events.
- A premature EOF or eligible connection error can issue up to three `GET /responses/{id}?stream=true&starting_after={sequence}` requests inside the original 180-second deadline while preserving admission and turn ownership.
- A resume 404 checks the same stored response once to resolve the completion race. No second inference POST exists.
- Stored responses receive a bounded best-effort DELETE on success, provider failure, cancellation, deadline, or exhausted recovery.
- Authenticated status exposes aggregate attempt/success/failure and cleanup counters only. It contains no response IDs, prompts, credentials, or output.
- Azure nonstream requests, Azure deployments without the capability, and non-Azure providers retain their prior behavior.

## Verification

The loopback regression suite covers dropped EOF, connection reset, raw cursor preservation after heartbeat renumbering, completion-race retrieval, bounded exhaustion, cancellation during resume, URL encoding, cleanup, nonstream behavior, capability isolation, and content-free diagnostics.

The isolated live proof `python3 -B scripts/verify-azure-stream-resume.py --live --deployment gpt-5.6-sol --close-after 4` intentionally closed the first provider stream after sequence 4. Azure accepted exactly one inference POST and one resume GET, returned sequence 5 first (`strictly_after: true`), and ended with `response.completed`. Cleanup was attempted. The probe printed no response ID, key, prompt content, or generated content.

Final pre-deployment checks passed on September 19:

- 572 Python tests with `ResourceWarning` treated as an error.
- 109 Swift tests.
- Python bytecode compilation for the adapter and live proof script.
- `git diff --check`.
- A signed application build using `Apple Development: Andrew Napier (P9ZRLF7F56)`. `codesign --verify --deep --strict` passed.
- A second isolated live Azure proof with the exact command above and the same one-POST, one-GET, sequence 4-to-5 completion result.

Coordinated runtime activation is recorded separately; it is not inferred from unit tests or the isolated provider proof.
