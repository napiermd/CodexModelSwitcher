# Independent runtime verification

September 17, 2026. This file records successive source, packaging, and installation checkpoints for SAY-3339–SAY-3342. The first checkpoints are historical stage-only results. The installation record below supersedes their installation status without claiming rolling desktop updates.

## Executed checks

| Check | Result | What it establishes |
| --- | --- | --- |
| Python suite, warnings treated as errors | 272 passed (50.772 seconds) | Provider, history, pacing, ownership, startup, control authentication, and release-tool behavior |
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

Gateway startup work runs off the UI actor. Automatic restoration of already-loaded Azure/OpenRouter credentials is enabled only in the UI session that bootstrapped a new service. A reopened UI attaches to an existing service without hydrating credentials or synchronizing configuration. Reopening alone therefore does not restore volatile connections after a gateway restart; an explicit connection action is required. Uncertain deliveries remain blocked rather than replayed.

## Remaining execution gates

SAY-3341 is not complete: no authoritative event subscription to the actual running desktop host was established. SAY-3342's promotion/rollback scenarios depend on SAY-3343 and are not implemented. SAY-3343 remains disabled under its original prerequisite. The retained service is a foundation for that work, not a substitute for it.

The combined 258-test Python run includes 27 hermetic Bifrost pilot tests for cleanup, aggregate gate reporting, native/converted route selection, retry ownership, authentication, usage fidelity, opaque history, and complete streamed events. Docker is mocked in driver tests; protocol tests use synthetic local HTTP fixtures. These tests establish harness behavior. Separately, the isolated GitHub Actions native-passthrough run `35275679952` passed all 24 synthetic transport cases on the pinned AMD64 image, including content/version and cleanup gates. The earlier local ARM64 native attempt failed during startup with zero test requests; its evidence is retained. GitHub Actions Checks run `35275679957`, on PR head `aed7f638c166b84b4e411d967df8336a331adcd5`, also passed 258 Python tests, 71 Swift tests, the evaluation app build, all 10 packaged gateway tests, staging, website checks, and JavaScript syntax checks. CI used its evaluation signing/build setup; the separate local signed artifact above uses the existing development identity. The experiment follow-up does not change the runtime implementation.

The next native matrix, Actions run `35277669718` at PR head `30b5ef067a1a5ec25b1926350b91540ec9fbf7eb`, passed three complete samples on both native AMD64 and ARM64 runners: six matrices and 144 passing case executions. The actual PR merge checkout was `eccf50640b32aa01fd809af6811ada00514f1b90`. Report hashes were verified against each artifact's generated provenance. The representative ARM64 report and all six sample hashes/selected observations are retained in `experiments/bifrost/`. Observed stall completion ranged from 3,002.32 to 3,005.98 ms; observed cancellation disconnect ranged from 500.58 to 501.04 ms. These are fixture measurements, not Azure latency or a universal deadline bound. Checks run `35277669658` also passed at head `30b5ef0`. The local Colima environment remains unverified and was not started again under memory pressure.

The pressure-study follow-up passed all **272 Python tests in 50.772 seconds** with `ResourceWarning` treated as an error, including 40 Bifrost harness tests. Its thirteen added tests cover observed follower ordering, exact request attribution, bounded holds/release cleanup, two-sided stream overlap, result fidelity, study isolation, incompatible-surface rejection, and content detection. The added Azure real-handler test covers eight error/header combinations across sixteen explicit requests with exactly one upstream attempt per client request. Independent read-only review found no remaining P1/P2 issues in the pressure driver, tests, or workflow. Actual pinned pressure execution subsequently passed its evidence gates on both architectures in Actions run `35279368950`. It observed one held request, one queued request, and one 503 rejection with upstream counts 1/1/0. It also observed two simultaneously open streams despite concurrency=1, disproving an active-stream bound. Both unchanged reports and SHA-256/run/checkout provenance are retained. This is a measured limitation, not a pacing guarantee.

