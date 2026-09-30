---
type: wayfinder:grilling (HITL)
status: done (grilled with Cyril 2026-09-30; ADR 0007)
---
## Question
Which implementation language, LLM provider and model does the spec commit to, and what happens offline or on failure? Decide:
- Language and runtime for `author`, `approve` and `generate` (constraint: byte-identical output per seed on the same machine, integer centavos, no network in `generate`).
- LLM provider and model for `author` only, and how its output is validated before a bundle is promoted.
- Cost expectations per `author` run and what happens when the LLM is unreachable (exit code 3 already reserved in ticket 08).
- Where the ledgers live: `inputs/ledgers/` in this private repo (Cyril, 2026-09-30); `author` derives `reference.json` from them.

## Context
- Ticket 03: LLM at authoring time only; `generate` needs no network.
- Ticket 08: exit codes 0-6.
- Listed under "Not yet specified" in MAP.md.

## Decisions (Cyril, 2026-09-30)
- **Language:** Python 3.12+, stdlib only for `generate`; own PRNG (SplitMix64 or PCG); `int` centavos. Lockfile and `.python-version` committed; `generate` warns on an interpreter differing from the one recorded for the bundle. Installed as a `txns` console script (`pipx` / `uv tool install`).
- **LLM:** OpenRouter, one pinned Sonnet-class slug (`author.model`), no fallbacks, provider set to deny data collection. Slug and reported model recorded in the bundle manifest. Key in `OPENROUTER_API_KEY` only.
- **Payload privacy:** aggregates and item patterns only. Names on the committed allowlist (`inputs/brands-allowlist.txt`, generic brands such as Grab, Lazada, Shopee) pass; every other name is replaced by a stable fabricated one. Leak check blocks the call; scrubbed payload is written to the temp folder.
- **Promotion gate (all automatic, in the temp folder):** responses match JSON schema; anchor prices and pack sizes within tolerance of `reference.json` (LLM never invents anchors); no scrubbed real name anywhere in the bundle; no duplicate items or blank text (ADR 0006); smoke `generate` on a fixed seed with no hard scorecard failures.
- **Cost:** about 15-25 calls per run, expected well under US$2 (expectation only). `author.max_cost_usd` default 5, enforced from per-response cost; hitting it stops the run, keeps partial drafts, exits 3.
- **Failure handling:** network errors and rate limits retry 3 times with exponential backoff, then exit 3. Schema-invalid response: one re-ask with the validation error, then exit 4. Partial drafts persist keyed by payload hash, so a re-run resumes. `generate` is unaffected offline.
- **Ledger input:** strict parser for the Xero-style export (title lines, header row, category headings, multi-line descriptions, section and grand totals); category headings become `author` category labels. Read from `inputs/ledgers/*.csv` (`author.ledgers_dir`). `reference.json` is re-derived each run and is part of the bundle hash; the manifest records ledger SHA-256s, informational only.
- **Exit codes (0-6 unchanged from ticket 08):** 2 also covers missing key, missing or unreadable ledgers, unrecognised layout, non-reconciling section total. 3 also covers retired model slug and cost cap reached. 4 also covers a schema-invalid response after the re-ask and any promotion-gate failure.
