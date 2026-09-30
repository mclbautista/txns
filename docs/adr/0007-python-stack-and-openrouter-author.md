# 7. Python stack, OpenRouter for `author`

Status: accepted (Cyril, 2026-09-30, ticket 09)

- **Runtime:** Python 3.12+, stdlib only for `generate`. Own small PRNG (SplitMix64 or PCG) instead of `random`, so sub-streams never shift with an interpreter upgrade. Money is `int` centavos; no floats touch amounts.
- **Pinning:** lockfile and `.python-version` committed; `run.json` records interpreter and generator version; `generate` warns when the interpreter differs from the one recorded for the bundle.
- **Install:** `txns` console script via `pipx` or `uv tool install` from the private repo; `pip install -e .` for development.
- **LLM:** OpenRouter API, one pinned Sonnet-class slug in `author.model`, no fallback models. Bundle manifest records the pinned slug and the model OpenRouter reports per response. Provider preference denies data collection. Key only in `OPENROUTER_API_KEY`, never in config, bundle or `run.json`.
- **Payload:** aggregates and item-text patterns only. A deterministic scrubber replaces every name not in the committed brand allowlist (`inputs/brands-allowlist.txt`) with a fabricated one (stable real-to-fake map, kept local). A leak check blocks the call if a non-allowlisted ledger name survives. The scrubbed payload is written to the temp folder for inspection.
- **Failures:** network errors and rate limits retry 3 times with backoff. A schema-invalid response gets one re-ask, then exit 4. Partial drafts persist in the temp folder keyed by payload hash so a re-run resumes.
- **Cost:** about 15-25 calls per run, expected well under US$2 (an expectation, not a guarantee). `author.max_cost_usd` (default 5), enforced from per-response cost, stops the run and keeps partial drafts.
