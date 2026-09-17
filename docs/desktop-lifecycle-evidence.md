# Desktop lifecycle evidence

Investigated September 17, 2026 for SAY-3341. This document summarizes the read-only investigation performed during the safe-update audit. The investigation read repository source, official documentation, CLI help, process arguments, and socket metadata. It did not start, stop, restart, or reconfigure a live application. It did not read credentials, conversations, transcripts, database rows, or inference payloads.

## Outcome

A reliable lifecycle integration for the existing desktop tasks has not been established. Automatic runtime retirement and live promotion must remain disabled.

The app-server protocol exposes turn events, and the CLI provides a client for a running control socket. At inspection time, the default control socket was absent and the existing desktop server used its private stdio connection. Launching a second app-server establishes lifecycle evidence only for that server's own tasks.

This finding rules out default-control-socket attachment on the inspected host at that time. It does not establish that a future supported desktop integration is impossible. The documented `notify` completion callback remains a candidate for a separately tested integration. No callback or hook was installed in this investigation.

## Local observations

| Check | Observed result | Limit |
| --- | --- | --- |
| Terminal `codex --version` | `codex-cli 0.150.1` | This is a different build from the desktop's bundled server. |
| Desktop bundled `codex --version` | `codex-cli 0.154.0-alpha.6.2` | Documentation for a newer release may not describe this exact build. |
| Both binaries' `app-server --help` | Listed `proxy`, `daemon`, stdio, Unix socket, and WebSocket options; stdio is the default. | Available commands do not establish which transport the already-running desktop uses. |
| `codex app-server proxy --help` | Described proxying stdio to a running control socket, with `--sock`. | A supported client still requires a listening server to attach to. |
| `codex app-server daemon version` | Failed because the default `app-server-control.sock` did not exist. | No replacement daemon was started. |
| Filtered arguments of the desktop's bundled server | `app-server` and `--analytics-default-enabled`; no explicit `--listen` or `--code-mode-host`. | Only option names relevant to transport were retained. |
| Unix socket metadata for that server | Connected unnamed sockets, including its standard streams, with no named listener. | No undocumented socket messages were sent. |
| TCP listener metadata for that server | No listening TCP socket. | This is a point-in-time observation. |

Directory-name-only inspection found an IPC socket and daemon lock files. Their existence does not prove that they implement public app-server RPC. The investigation did not connect to the IPC socket, read lock contents, inspect desktop internal code, or change launch arguments.

### Existing verification scope

`scripts/verify-live-switch.py` launches `codex app-server --stdio` with a temporary `CODEX_HOME`. It creates synthetic tasks, starts their turns, and reads `turn/completed` notifications from that subprocess's stdout. Its unsubscribe/resume operations target tasks owned by that same subprocess, which it terminates afterward.

This verifies routing, tool calls, and resume in an isolated app-server. It does not verify attachment to the existing desktop host, production lifecycle coverage, lost-event reconciliation, or runtime retirement. The script was read, not executed, for this investigation.

### Inference requests are not a completion source

The audited adapter read canonical task and turn identifiers from request metadata. Its provider counters and response status tracked individual model requests. A successful model response can request a tool, after which the same Codex turn submits another inference request. Therefore, zero active HTTP requests and upstream `response.completed` do not prove that a turn has finished.

The audit also found a 4096-entry in-memory route limit with no completion check. Durable ownership work addresses that loss mechanism, but it does not create missing completion evidence.

An official-documentation search for the exact `x-codex-turn-metadata` name returned no result. This does not prove the field is unavailable. It means the investigation found no verified public completion contract attached to that metadata. Do not invent a completion header or infer completion from a final-looking answer.

## Documented interfaces

The original investigation searched and read the following official documentation on September 17, 2026. These are descriptions of documented interfaces, not claims that Harbor connected to the existing desktop through them.

### App-server

Reference: [Codex app-server](https://learn.chatgpt.com/docs/app-server), including the protocol, lifecycle, API overview, and stored-thread sections.

The protocol includes `turn/started` and terminal `turn/completed` notifications. It uses an initialization handshake and task-scoped subscriptions. `thread/start` subscribes its caller. `thread/read` neither subscribes nor loads the task. `thread/resume` loads or resumes the task and subscribes, so it cannot serve as a passive observation substitute. `thread/loaded/list` describes that server's in-memory tasks.

The documentation describes connecting to a server listener, including a Unix socket. It does not establish attachment to this desktop instance's existing private stdio connection. The inspected page described app-server/WebSocket integration as experimental and unsupported for production workloads.

The investigation established no documented read-only global lifecycle subscription, event replay cursor, or atomic snapshot-plus-subscription contract for the existing desktop host.

### Hooks

Reference: [Codex hooks](https://learn.chatgpt.com/docs/hooks), including common input fields, `UserPromptSubmit`, `Stop`, `SubagentStop`, and `Interrupt`.

Hooks carry session and turn identities. `UserPromptSubmit` runs before a prompt. `Stop` and `SubagentStop` can request continuation, and matching hooks run concurrently. Observing one `Stop` hook therefore does not prove that another hook will not continue the work. `Interrupt` excludes subagents. New or changed non-managed hooks require trust before execution, and the transcript format is documented as unstable.

Hooks may provide useful signals, but missing trust, failed delivery, incomplete host coverage, or an uninstrumented existing task cannot be interpreted as completion.

### Notify

Reference: [Advanced configuration: notifications](https://learn.chatgpt.com/docs/config-file/config-advanced#notifications).

`notify` can run an external program for `agent-turn-complete`, with task and turn identity in its payload. This is a plausible completion adapter. The inspected contract does not supply a matching start event, durable replay or acknowledgement guarantee, or assurance that configuring it now covers already-loaded desktop tasks. Failed and interrupted turns, subagent coverage, and configuration reload behavior remain unverified.

A future integration should retain only identity and lifecycle data and discard message or prompt fields. It must prove coverage and delivery behavior before enabling retirement.

### In-task management tools

Available task-listing, task-reading, and task-waiting tools support on-demand inspection by an agent. Their availability does not establish an automatic event subscription or usable credentials for a standalone Harbor service. They were not called during this investigation.

## Gate before enabling live runtime changes

Harbor must first establish all of the following:

1. The observer identifies the actual desktop host sending Harbor requests and agrees on task/turn identity.
2. Existing active tasks are reconciled when observation begins. Tasks admitted before proven coverage remain unresolved.
3. Start and terminal-completion semantics cover tool gaps, failures, interruptions, reconnects, and subagents.
4. Duplicate or reordered events are handled without deleting valid ownership. Missing events are recoverable through an authoritative snapshot/replay mechanism, or affected owners remain retained.
5. Runtime retirement stops new admission, requires authoritative completion for every owned turn, and waits for all owned transports to finish. Counter zero is an additional condition only.
6. Provider conversational affinity beyond a single turn is understood before continuing requests on another runtime.
7. Isolated production-entrypoint tests and actual desktop integration tests both pass. A fixture app-server does not substitute for the desktop test.

Until those conditions are met, retain unresolved ownership, stage runtime artifacts, and require coordinated maintenance for disruptive replacement. Never evict an unresolved owner because of elapsed time, a quiet request counter, or storage pressure.

SAY-3341 remains **evidence gathered; integration unproven**. Implemented ownership persistence, readiness checks, and production-entrypoint fixtures should be reported separately. They do not complete the desktop lifecycle or uninterrupted runtime replacement gate.
