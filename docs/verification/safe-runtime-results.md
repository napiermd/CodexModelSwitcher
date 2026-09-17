# Independent runtime verification

September 17, 2026. Scope: the staged source change for SAY-3339–SAY-3342. This is not an installed migration or proof of rolling desktop updates.

## Executed checks

| Check | Result | What it establishes |
| --- | --- | --- |
| Python suite, warnings treated as errors | 258 passed (52.685 seconds) | Provider, history, pacing, ownership, startup, control authentication, and release-tool behavior |
| Swift suite | 71 passed | Swift behavior contracts and compilation |
| Signed Xcode app build | Passed | Candidate compiles and uses the existing development signing identity |
| Production-entrypoint suite using packaged resources | 10 passed | The actual bundled runtime survives controller exit and preserves ownership through synthetic tool continuations |
| Staged signed artifact | Passed | Bundle and manifest verified; no source-to-artifact provenance claim |
| Installed app or launchd migration | Not performed | No claim about an installed independent service |
| Real provider readiness probes | Not performed | Synthetic provider checks do not prove live Azure or subscription readiness |
| Existing desktop lifecycle integration | Unproven | Live promotion, retirement, and rollback remain disabled |

The commands are:

```sh
python3 -W error::ResourceWarning -m unittest discover -s Tests -p 'test_*.py' -q
swift test
./scripts/build-app.sh
HARBOR_TEST_BUNDLE_RESOURCES='build/Build/Products/Debug/Model Harbor.app/Contents/Resources' \
  python3 -B -W error::ResourceWarning -m unittest discover -s Tests -p test_gateway_lifecycle.py -q
python3 scripts/stage-update.py \
  --app 'build/Build/Products/Debug/Model Harbor.app' \
  --name independent-gateway-review-20260917
```

The first staging attempt correctly rejected a test-modified signature: importing a packaged Python helper had created bytecode in the app bundle. Packaged tests now disable bytecode writes and assert that all resource bytes remain unchanged. The rebuilt candidate passed all 10 packaged tests in 12.476 seconds and passed the staging signature/inventory checks. The staged directory is `build/staged-updates/independent-gateway-review-20260917`. Its manifest records `installed`, `provider_verified`, and `live_handoff_verified` as false. Source-to-artifact binding remains explicitly unverified. Website checks and JavaScript syntax checks also passed.

## Production-entrypoint coverage

Tests run `grok_adapter.py` as a real process from a retained copy of the complete runtime. HOME, Codex configuration, bridge/proof keys, state, ports, logs, and upstreams are synthetic and isolated. The harness never registers launchd jobs, accesses real credentials, or modifies user task history.

- A controller exits while two different model routes are streaming. Both complete; a later tool continuation retains the original route.
- A zero-request tool gap lasts beyond the old parent-watcher interval. Removing the synthetic UI payload does not remove the retained runtime.
- Reopened authenticated control clients attach to the same boot identity.
- Duplicate startup, an occupied port, and a mismatched artifact digest preserve the existing process.
- Missing and invalid credentials fail verification. Repeated status checks send no inference.
- Partial-stream failure remains uncertain after restart; retry does not dispatch again. Cancellation does not replay output.
- An account change during an unfinished turn fails before upstream dispatch; restoring the original connection allows continuation.
- Every tested promotion, retirement, rollback, or shutdown attempt refuses because desktop completion is unverified.
- Sentinel history and packaged resource bytes remain unchanged.

## Review fixes

A read-only review reproduced two credential races and a fake-listener vulnerability. Dedicated regressions now cover stale success/failure/probe outcomes, key replacement during request preparation, a fake listener knowing the inference token, wrong socket/nonce proofs, and listener replacement between proof and control. The client disables automatic reconnection after proving the server.

Gateway startup work runs off the UI actor. A detected new gateway boot restores Azure/OpenRouter credentials already loaded in the open UI, without reading Keychain again. A gateway restart while the UI is closed still requires reopening the UI to restore those volatile connections. Uncertain deliveries remain blocked rather than replayed.

## Remaining execution gates

SAY-3341 is not complete: no authoritative event subscription to the actual running desktop host was established. SAY-3342's promotion/rollback scenarios depend on SAY-3343 and are not implemented. SAY-3343 remains disabled under its original prerequisite. The retained service is a foundation for that work, not a substitute for it.

The combined 258-test Python run includes 27 hermetic Bifrost pilot tests for cleanup, aggregate gate reporting, native/converted route selection, retry ownership, authentication, usage fidelity, opaque history, and complete streamed events. Docker is mocked in driver tests; protocol tests use synthetic local HTTP fixtures. These tests establish the harness behavior, not a passing native Bifrost transport. The native container attempt failed during startup with zero test requests. Existing Swift/build/packaged-artifact evidence above belongs to the unchanged runtime implementation; the follow-up edits affect only the experiment, its tests, and documentation.

The Bifrost experiment and its narrower evidence are recorded separately in [Bifrost evaluation](../bifrost-evaluation.md). No live route was moved to it.
