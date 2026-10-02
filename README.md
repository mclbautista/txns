# txns

Realistic, deterministic spend-ledger CSV generator for load tests. Design: `CONTEXT.md`, `docs/adr/`, spec in GitHub issue #7.

## Install and run

```sh
uv tool install .        # or: pipx install .   (development: pip install -e .)
txns generate            # reads txns.toml, uses the latest bundle in ./bundles
txns generate --seed 42 --bundle <label>-<hash12> --config txns.toml
```

A new bundle comes from `txns author` (needs `OPENROUTER_API_KEY` and `author.model` in `txns.toml`):

```sh
txns author --label v1   # ledgers -> scrubbed payload -> LLM drafts -> gates -> bundles/v1-<hash12>/ (unreviewed)
```

After checking (or hand-editing) it, mark it reviewed; no API key or network needed, but the ledgers must be present for the leak check:

```sh
txns approve             # latest bundle; or: txns approve <label>-<hash12>
```

`approve` re-runs the offline gates. An unedited bundle gets `reviewed: true` in place (the flag is outside the hash); a hand-edited one is written as a new `bundles/<label>-<newhash12>/` (reviewed, now the latest) and the edited original is left as it was.

Prices come only from the ledgers' anchors and the committed `inputs/price-anchors.json`; rule defaults from the committed `inputs/bundle-rules.json`. Both are plain JSON to review and edit.

Output: `out/txns-<period>-<runid6>.csv` plus `.run.json`. Exit codes: 0 ok, 1 scorecard hard failure (CSV written), 2 missing input or bundle, 3 LLM unreachable, 4 bundle validation failed (including a row that cannot be re-drawn up to the minimum amount), 5 gap rules cannot hold, 6 target, band or row count unsatisfiable.

Volume and hygiene (`[calibration]` in `txns.toml`): a quarter lands at 300 to 400 rows for the ₱4M target, no row under ₱500 (items that can never reach it are left out with a warning), and item text matching `denied_item_patterns` in `inputs/bundle-rules.json` (such as "Out fee") is never drawn. `generate` warns about any row that still breaks those rules; `approve` and `author` refuse the bundle. `[multipliers.archetype]` makes an archetype's items occur more or less often, for example more big-ticket purchases when the row band leaves the total short.

## Tests

```sh
python3.12 -m unittest          # from the repo root; standard library only
```
