---
type: wayfinder:grilling (HITL)
status: done (grilled with Cyril 2026-09-30)
---
## Question
What is the single command and reusable config (stories 1, 6, 8)? Decide the config file contents (seed, period, volume, catalog set), quarter-to-quarter reuse, unattended-run behaviour and failure modes (e.g. LLM unreachable).

## Progress
- Prototype at `prototypes/08-config-cli/` (throwaway logic demo) proposes commands `author`, `approve`, `generate`, one TOML config, and distinct exit codes. Its `--corrections` flag is superseded by the decision below.
- **Spot-check and corrections (Cyril, 2026-09-30):** the human reviewer checks the output CSV against the bundle, then discusses updates with Claude; learning from corrections is a GitHub ticket. So the CLI has no corrections input. See ticket 07.
- **`txns approve` kept (Cyril):** Cyril may edit a bundle by hand; `approve` then marks it `reviewed: true`. `generate` still warns on unreviewed bundles but runs.
- **Exit codes accepted as prototyped (Cyril):** 0 ok, 1 scorecard hard fail (CSV still written), 2 missing input or bundle, 3 LLM unreachable, 4 bundle validation failed, 5 gap rules cannot hold, 6 target, band or row count unsatisfiable.
- **Final shape:** commands `author`, `approve`, `generate`; one TOML config (bundle, start/end default `auto` = latest full quarter, target, band_pct, optional target_rows, tier, tolerance_pct, seed blank = draw and record, out); run identity = seed + bundle hash + config hash (out and logging excluded).
