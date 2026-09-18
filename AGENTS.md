# Model Harbor development and update safety

## Protect running tasks

Read [docs/safe-updates.md](docs/safe-updates.md) before changing installation, runtime lifecycle, routing, or shared Codex configuration.

- Build and test changes in isolation. Treat a request to fix or update Harbor as authorization to prepare and verify the change, not evidence that running tasks can tolerate a gateway outage.
- Determine ownership from the authenticated running gateway. A verified independent gateway survives interface exit; replacing only the signed interface is allowed after preserving a rollback copy and verifying unchanged gateway identity and configuration. A legacy UI-owned bridge must remain running during active work. Never terminate or replace the inference gateway on the basis of zero active HTTP requests: agents run tools between requests.
- Until a tested runtime handoff exists, stage updates. A disruptive installation requires an explicitly coordinated maintenance window after the user has paused or finished affected work. Do not poll for a momentary zero counter and kill the process.
- Use separate ports, state directories, and synthetic conversations for candidate runtimes and gateway pilots. Never edit live conversation history or global routes for a test.
- A listening process is not provider readiness. Required provider readiness must be true and the candidate must pass a real route check before it can receive user traffic.
- Report staging, installation, provider verification, and interruption-free update verification separately. Do not claim seamless updates from a successful request or unit test alone.
