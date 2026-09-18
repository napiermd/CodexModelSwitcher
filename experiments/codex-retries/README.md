# Optional Codex CLI retry experiment

This harness checks the installed **native Codex CLI**, using synthetic Responses on a local HTTP server. It does not run Harbor or call Azure. It is optional and is not part of the normal unit-test suite.

## Run

Supply an absolute path to the native CLI executable and a new output filename:

```sh
python3 -B -W error::ResourceWarning experiments/codex-retries/run.py \
  --codex /absolute/path/to/native/codex \
  --expected-version 0.150.1 \
  --output /tmp/codex-retries-new-run.json
```

The harness checks `--version` and `exec --help` first. A different version, missing isolation option, unexpected child environment, failed request assertion, or timeout makes the report fail. Existing reports are never replaced. An authentication requirement is a failed experiment; the harness does not retrieve credentials or offer a login flow.

Each case uses a new temporary `HOME`, `CODEX_HOME`, working directory, and temporary directory. The child environment is constructed from an allowlist instead of inherited. Its only credential is a fixed synthetic token accepted by the local fixture. Configuration sets file-based credential storage in the temporary home, disables apps and agents, disables WebSockets, and uses:

```toml
request_max_retries = 0
stream_max_retries = 0
```

Each CLI invocation has a 30-second external timeout; version/help calls have ten seconds. The fixture binds only to `127.0.0.1`. HTTP, HTTPS, and ALL proxy variables point to this fixture, which rejects external CONNECT and non-fixture requests. This is proxy-based isolation, not an operating-system network firewall. No real provider URL or credentials are supplied. Timeout cleanup kills the launched process group; server cleanup and temporary-state removal run on failure as well as success.

The report retains CLI JSON events, POST counts, elapsed times, version, and executable SHA-256. It replaces temporary paths, the executable path, dynamic ports, synthetic token, and generated thread IDs with placeholders. Request bodies, headers, and blocked CONNECT targets are not recorded.

## Recorded result: September 17, 2026

`results.cli-0.150.1.json` was produced by running this checked-in harness with `codex-cli 0.150.1`. All cases exited with code 1, as expected for the synthetic failures; none timed out.

| Fixture | Responses POSTs | CLI elapsed time | Observed outcome |
| --- | ---: | ---: | --- |
| HTTP 429 | 1 | 480 ms | Failed turn; retry limit exceeded |
| HTTP 503 | 1 | 508 ms | Failed turn; returned 503 |
| Nonempty SSE delta, then EOF | 1 | 473 ms | Failed turn before `response.completed` |
| Completed output item, then EOF before response completion | 1 | 208 ms | Emitted `synthetic partial output`, then failed without replay |

The rejecting local proxy blocked 1, 2, 2, and 2 unrelated HTTPS CONNECT attempts respectively. The synthetic model used fallback metadata; its warning is retained in the report. No `turn.completed` event was accepted as success.

## What this proves

For a newly launched CLI 0.150.1 process using this isolated custom provider, both zero settings result in one HTTP attempt for each tested failure, including a failure after accepted output. The harness verifies that request model, streaming flag, and synthetic authorization match the intended fixture.

This does not verify an existing desktop task's loaded configuration, reload behavior, desktop lifecycle, real Azure behavior, Harbor's transport, opaque-history preservation, or any other CLI version. The fixture sets `requires_openai_auth = false`; it does not test Harbor's separate desktop authentication configuration. It does not establish a retry/backoff policy for nonzero settings or parse `Retry-After` into one.
