# txns

Realistic, deterministic spend-ledger CSV generator for load tests. Design: `CONTEXT.md`, `docs/adr/`, spec in GitHub issue #7.

## Install and run

```sh
uv tool install .        # or: pipx install .   (development: pip install -e .)
txns generate            # reads txns.toml, uses the latest bundle in ./bundles
txns generate --seed 42 --bundle <label>-<hash12> --config txns.toml
```

Output: `out/txns-<period>-<runid6>.csv` plus `.run.json`. Exit codes: 0 ok, 1 scorecard hard failure (CSV written), 2 missing input or bundle, 3 LLM unreachable, 4 bundle validation failed, 5 gap rules cannot hold, 6 target, band or row count unsatisfiable.

## Tests

```sh
python3.12 -m unittest          # from the repo root; standard library only
```
