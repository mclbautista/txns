# PROTOTYPE (throwaway): ticket 08 config and CLI shape
Open `index.html` by double-click. Pure module `Txns` in the script is the liftable part; the page is a shell.

Proposed shape being tested:
- Commands: `txns author [--corrections FILE] [--label NAME]`, `txns approve [BUNDLE]`, `txns generate [--config txns.toml] [--seed N] [--bundle LABEL-HASH]`.
- Config (`txns.toml`): bundle (default latest), start/end (default `auto` = latest full quarter), target, band_pct, optional target_rows, tier, tolerance_pct, seed (blank = draw and record), out. Everything except out/log is hashed into run identity.
- Exit codes: 0 ok, 1 scorecard hard fail (CSV still written), 2 missing input/bundle, 3 LLM unreachable, 4 bundle validation failed, 5 gap rules cannot hold, 6 target/band/rows unsatisfiable.
- Quarter reuse: the config never changes; only the calendar and drawn seed do.
