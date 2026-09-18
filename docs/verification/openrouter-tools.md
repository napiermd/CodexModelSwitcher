# OpenRouter tool routing, September 18, 2026

Fable 5.1's published endpoints advertise tools but omit tool-choice and parallel-tool parameters. Live synthetic requests with `require_parameters: true` returned HTTP 404 when tools were combined with either `tool_choice: auto` or `parallel_tool_calls: false`. The same model accepted tools without those optional parameters. Normal endpoint selection accepted both settings and completed a tool call and its streamed result continuation.

The patch uses `require_parameters: false` for inference and saved-connection verification. It preserves the exact model and request parameters. OpenRouter may ignore parameters unsupported by an endpoint; this does not establish enforcement of every tool constraint.

The mainline source bridge and the candidate based on the installed gateway source both passed `scripts/verify-openrouter-tools.py --live`. The unchanged installed gateway failed the same check with HTTP 404. The candidate was built with the existing Apple Development signing identity and staged under `build/staged-updates/openrouter-tool-routing-20260918`.

Activation requires the coordinated maintenance controller. A staged app, a GitHub merge, or an interface restart does not change the retained running gateway.
