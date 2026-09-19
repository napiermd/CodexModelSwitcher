# Direction audit

Date: 2026-09-18. Scope: every open workstream on `codex/router-adoption-audit` and its ancestors. This is an audit, not an implementation.

## The actual situation

`origin/main` (`b72ad38`) is an ancestor of the work branch. The whole stack — context-stream fix, router hardening, context-audit diagnostics, and the codex-router adoption layer — fast-forwards cleanly. Nothing is merged back yet. The comparison document is accurate: the deployed runtime already carries heartbeats, provenance, empty-completion rejection, and image routing, and the remaining codex-router ideas were evaluated honestly in `router-adoption-audit.md`.

## The drift the direction question is about

Three rounds of work answered three different questions, and the last two rounds kept going past the original one:

1. **The crash report** ("stream disconnected before completion") — answered: socket resets whose initiator cannot be reconstructed from the old logs. Repaired by heartbeats plus terminal-failure frames. Done and activated.
2. **The compaction loop** — answered: 121,600 effective window against ~140K first requests, with the 129,025-token cached prefix explaining the apparent estimate gap. Done; the corrected catalog was published.
3. **"What else can we take from codex-router"** — answered twice: a comparison doc, then ten more features (SAY-3370–3379) implemented on the branch.

The last round is where direction was lost. Ten features landed as one undifferentiated stack. They are not equal. Some close real observed gaps; others are speculative ports of code Harbor has no current need for, and two of them are gated behind environment flags that default off — which is another way of saying the evidence for enabling them does not exist yet.

## What the branch actually contains, sorted by evidence of need

**Justified by an observed failure in Harbor's own logs:**

- The usage ledger (`usage_ledger.py`). The compaction investigation was done by hand because Harbor recorded nothing durably. This is the fix for the diagnostic blind spot, and three other features hang off it.
- The usage estimator (`usage_estimate.py`). The compaction incident's confusing token counts were partly missing-usage artifacts. Estimated-vs-reported labeling is the honest answer.
- Context-window drift report (`report-context-drift.py`). Directly automates the proof that a declared window is wrong, from accepted usage.
- Fail-closed invalid tool-call arguments (`7e529c8`). A completed call with unparseable JSON is a real corruption class; failing the turn is cheaper than poisoning history.
- Transport failure classification (`transport.py`). "The response connection was interrupted" was unanswerable partly because Harbor had no named failure classes. This closes that.

**Justified but inactive or isolated:**

- Metrics rollup persistence (`f3051a0`). Only active when `MODEL_HARBOR_REQUEST_METRICS=1`.
- Deferred app-tool merge (`da38238`). Gated behind `MODEL_HARBOR_CODEX_APP_TOOLS=1`. The snapshot was built from an observed session's tool-registration record, not a codex capture. Until a real deferred-load capture validates the schema, the flag must stay off.
- Reasoning replay (`chat_reasoning.py`). The isolated Chat compatibility pilot now applies the table in both request and response translation. Production Baseten routing still uses Responses, so no live route activates it.

**Speculative, isolated, lowest cost to carry:**

- Chat-compat image strip-to-notice (`533502c`). Touches only `experiments/chat_compat.py`, which is not wired to production.
- Tool-result aging estimator (`tool_result_aging.py`). Read-only advisory with an explicit `scripts/audit-context.py --tool-result-aging` mode. The proven 86% figure is a benchmark best case, not a workload expectation.

## Root issue

Not a bug. The issue is that evaluation produced code faster than the evidence that would justify activating it, and the work accumulated on a branch with no merge gate. The risk is that "implemented" reads as "adopted" when most of it is unproven against live traffic.

## What should happen to each piece

The merge question and the activation question are separate. Merging to `main` is safe — it is a fast-forward, everything is tested, and nothing self-activates. Activation is the decision that needs discipline.

## Execution checklist

- [x] Establish that origin/main is an ancestor and the stack fast-forwards cleanly.
- [x] Sort the ten adoption features by evidence of need.
- [x] Identify which features are inert behind flags and which are live.
- [x] Create one Linear issue: merge the adoption stack to main as inactive code ([SAY-3380](https://linear.app/sayvant/issue/SAY-3380)).
- [x] Create one Linear issue: validate the app-tool snapshot against a real deferred-load capture before any enablement ([SAY-3382](https://linear.app/sayvant/issue/SAY-3382)).
- [x] Create one Linear issue: promote the tested Chat compatibility pilot into routing only when a concrete Chat-only thinking model requires it; keep the model-family replay contract at that boundary ([SAY-3381](https://linear.app/sayvant/issue/SAY-3381)).
- [x] Close out SAY-3370–3379 with implementation evidence and corrected scope; production promotion and real-capture validation remain in SAY-3381 and SAY-3382.
- [x] Keep every optional environment flag disabled in the shared runtime during this work.