Actions Checks run `35279368948` also passed at implementation head `6920a037ad81939bcebb294396f1b27d7b94440d`: Swift and Python suites, evaluation app build, packaged gateway tests, staging, and website/JavaScript checks. The following report commit changes evidence and documentation only.

The Bifrost experiment and its narrower evidence are recorded separately in [Bifrost evaluation](../bifrost-evaluation.md). No live route was moved to it.


## Azure stream admission candidate, September 17, 2026

The Bifrost pressure finding led to a process-local Harbor admission guard in `Support/azure_admission.py`, used by Azure inference and gateway verification. The staged defaults are two active requests and sixteen waiting requests per endpoint/deployment, with a 30-second monotonic queue deadline. Admission remains held through upstream close. FIFO waiters poll cancellation at configured intervals of at most 100 ms. Empty buckets are removed and permits release idempotently. No Azure retry loop was added.

Twenty new admission tests and six real HTTP-handler tests establish capacity, FIFO behavior, bounded waiting, cancellation before dispatch, independent deployments, and close-before-release. A TCP reset during queued verification preserves an existing readiness proof and makes zero upstream attempts. Queue failures preserve task ownership without marking an undispatched request uncertain. Existing exact-status/Retry-After forwarding tests still pass. Independent review found no P1/P2 issues and passed 50 focused tests.

The first full run exposed an existing test race: the OpenRouter auth-error assertion could read activity before the handler's `finally` bookkeeping ran. That test now waits for the actual activity completion event. No production behavior was changed for that test fix.

Final local checks passed 298 Python tests with ResourceWarning treated as an error, 71 Swift tests, the signed Xcode build, and ten tests against the packaged gateway. The signed bundle contains `azure_admission.py`. Staging succeeded at `build/staged-updates/independent-gateway-admission-review-20260917/`; its manifest reports installed=false, provider_verified=false, and live_handoff_verified=false. Exact source-to-build binding remains unverified.

Open requirements remain explicit: total request deadlines, effective desktop retry ownership, coordination across runtimes, real Azure parity, and authoritative desktop turn completion. Local stream admission does not satisfy those requirements or authorize a production Bifrost route.


The first hosted run for the admission change exposed a test-only scheduling assumption: a 20 ms timeout test resumed after 141 ms on a loaded runner. Deadline and override assertions now use an injected monotonic clock with exact condition-wait budgets. Real concurrent stream, FIFO, cancellation, and close-barrier tests remain. This does not promise operating-system scheduling within 100 ms or weaken the production queue deadline.

## Absolute Azure deadline and caller retry checkpoint — September 17, 2026

The candidate now gives accepted Azure inference one 180-second monotonic budget across admission, DNS, TCP/TLS, upstream headers/body/SSE, and downstream writes. Verification uses ten seconds. Socket trickles cannot renew this budget. Cancellation shuts down only owned upstream sockets; DNS uses four bounded workers and late resolutions cannot dispatch. Reporting an expired request has its own maximum 250-millisecond write budget.

The gateway makes one attempt and keeps interrupted dispatched turns uncertain. Nonstream bodies are read before success headers. Premature EOF and errors over the 64 KiB forwarding limit cannot be treated as fully delivered errors. Admission is released after upstream closure. Separate header/body/flush writes each check the remaining budget.

Generated shared Codex provider configuration sets both request and stream retries to zero. The repeatable `experiments/codex-retries/run.py` harness verified installed CLI 0.150.1 against local 429, 503, interrupted delta, and accepted-output-before-EOF fixtures: one actual POST each, no replay. These fresh CLI runs do not establish active desktop configuration or reload behavior. The harness blocks proxy-routed external requests; it is not an OS network sandbox.

