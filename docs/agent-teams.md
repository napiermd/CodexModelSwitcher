# Mixed-model coding teams

[← Model Harbor](../README.md)

Use Kimi K3 to plan a feature, give three coding models separate assignments, and review their combined changes in the same coordinating Codex task. Model Harbor routes each request using the worker’s fixed model ID. Changing a model in another task does not change these workers.

## The fixed roles

| Role | Baseten model | Verified Responses setting | Permissions |
| --- | --- | --- | --- |
| `harbor_kimi_architect` | `moonshotai/Kimi-K3` | `xhigh` | Read-only planning |
| `harbor_glm_coder` | `zai-org/GLM-5.3` | `xhigh` | Write in its worktree |
| `harbor_deepseek_coder` | `deepseek-ai/DeepSeek-V4-Pro-0813` | `xhigh` | Write in its worktree |
| `harbor_kimi_coder` | `moonshotai/Kimi-K2.7-Code` | Thinking enabled | Write in its worktree |
| `harbor_reviewer` | `moonshotai/Kimi-K3` | `xhigh` | Independent read-only review |

Each role pins `model_provider = "model-harbor"` and the full route, such as `harbor/baseten/zai-org/GLM-5.3`. There is no automatic fallback to another model.

Live checks on September 16, 2026 completed Responses function calls with the exact Kimi K3, GLM 5.3, and DeepSeek Pro model IDs and `xhigh` in their responses. Literal `max` was rejected by the Kimi K3 and DeepSeek Pro Responses endpoints. This differs from Baseten’s Chat Completions documentation. Harbor preserves `xhigh` and rejects `max`/`ultra` instead of silently downgrading them. These are verified request and response settings; they do not independently measure the provider’s internal compute.

Kimi K2.7 Code documents a thinking toggle, without a reasoning-depth control. Its role uses `high` as Harbor’s local thinking-enabled choice. The bridge sends `chat_template_args.enable_thinking = true` and removes the depth field. A live reasoning check reported 400 reasoning tokens and the correct result. Do not call this maximum reasoning.

The shared definitions are in [`baseten-models.json`](../ModelHarbor/Support/baseten-models.json). Catalog updates preserve existing selections and custom Baseten entries. They require an existing Baseten connection; installing roles does not create credentials or grant provider access.

## Native subagents and isolated workers

Codex CLI 0.150.1 was verified to discover personal roles in `~/.codex/agents/` and trusted project roles in `.codex/agents/`. Their model, provider, and effort override inherited choices. Native subagents inherit the parent’s working directory: their launch tool has no separate worktree argument. Instructions to change directory alone are not enforced filesystem isolation. Their accepted `sandbox_mode` field can also be superseded by inherited runtime permissions.

For concurrent writers, use the bundled **worktree launcher**. It starts one headless Codex process per worker, with its own working copy and the appropriate Codex sandbox. The existing desktop task remains the coordinator and collects each worker’s results. The launcher reads a fresh catalog on every run, so it does not require restarting the open Codex app. The current desktop subagent tool does not expose custom `agent_type` selection; the launcher provides the reliable path for this team.

This separates working copies and writer locks, not machines. Normal Codex sandbox and approval restrictions still apply. The workers share Git object storage, use the local Harbor bridge, and consume your Baseten account’s usage.

## Install the roles

Keep the updated Model Harbor app running, with Baseten connected. Install Codex CLI on `PATH`.

From the repository:

```sh
python3 scripts/harbor-team.py install
```

Or from the installed app:

```sh
python3 "/Applications/Model Harbor.app/Contents/Resources/harbor_team.py" install
```

For one project instead of personal roles:

```sh
python3 scripts/harbor-team.py install --project /absolute/path/to/project
```

Codex must trust the project before it discovers project roles. Installation refuses to overwrite an unrelated role file. Changed Harbor-owned roles receive a backup. Each generated role pins the local Harbor catalog; do not commit personal absolute catalog paths to a public repository.

## Prepare and run a team

Start from a clean, committed checkout. The output directory must be new and outside the source checkout:

```sh
python3 scripts/harbor-team.py prepare \
  --repo /absolute/path/to/project \
  --output /absolute/path/to/team-run
```

This creates three coding worktrees and one integration worktree, all at the same recorded commit. Each has a separate branch. An incomplete preparation retains its recovery manifest and any worktrees already created.

Write a bounded assignment to a text file. Include the owned files, expected behavior, and acceptance checks. First run the architect:

```sh
python3 scripts/harbor-team.py run \
  --team /absolute/path/to/team-run/team.json \
  --role harbor_kimi_architect \
  --prompt-file /absolute/path/to/architecture-task.txt
```

Then launch the three coding roles concurrently, using a different assignment file for each:

```sh
python3 scripts/harbor-team.py run \
  --team /absolute/path/to/team-run/team.json \
  --role harbor_glm_coder \
  --prompt-file /absolute/path/to/glm-task.txt \
  --timeout 1800
```

Repeat that command concurrently for `harbor_deepseek_coder` and `harbor_kimi_coder`. A second process targeting the same worktree fails immediately. Different models can run in parallel; Harbor’s existing per-model pacing and bounded retries still apply.

The coordinating task waits for all workers, inspects their changes, runs independent checks, and applies accepted changes to the integration worktree. Use `harbor_reviewer` against that worktree for an independent review, then run the project’s combined checks before merging. The architect proposes integration steps in read-only mode; the coordinating task performs the actual integration. Nothing merges, pushes, or deploys automatically.

## Read the results

Each run leaves a private directory under `team-run/runs/`, including `result.json`, Codex events, stderr, `changes.patch`, `changes.json`, and worktree status. Untracked files receive private snapshots with hashes under `untracked-files/`; symlink targets are recorded without dereferencing. `changes.json` reports any incomplete capture. Originals remain in the worker’s working copy; inspect them as part of review. Worktrees and artifacts survive errors, timeout, and cancellation.

`completed` means Codex exited successfully and emitted `turn.completed`. It does **not** mean the generated patch passed your acceptance checks. Requested model and effort are always recorded. Observed values are recorded only when the CLI emits them; otherwise they remain `null`. Any observed substitution fails the run. A timeout limits runtime, not spending.

Workers receive a temporary Codex configuration with one model, the local Harbor credential, and no unrelated user MCP configuration. Project configuration is untrusted in that temporary home, so a project cannot redirect the provider or add MCP servers to the worker. Repository instruction files still apply. The launcher does not load an upstream API key or call 1Password. The bridge retains its existing credential cache, so worker requests reuse the current Harbor session.

## Instruction for your coordinating task

> Use the Model Harbor team. Have Kimi K3 architect this feature. Delegate independent implementation to GLM 5.3, DeepSeek V4 Pro 0813, and Kimi K2.7 Code using their fixed verified settings. Give writers separate worktrees, wait for their results, review the combined changes independently, and integrate only changes that pass the checks. Report errors or substitutions. Keep coordinating in this task.

This workflow can use a Kimi parent task or another parent model. Parent-model selection does not control worker models.

## References

- [Codex custom subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents)
- [Baseten reasoning parameters](https://docs.baseten.co/inference/model-apis/reasoning)
