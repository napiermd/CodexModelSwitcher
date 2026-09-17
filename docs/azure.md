# Azure OpenAI

Model Harbor can route named Azure deployments through the same local bridge as your other providers. Each Codex task keeps its own model choice.

## Connect

1. Open **Add provider → Azure → Connect** in Harbor.
2. Enter your resource endpoint, such as `https://your-resource.openai.azure.com/openai/v1/`. Direct `services.ai.azure.com` endpoints are also accepted. Redirects, URL credentials, custom ports, query strings, and non-Azure hosts are rejected.
3. Enter the resource API key in the secure field. It is saved in macOS Keychain and held in memory by the local bridge while Harbor is running.
4. Click **Find deployments** to check the key and load ready deployments from your resource. Choose one from the picker, or enter its exact name manually if the resource does not expose discovery. This can differ from the underlying model name. Harbor lists deployments, not the separate catalog of base models. Discovery does not create deployments or run inference.
5. In **Deployment options**, select a reasoning setting, enable image input if supported, and set a context token limit from the deployed model's specifications. The conservative default is 128,000 tokens; it is not a discovered capacity. Deployment default omits reasoning controls from requests.
6. Choose whether to use this deployment for new tasks and hide Baseten in Harbor.
7. Click **Verify & add deployment**. This sends one small, billable Responses request and requires a completed function call. When image input is enabled, the check also includes a tiny test image. A failed check does not save the new connection. The form shows elapsed time and a Cancel action; discovery stops after 20 seconds and verification after 60 seconds.
8. Reopen Codex once to load newly added catalog entries. Select the named Azure deployment in an existing Harbor task to move that task to Azure. Existing choices are never migrated automatically.

Add more deployments using **Manage connection → Add or update deployment**. Enter an existing deployment name to update its options. A connection is pinned to one Azure resource; additional resources can use the separate custom-provider workflow.

Only the reasoning setting selected during setup is advertised for that deployment. Harbor applies that verified setting even if an older task inherits a different effort. To change it, update the deployment options and verify again. This avoids advertising unsupported effort levels. The connection check is a basic protocol check, not proof of every Codex tool or workload.

## Visibility and defaults

**Settings → Providers → Visible providers** controls the connection tabs and usage picker. Hiding Baseten preserves its key and existing task routes. Choosing Azure as the new-task default affects future tasks; it does not change tasks already using Baseten.

## Connection and performance

Azure uses `/openai/v1/responses` with the resource key in `api-key`. Harbor never forwards the Codex subscription token to Azure, never substitutes another model, and does not place Azure requests in the Baseten queue. Namespaces and custom tools are translated to function tools. Tool-result images retain their original data and call IDs and are moved into a labeled following message.

Azure still applies deployment quotas and capacity limits. HTTP 429 is returned with available retry headers; partial streams are never replayed by Harbor. The new provider does not guarantee lower latency. Test your actual workload and inspect Azure's deployment metrics when evaluating performance.

Azure inference keys do not provide a Harbor billing integration. Usage shows that Azure quota and spend are unavailable and links to the Azure dashboard. Use Azure Cost Management for billed charges.

## Troubleshooting

- **401/403:** verify the key belongs to the resource and that network access rules allow this Mac. Reconnect after correcting the problem.
- **404:** check the exact deployment name and endpoint.
- **400:** confirm Responses, tool calls, the selected effort, and image input are supported. Try Deployment default and disable image input to isolate a capability mismatch.
- **429:** inspect the deployment's quota and available capacity; wait for the provider cooldown.
- **Incomplete tool check:** choose a lower effort or a deployment that completes the tool check within its output budget.
- **Bridge cannot accept Azure settings:** finish active requests and reopen the updated Harbor app once. An older running bridge cannot load new provider code until restarted.

## References

- [Microsoft: Azure OpenAI v1 API](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle)
- [OpenAI: Codex configuration reference](https://developers.openai.com/codex/config-reference/)