Local validation passed 326 Python tests in 65.064 seconds with warnings treated as errors and 73 Swift tests. The signed app build passed. All ten production-entrypoint tests passed against its packaged resources in 12.761 seconds with bytecode writes disabled. Focused transport/handler tests cover actual sockets, including a nonreading downstream client. Review found incomplete-error framing and per-write budget gaps; both were fixed with regressions before staging.

No live provider calls, installed app changes, global configuration edits, or task-history rewrites were made. Actual desktop completion/handoff, cross-runtime pacing, composed Bifrost routing, and real Azure history/performance parity remain open. See [the policy](../azure-request-policy.md) for exact scope.

A final independent review also found that probe `read1()` could accept a completed JSON prefix despite an unmet Content-Length. Verification now rejects that response and clears stale readiness; the real-HTTP regression returns 503 after exactly one upstream request.

Final local rerun after the readiness fix: **327 Python tests passed in 63.389 seconds**, with all warnings treated as errors. The unchanged Swift changes passed 73 tests. The rebuilt signed candidate passed all ten packaged lifecycle tests in 12.574 seconds, plus site and JavaScript checks. Independent review passed 28 focused tests before the last probe-framing regression and confirmed the preceding error-budget findings closed.

The new signed stage is `build/staged-updates/independent-gateway-deadline-review-20260917`. Its manifest observed clean checkout `a8f4c320547b77282796ad0294fbd3bda01fbb58` and records `installed=false`, `provider_verified=false`, `live_handoff_verified=false`, and `artifact_source_binding=unverified`. Bundle inventory SHA-256: `0c9df9443eacffb1daae10a127f3031c7d1fc7b07d5a06aa91de8decc0ee60e2`. All six staged gateway `.py` modules match the current source bytes. This comparison does not establish whole-artifact source provenance or live installation.


## Authorized installation and live Azure verification

On September 17, 2026, Andrew explicitly requested immediate installation after the shared-gateway restart had been identified as the remaining installation step. The installer verified the staged signature and complete inventory, backed up the installed signed app and private settings, prepared a verified incoming copy, stopped the previous menu app and its child gateway, and replaced `/Applications/Model Harbor.app`.

The installed bundle matches stage `independent-gateway-deadline-review-20260917` exactly. The independent gateway is registered with the per-user service manager. Its retained runtime matches the installed resource inventory, with runtime ID `5da659334bfd7bbd5af5456e9dd7761ea7b9c94f7e9310d447506900c42b3ad5`. Authenticated runtime status reported independent mode. Saved Azure and OpenRouter credentials restored without copying them into this record.

A live verification request for `harbor/azure/gpt-5.6-sol` completed in 2.11 seconds, and `providers.azure_ready` was true. The installed UI reported Azure Connected and Ready for the next request. This verifies that route at that time; it does not verify resumed reasoning history, all deployments, or OpenRouter inference. Saved Codex configuration has both request and stream retries set to zero. Reload by every already-running desktop task is unverified. Codex was not restarted.

The local installation and rollback record is `~/Library/Application Support/Model Harbor/updates/20260917-170043/installation.json`. Installation and provider verification are true; interruption-free handoff remains false. The original stage manifest is unchanged. The prior application and settings remain available for rollback.

Both final-head CI workflows passed at `85040f670909d03a55683c9927e01bed6a78ca74`: Checks run 35288496279 and Bifrost native run 35288496251. This deployment does not close the remaining desktop completion, promotion/rollback, composed Bifrost, or reasoning-history requirements.

## Direct Azure reasoning-history candidate

The isolated source reproduction dropped one reasoning item from a two-item request. Eight new tests now cover opaque input/output preservation, tool continuity, and explicit one-attempt rejection. All 335 Python tests pass with warnings treated as errors. The signed build and ten packaged lifecycle tests pass. The candidate is staged as `azure-history-preservation-20260917` and has not replaced the installed runtime.

The bounded real-provider verifier is implemented, but its credential preflight timed out before sending an Azure request. Real continuation remains unverified. See [the history report](azure-history.md) for the exact scope and remaining gates.
