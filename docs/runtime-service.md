# Independent gateway foundation

The menu app now asks `Support/gateway_service.py` to attach to Harbor or prepare one independent per-user service. The helper has no worker replacement operation. UI exit releases its client state and does not terminate inference.

This is a retained-runtime foundation. It does not implement rolling promotion, reliable desktop turn completion, or seamless worker replacement. The initial migration from a legacy UI-owned listener still requires coordinated maintenance. The helper refuses a legacy listener that cannot prove ownership and reports that coordinated maintenance is required. It leaves that listener untouched. A protocol-1 service running in legacy mode can attach only after providing a valid server proof.

## Startup contract

`GrokAdapter.start()` asynchronously runs the bundled helper using the existing `PythonRuntime` selection. The helper's `--source` directory must contain the complete bundled Python runtime, including `grok_adapter.py`, `gateway_runtime.py`, `gateway_service.py`, `gateway_control.py`, `provider_usage.py`, `task_repair.py`, and any other shipped `.py` modules. Bundle resource integration must include the new helper.

The helper reads `MODEL_HARBOR_CONFIG_DIR`, `MODEL_HARBOR_TOKEN_PATH`, and `MODEL_HARBOR_PORT`. All UI owner controls pass through `gateway_control.py`. The Swift client supplies payloads on stdin, never in process arguments or files. The state directory defaults to `~/Library/Application Support/Model Harbor/gateway`; `MODEL_HARBOR_STATE_DIR` overrides it for isolated tests. Runtime directories and the ownership lock are private to the current user. No credential values are included in helper results, job files, or manifests.

Startup proceeds as follows:

1. Inspect endpoint occupancy. An occupied endpoint first receives an unauthenticated nonce challenge at `/harbor/handshake`. It must prove knowledge of a separate private server-proof secret with an HMAC covering the nonce, runtime identity, and actual client/server socket addresses. Only after validating that proof does the helper send the token to `/harbor/status` on the same TCP connection. HTTP automatic reconnection is disabled. Failure, incompatible protocol, malformed identity, a closed proof connection, or a missing private token/proof secret blocks startup. The helper never replaces an occupied endpoint.
2. If the endpoint is free, obtain the private startup lock and inspect it again. Concurrent helper invocations serialize here.
3. Inspect the job named `dev.napier.ModelHarbor.gateway.<digest>`. The suffix derives from the canonical state path and port. An existing job keeps its existing runtime even if the new app contains changed Python files. Startup never invokes `kickstart`, `bootout`, `kill`, or a direct orphan-process fallback.
4. For a new job, verify every `.py` source file, retain it under `runtimes/<inventory-sha256>/`, compare the source and copied inventories, and publish the directory with an exclusive atomic rename. Source links, non-regular Python files, invalid Python syntax, and changes during copying fail. The inventory records relative paths, modes, sizes, and content hashes. Reuse reverifies the manifest and complete retained file set. Retained versions are never automatically deleted.
5. Write a private job description and call only `launchctl bootstrap gui/<uid> <job>`. A failed registration preserves the prepared artifact and exact job description for a later retry. A different configuration, interpreter path, or tampered runtime blocks that retry. It never falls back to a detached process.
6. Poll authenticated status from Swift. Job registration and process health do not prove provider readiness. When attaching to an existing gateway, load display metadata without rewriting credentials, catalogs, the default selection, or shared Codex configuration. Provider synchronization and initial catalog setup run only when bootstrapping a new service or after an explicit user action.
7. Compare the running runtime inventory hash with the bundled candidate. A difference remains visible as a pending gateway update through subsequent health checks. Reopening the interface does not activate a staged gateway version.

The job uses `RunAtLoad` and `KeepAlive`, `/dev/null` output, and these environment values:

- `HOME`, explicit for subscription session discovery.
- `MODEL_HARBOR_INDEPENDENT=1`.
- `MODEL_HARBOR_STATE_DIR`, the canonical private directory.
- `MODEL_HARBOR_RUNTIME_DIGEST`, the exact retained inventory hash.
- `MODEL_HARBOR_CONFIG_DIR`, `MODEL_HARBOR_TOKEN_PATH`, and `MODEL_HARBOR_PORT`.
- `PYTHONDONTWRITEBYTECODE=1`.

