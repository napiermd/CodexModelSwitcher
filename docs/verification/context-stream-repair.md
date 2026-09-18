# Context and stream repair, 2026-09-18

Candidate branch: `codex/context-stream-fix`, based on installed integration source `b72ad38`.

- Native Sol metadata observed: default 272,000; maximum 872,000.
- Live Azure and merged catalogs corrected and reread successfully.
- Backup: `/Users/andrewn/.codex/model-catalogs/context-repair-backup-20260918-135650`.
- Integrated tests: 103 Swift and 421 Python passed.
- Additional real HTTP Azure EOF regression: 12 deadline/EOF tests passed. Partial output is preserved, failure is explicit, uncertain delivery is retained, a repeated turn is rejected, and only one upstream POST occurs.
- Signed Xcode build succeeded using the existing Apple Development identity. Strict signature verification passed.
- Stage: `build/staged-updates/context-stream-20260918/Model Harbor.app`; manifest is beside it.
- Backend activation: pending coordinated maintenance. No installed runtime or desktop process was replaced, paused, or terminated.
- Desktop catalog reload: unverified. On-disk correction does not prove the loaded task adopted new limits.
- Live provider verification of this candidate: not yet run. Local tests use synthetic providers.

The screenshot proves a stream ended without completion. It does not identify whether the cause was an upstream disconnect, a deadline, or a bridge exception. This repair fixes missing error signaling; it does not claim to eliminate provider outages.

See `../codex-router-comparison.md` for the source comparison and remaining work.
