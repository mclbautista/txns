---
type: wayfinder:grilling (HITL)
status: open, blocked by 03
---
## Question
What is the single command and reusable config (stories 1, 6, 8)? Decide the config file contents (seed, period, volume, catalog set), quarter-to-quarter reuse, unattended-run behaviour and failure modes (e.g. LLM unreachable).

## Progress
- Prototype at `prototypes/08-config-cli/` (throwaway logic demo) proposes commands `author`, `approve`, `generate`, one TOML config, and distinct exit codes. Its `--corrections` flag is superseded by the decision below.
- **Spot-check and corrections (Cyril, 2026-09-30):** the human reviewer checks the output CSV against the bundle, then discusses updates with Claude; learning from corrections is a GitHub ticket. So the CLI has no corrections input. See ticket 07.
- Still open: whether to keep `approve`, and whether exit codes stay distinct.
