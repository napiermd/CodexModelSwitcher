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

## Installed verification

Activated through the coordinated maintenance controller on September 18. Receipt `gateway-e283a39b-790c-46f8-9ea8-b0460cc8cb52/maintenance.json` reports `resumed`, six resumed clients, preserved ownership and shared configuration. Runtime `9baa7658a0a5ef318f36b8613ab03dd2f463aad89ce217191b3d59deec73b763` is active.

The installed proof passed four requests sharing one synthetic turn: Fable pre-turn compaction followed by three Azure requests, closing each response at the terminal frame. Provider response headers confirmed Fable then Azure. The signed application bundle was installed with the existing signing identity and a rollback copy. Computer Use verified Azure and OpenRouter both display Connected; OpenRouter lists Fable 5.1 and DeepSeek V4.1 Flash.

Mainline integration passed 418 local Python tests and all GitHub CI checks before PR 15 merged. PR 18 contains the provider/compaction fixes. The older failed portal tasks remain stopped because a replacement task is already active on the same work; no tool actions or history were replayed.
