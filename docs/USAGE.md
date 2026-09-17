# Usage and spend

Open **Usage & spend** in the Model Harbor panel. Select a provider and, when
available, an account. Changing this selection only changes the usage view.
It does not switch the account or model serving a Codex task.

## What each number measures

| Provider / view | Source and scope | Available information |
| --- | --- | --- |
| Codex accounts | Codex account usage, using the current sign-in and already unlocked saved accounts | Remaining quota, reset times, plan, credits when supplied |
| Baseten | Baseten management billing API, entire organization, all Model API keys | Today and rolling 30-day USD costs, daily chart, token totals when permitted |
| Grok | Grok CLI account billing, current OAuth account | Plan quota and reset time |
| OpenRouter | OpenRouter current-key API | Today's spend, calendar-month spend, remaining key budget when supplied |
| Local history | CodexBar's existing widget snapshot on this Mac | Token totals and API-price value estimates; all local accounts together |
| Claude | CodexBar's cached selected-account quota | Quota and resets when present; account identity is not supplied by the cache |

**Token value estimates are not subscription bills or additional charges.**
Harbor does not add them to API spend. Baseten's current UTC day is partial;
provider-reported costs can differ from the final invoice. Its cost API has
history from August 5, 2026. OpenRouter's monthly total uses its calendar billing
period, not a rolling 30-day window. Missing information says unavailable.

## Refresh and account access

Usage refreshes every five minutes while Harbor is open. Manual refreshes are
limited to once per minute. API billing failures back off for at least five
minutes, with longer waits for reported rate limits. Old successful readings
remain labeled stale. Incomplete billing pagination never becomes an exact
total. Changing API keys discards the prior key's cached totals.

Usage reads never invoke 1Password, read browser cookies, rotate OAuth refresh
tokens, make inference calls, or switch the active Codex account. Baseten uses
its already unlocked in-memory credential. Connect it once in **Connections**
after launching Harbor. Usage cannot read organization billing unless that key
has permission. A denied request is shown as an access error.

Saved Codex sign-ins that have expired need renewal through Settings → Providers.
The active Codex client owns refresh of its current sign-in. Harbor does not
compete with it for refresh-token rotation.

## Settings and menu bar

In Settings → Menu bar, choose **Provider + quota remaining**, **Provider + today's
spend**, or **Provider + next reset**. These follow the provider selected in
Connections and the account selected in its Usage view. Quota mode shows the
lowest remaining percentage among the selected account's reported windows.
Stale or missing readings display unavailable.

Use **Connect CodexBar history…** in the same settings view and select
`widget-snapshot.json`. The file picker grants read access to that file, which
macOS otherwise protects inside CodexBar’s container. Harbor remembers that
permission. You can disable automatic refresh or cached history at any time.
CodexBar is optional: direct Codex, Grok, Baseten, and OpenRouter reads work
without it. Claude monitoring and local token-value history currently require
an existing CodexBar snapshot. Refresh those sources in CodexBar; Harbor does
not launch it or run its credential-fetching CLI.

## Sources and implementation

- [Baseten costs](https://docs.baseten.co/reference/management-api/billing/gets-model-apis-costs)
- [Baseten token usage](https://docs.baseten.co/reference/management-api/model-apis/gets-model-apis-token-usage)
- [CodexBar](https://github.com/steipete/CodexBar), MIT; see [NOTICE](../NOTICE.md).
- `ModelHarbor/Support/provider_usage.py`: API billing readers and throttled cache.
- `ModelHarbor/Usage.swift`: account usage, source labels, CodexBar cache import.
- `ModelHarbor/UsageView.swift`: quota rows, history, freshness, and account selection.

Raw keys, OAuth tokens, prompts, and tool results are never written to a usage
cache. Usage snapshots live in Harbor memory; providers own billing history,
and CodexBar owns its existing on-disk history. A future native local ledger
can replace that optional dependency without changing the account/API scopes.
