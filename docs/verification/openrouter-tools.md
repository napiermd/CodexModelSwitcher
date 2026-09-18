# OpenRouter tool routing, September 18, 2026

Fable 5.1's published endpoints advertise tools but omit tool-choice and parallel-tool parameters. Live synthetic requests with `require_parameters: true` returned HTTP 404 when tools were combined with either `tool_choice: auto` or `parallel_tool_calls: false`. The same model accepted tools without those optional parameters. Normal endpoint selection accepted both settings and completed a tool call and its streamed result continuation.

The patch uses `require_parameters: false` for inference and saved-connection verification. It preserves the exact model and request parameters. OpenRouter may ignore parameters unsupported by an endpoint; this does not establish enforcement of every tool constraint.

The mainline source bridge and the candidate based on the installed gateway source both passed `scripts/verify-openrouter-tools.py --live`. The unchanged installed gateway failed the same check with HTTP 404. The candidate was built with the existing Apple Development signing identity and staged under `build/staged-updates/openrouter-tool-routing-20260918`.

After explicit activation approval, the coordinated maintenance controller completed in `resumed` state. Runtime `4feebf923e5b0aa7048b92c7c0ccad6746cd4e9815a7f034e5a2a3200d4f714e` became active; all five captured processes resumed. Shared Codex files matched their pre-activation hashes. The old runtime remains retained for rollback.

`scripts/verify-openrouter-tools.py --live --installed` then passed both the Fable 5.1 tool call and streamed result continuation through port 48118. The mainline suite passed 141 Python tests; the gateway candidate passed 412. The signed candidate's nine Python modules matched the committed source.

A staged app, a GitHub merge, or an interface restart alone does not change the retained running gateway. This activation used coordinated maintenance and does not establish automatic rolling updates.

## Connection status after idle time

The interface now treats a successful route check as connection evidence for the same gateway boot and account configuration while credentials remain loaded. Expiration of the five-minute readiness proof no longer disconnects the provider tab or reduces the connection count. Strict readiness expiration remains unchanged in the gateway. Authentication failures, replaced account configuration, a new gateway boot, or removed credentials invalidate the interface evidence.

When attaching to the independent gateway, Harbor checks Grok's existing sign-in without saving credentials, regenerating catalogs, or changing task defaults. The status no longer stays unchecked merely because the interface restarted.
