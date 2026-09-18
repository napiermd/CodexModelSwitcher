# Context compaction audit

Date: 2026-09-18. Baseline: `3bc6eca`. Implementation branch: `codex/context-audit`.

## Root issue and remaining uncertainty

The router hardening patch is already activated. Its heartbeats, stream failure diagnostics, catalog provenance and native image routing do not explain or repair all automatic compaction behavior. The earlier connection resets are a separate incident; their initiating peer remains unknown.

A local read-only audit of the affected rollout reproduces eight consecutive short cycles beginning at lines 15822 through 15974. Each has one normal provider request, with input between 140,277 and 140,870 tokens against an observed effective context window of 121,600. Compaction repeats after 19–41 seconds. The next cycle begins under the same small window but continues through the later correction, so it is not another one-request loop.

Later cycles observe 258,400 effective tokens. This is evidence from task usage, not an inference from a catalog file. It is consistent with 95% of the 272,000 default. The 872,000 advertised maximum is neither the selected context nor independently verified Azure capacity.

Three completed examples, all on September 18 UTC:

| Cycle start | Local estimate | First provider input | Unattributed difference | Last normal input | Normal requests | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 21:30:40.736 | 20,653 | 144,035 | 123,382 | 244,545 | 20 | 261.688 |
| 21:35:02.424 | 20,074 | 143,558 | 123,484 | 233,813 | 29 | 420.919 |
| 21:42:03.343 | 18,380 | 141,882 | 123,502 | 238,545 | 24 | 261.872 |

The first example includes 27 tool outputs totaling 626,137 compact ASCII JSON bytes. This is a serialization metric, not a token count or the exact provider wire size. The compactor's own response is excluded by response ID in all three examples.

The roughly 123,000-token difference remains unattributed. The earliest two cycles in the same rollout showed differences of only 10,455 and 9,948 tokens. Request composition or accounting changed during the task, but this audit cannot identify which component changed. Do not label the difference duplicated history, tool schemas, instructions, images, or a provider bug without measurement.

## Implementation and files

- `scripts/audit-context.py` streams an explicitly supplied local rollout read-only and emits JSON with recent compaction cycles.
- `Tests/test_context_audit.py` covers accounting, confidentiality, malformed and partial records, replayed usage, model changes, retention bounds, and CLI behavior.
- `Package.swift` excludes the new Python test from SwiftPM.
- `docs/codex-router-comparison.md` links this follow-up without reopening the deployed patch.

Run `python3 scripts/audit-context.py /explicit/local/rollout.jsonl --limit 10`. No automatic session discovery, source path, title, prompt, tool output, response ID or raw model string is emitted. Model changes are represented by numeric epochs instead of untrusted identifiers. The input is not rewritten.

Memory grows with the largest single JSONL record, up to 1,000 retained cycle summaries, and the last 256 response IDs. It does not grow with the total file length. A record containing an image can still be large.

## Accounting and risks

Only distinct identified `token_usage_record` entries count as provider requests. Repeated `token_count` snapshots and cumulative thread totals do not. Cached input is already part of input tokens. Missing usage stays unknown; the script never invents tokens from bytes.

The latest distinct provider record is held until the next record or compaction so the compactor can be excluded by response ID. Nonadjacent matching usage is explicitly marked unresolved, not silently claimed excluded. An open cycle may still include a compactor whose completion record has not arrived. Deduplication covers only the last 256 IDs.

A model change clears the currently observed window until a new observation arrives. Earlier request measurements retain their contemporaneous window and estimate. A trailing incomplete record is reported separately from malformed interior JSON. Reading an actively appended file is not an atomic snapshot. Reports reflect the records read during that invocation.

These constraints prevent content disclosure and false precision. No runtime, routing, thresholds, catalogs, conversation history, or global settings were changed.

## Focused issues and implementation order

1. [SAY-3359](https://linear.app/sayvant/issue/SAY-3359): reproducible content-free rollout audit. Implemented here.
2. [SAY-3360](https://linear.app/sayvant/issue/SAY-3360): content-free request composition measurements, correlated to usage, before attempting to reduce the unexplained difference. Preserve body bytes and routing. Test text, tools, images, references, cumulative and cached usage, and diagnostic redaction.
3. [SAY-3361](https://linear.app/sayvant/issue/SAY-3361): atomically reconcile changed catalog sources while preserving manual capability overrides and route bindings. Test drift, interrupted writes, unchanged captures, and manual restrictions.
4. [SAY-3362](https://linear.app/sayvant/issue/SAY-3362): distinguish task-observed context from published defaults, advertised maxima, and unknown desktop adoption. Test model switches, stale observations and absent evidence.
5. [SAY-3363](https://linear.app/sayvant/issue/SAY-3363): verify deployment capacity before offering explicit larger context. Test opt-in selection, rejected capacity, fallback and unchanged unrelated tasks.
6. [SAY-3364](https://linear.app/sayvant/issue/SAY-3364): optional isolated Chat Completions compatibility pilot. Verify streaming tools, reasoning, usage, cancellation and partial failures. This is not a compaction fix.

Each Linear issue contains its implementation map, acceptance criteria, risks and required tests. Instrumentation precedes any pressure-reduction change. Catalog and compatibility work must not silently change active task bindings or retry uncertain provider work.

## Verification

- Seven initial regression tests failed because the CLI did not exist, then passed with the implementation.
- An added model-switch regression exposed loss of a pending request's estimate. The implementation now captures estimates with each pending request.
- Fourteen focused tests pass, including unchanged source hash and private sentinels absent from output.
- Swift suite: 104 tests passed.
- Real rollout audit at one checkpoint: 43 compactions, no malformed records. The task was still appending; totals are not a permanent task-wide count.
- Full Python suite: 444 tests passed in 125.820 seconds, including all 14 audit tests.

## Execution checklist

- [x] Audit source, deployment evidence and local task usage before editing.
- [x] Create six focused Linear issues with dependencies.
- [x] Implement and test the read-only audit in an isolated worktree.
- [x] Reproduce the short-loop and corrected-window patterns without publishing task content.
- [ ] Attribute the unexplained request difference with nonmutating measurements.
- [ ] Reconcile catalogs and report observed versus advertised context separately.
- [ ] Verify larger-context capacity before offering it explicitly.
- [ ] Evaluate the optional compatibility adapter in isolation.
