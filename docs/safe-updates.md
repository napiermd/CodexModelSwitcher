# Protect tasks during Harbor updates

Status: stage-only release tooling and a verified independent-runtime candidate are available. The candidate is not installed. Automatic promotion, retirement, and rollback remain disabled until actual desktop lifecycle integration is verified.

See the [audit, file map, Linear issues, and execution checklist](update-safety-audit.md).

## Why the previous update procedure was unsafe

The September 17, 2026 update used an ad hoc installer that checked the bridge's active HTTP request counters, then terminated the installed app and its bridge before replacing the bundle. Those counters do not cover a complete agent turn. A task can be running a tool with no model request in flight, then need the gateway again immediately.

The installer also accepted the presence of the `azure_ready` status field without requiring its value to be true. A replacement process could therefore be announced as running before its Azure credentials were configured.

The shared bridge is a child of the menu app (`GrokAdapter.swift`). Its parent watcher shuts the HTTP server down when the app exits (`Support/grok_adapter.py`). Restarting the app affects every task routed through that bridge. The Azure request-format regression fix and its successful synthetic probe did not establish that the installation was safe for concurrent tasks.

## Current update procedure

1. Build, inspect, and test a candidate without replacing the installed app.
2. Run candidates on a separate port with isolated state and synthetic conversations. Preserve the working runtime, provider credentials, model selections, and conversation files.
3. Leave the candidate staged while users are working. An active-request count of zero is never an installation gate.
4. If installation requires a restart, coordinate a maintenance window after affected tasks have been paused or completed. Do not restart the shared bridge as a background part of an unrelated repair.
5. Verify credentials and the intended provider route after installation. A successful health response establishes process health only. A readiness field must be true, and an end-to-end provider probe must succeed.
6. Record the source version, checks, installation state, and rollback artifact. Do not store credentials or conversation content in the record.

## Stage a release

On macOS, build with your established signing identity, then stage the signed bundle:

```sh
./scripts/build-app.sh
python3 scripts/stage-update.py --app "build/Build/Products/Debug/Model Harbor.app"
```

If you set `MODEL_HARBOR_BUILD_DIR`, pass the actual built app path to `--app`. An ad-hoc evaluation build can also be staged; its signature is recorded and does not establish stable Keychain trust.

The command writes only beneath this checkout's `build/staged-updates/`. It does not launch the candidate, install it, control processes, read provider credentials, or change Codex configuration. `--name <unique-name>` gives the stage a chosen name. Existing names, including an empty directory created concurrently, are never replaced.

Before publishing a completed stage, the command verifies the source and copied signatures, compares file hashes/modes/link targets, and checks that the source still matches. Relative internal bundle symlinks are preserved; external, absolute, dangling, and looping symlinks are rejected. Special files and symlinked staging directories are rejected. A failed copy/verification removes its own temporary output. The completed directory is published with macOS's exclusive atomic rename.

Each stage contains `Model Harbor.app` and `manifest.json`. The manifest records bundle versions, signing identity, a file inventory, the checkout revision and dirty status observed at staging, and these explicit results:

- `state: staged`
- `installed: false`
- `provider_verified: false`
- `live_handoff_verified: false`

**Source provenance is contextual:** staging an existing bundle does not prove it came from the current commit. The manifest therefore records `artifact_source_binding: unverified`, even for a clean checkout. Do not describe that commit as the app's verified build source. Hashes identify the artifact that was actually copied. Stages are never overwritten by this command, but their owner can still modify files afterward; reverify integrity before any future installation.

This is a macOS release-preparation command, not an updater. It provides no live promotion path. The runtime work and its required gates are tracked in the audit linked above.

## Required runtime design

The menu app should control an independently supervised background gateway. Closing or updating the UI should not terminate inference. Runtime artifacts must live in versioned locations that remain valid when the UI bundle is replaced.

For a gateway update, start a candidate alongside the existing worker, load credentials, and verify its routes before admitting new work. Retain the previous worker for its existing turns and streams, including intervals spent executing tools. This requires a reliable turn lifecycle signal and admission control; a timeout or quiet request counter is not a substitute. If that signal is unavailable, defer replacement to maintenance.

Keep model, provider, and account choices pinned for each task. Configuration changes should be transactional, preserve stable model identifiers, and have a rollback path. Do not replay partially delivered tool activity or rewrite saved history to make an update appear successful. Give retries a bounded total deadline, and require an explicit policy for model substitution.

## Verification required before claiming uninterrupted updates

- Update the UI while multiple providers are streaming; all streams complete.
- Update while an agent is executing a tool; its next request reaches the correct worker and model.
- Reject a candidate whose credentials, model deployment, or route probe fails; current tasks continue.
- Roll back new traffic while old streams complete on their original worker.
- Preserve task model choices, tool-call/result pairing, and history bytes.
- Test cancellation and interrupted requests without executing a tool twice.

## Evaluating another gateway

A Bifrost pilot should use a separate port and state directory, an isolated Codex home, synthetic inputs, and one Azure deployment first. Compare image input, tool calls, resumed history, cancellation, error handling, latency, and usage accounting against the existing route. Test the exact pinned version that would be deployed. Keep retries owned by one layer with a shared deadline; multiple nested retry loops can prolong an outage.

Provider API support does not establish compatibility with Harbor's subscription sign-ins. Preserve those adapters until they are separately verified. Installing a new gateway does not by itself implement the update handoff described above.
