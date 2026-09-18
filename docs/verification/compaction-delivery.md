# Compaction delivery and model switching

September 18, 2026.

Azure could deliver a completed response and then classify the client's normal terminal-frame disconnect as uncertain. The loopback regression reproduces a socket close while the upstream response context exits. Terminal delivery now completes the budget after a successful write and flush. Partial streams, failed writes, deadlines, and cancellation before delivery retain the existing uncertainty behavior.

Codex also uses the previous model for pre-turn compaction when switching to a smaller context window. Those requests share the new turn ID. Pre-turn compaction now has separate ownership from normal sampling, so it cannot pin the selected model to the previous provider. Normal turn keys and existing ownership records remain unchanged.

OpenRouter requests without an explicit output allowance now use 32,768 tokens. Explicit allowances are preserved. This reduces maximum-output credit reservations; it does not bypass provider billing or guarantee that an account can fund every concurrent request.

Validation:

- 415 Python tests passed, including the reproduced completion race, partial-stream deadlines, cancellation, and route identity cases.
- An isolated live Azure gateway accepted three same-turn requests with clients closing at the completed SSE frame.
- A live Fable tool call and streamed continuation passed.
- `scripts/verify-compaction-delivery.py --live` additionally exercises a previous-model Fable summary followed by Azure requests with the same turn identity. It uses only synthetic prompts and prints aggregate evidence.

The live proof sends request metadata equivalent to compaction; it does not drive a full Codex automatic compaction with a 128k-token history. Existing uncertain records are preserved and no user tool actions are replayed.