The job invokes `[python, -B, -u, retained/grok_adapter.py]`. It has no dependency on the replaceable app bundle. The existing external Python interpreter remains a runtime dependency; the retained inventory proves the Python payload, not an immutable interpreter or a binding to the checkout revision.

## Adapter entrypoint contract

The production adapter must recognize independent mode, preserve a single owner, and disable its GUI-parent watcher only for that mode. A successful authenticated `/harbor/status` response must retain `routing: "per-task"` and the `providers` object, and add:

```json
{
  "runtime": {
    "protocol_version": 1,
    "runtime_id": "64 lowercase hexadecimal characters",
    "boot_id": "a UUID unique to this process",
    "mode": "independent"
  }
}
```

A listener without the proof endpoint is refused without receiving a token or credentials. A service with protocol version 1 and `mode: "legacy"` can attach only after a valid proof, and then requires maintenance for runtime replacement. A response with an incompatible or malformed runtime is rejected. The Swift client exposes `requiresMaintenanceForRuntimeUpdate` to distinguish the legacy case. The explicit `verifyRoute(model:)` client operation sends one authenticated `/harbor/verify` request with a 15-second timeout and requires `verified: true`. Startup and status polling never invoke a billable route probe automatically. The public listener still needs its existing request authentication. The proof authenticates a server that knows the separate private server-proof secret and binds that proof to the retained socket. The secret is stored in a mode-0600 file beside the bridge token, with `.server-proof` appended to its filename. It is generated once, never sent over HTTP, and never distributed in Codex configuration. Knowing the inference token alone cannot forge a server proof. This does not protect against hostile code running as the same user with access to that private secret. The existing inference listener still uses its configured bearer/header authentication; its client transport is unchanged.

The independent process must start without UI-provided provider keys. The UI supplies its existing saved Azure/OpenRouter connections after server-authenticated attachment. While the UI remains open, a changed authenticated boot identity triggers restoration from its already-loaded credentials without a new Keychain read. With the UI closed, a service-manager restart leaves these in-memory provider connections unavailable until the UI reopens and synchronizes them. This foundation does not claim durable credential bootstrap while the UI is closed.

## Verification and operating limit

`Tests/test_gateway_service.py` uses a recording service-manager seam, isolated temporary paths, synthetic tokens, and fixture HTTP listeners. It verifies copied modules, exact runtime identity, duplicate attempts, existing-version retention, failed-registration retry, artifact corruption, source races, unsafe paths, proven legacy/independent attachment, fake-listener rejection without token or credential disclosure, nonce/socket/identity proof validation, and replacement without reconnection. It never registers a real login service or controls a user's process.

The production-entrypoint lifecycle tests must separately prove that an independent gateway survives launcher exit and that duplicate adapters cannot claim the same endpoint. Real launchd registration, installed migration, provider verification, and uninterrupted worker replacement remain separate verification states. This change must be staged without replacing the running app or gateway during active work.

## Ownership and readiness evidence

`gateway_runtime.py` takes a process-lifetime lock and journals hashed task/turn identities, fixed routes, private binding fingerprints, and outstanding requests in SQLite. Completion of an HTTP response retains the turn, because the caller may now execute tools. No timeout or least-recently-used rule deletes ownership. At the storage limit, new turns fail explicitly and existing owners remain.

After a gateway crash, requests from the previous process are recorded as uncertain; they are not reported as active transports in the new process. Their turns cannot be silently retried. No inference text, tool input, or provider credential is stored in the ownership journal. A changed account or route binding is refused before dispatch.

Azure/OpenRouter credential availability and route verification are separate. Proofs cover the process boot, configuration fingerprint, provider/deployment, result, and verification time. A probe or completed request can only update proof for the credentials it actually used and the still-current configuration. Old authentication failures cannot clear a replacement key. Proofs expire after five minutes; status polling performs no inference. Other provider readiness is not established by these probes.

All `/harbor/runtime/{promote,retire,rollback,shutdown}` controls currently refuse with the desktop-lifecycle reason. These are explicit refusal boundaries, not implemented rolling-update operations. See [verification and remaining gates](verification/safe-runtime-results.md).

## Azure request lifetime

The candidate shares a single monotonic deadline across Azure admission, transport, and response delivery. It preserves uncertainty after possible dispatch and disables additional retries in generated Codex configuration. See [Azure request deadlines and retry ownership](azure-request-policy.md) for bounds, evidence, and remaining desktop gates.
