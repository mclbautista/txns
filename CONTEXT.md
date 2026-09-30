# Txns: domain context

A spec (not yet built) for a one-command tool that generates realistic, human-looking, high-volume ledger CSVs to load-test a post-production facility's financial review system. Design detail lives in `wayfinder/MAP.md` and `wayfinder/tickets/`.

## Glossary

- **Ledger**: spend-only account transactions of a Philippine post-production facility (currency PHP). Rows paying a person are excluded; sold services are not in the catalog.
- **Output CSV**: four columns only, `date, qty, unit_price, item`. No blank item cells; rows without real item text get an invented item (never vendor-only). Sidecar `run.json` holds everything else.
- **Bundle**: immutable, content-hashed folder `bundles/<label>-<hash>/` holding catalog, rate cards, vocabulary, archetype assignments, holiday calendar, declarative rules and `reference.json`. Starts `reviewed: false` until `approve`.
- **`author`**: the only command that calls an LLM. Drafts catalog, item variants (including invented items for text-less rows) and vocabulary; validates in a temp folder before promoting a bundle.
- **`generate`**: pure function of seed + bundle + config. No LLM, no network. Byte-identical per seed on the same machine. The quarterly routine is `generate` alone.
- **`approve`**: marks a hand-edited bundle `reviewed: true`.
- **Run identity**: seed + bundle hash + config hash (output path and logging excluded).
- **Storyline**: named spending theme (for example parties, subscription stack) with month weights, burst length and bursts per quarter.
- **Archetype**: one of seven timing patterns: fixed-day subscription, project burst, periodic top-up, petty daily, deposit-then-balance pair, one-off big ticket, batch-logged.
- **Rate card**: fixed price points per item. Never random cents; one tidy price step per item per year.
- **Tier factor**: independent 0.5 / mid 1.0 / high-end 2.5; scales occurrences and quantities, never unit prices.
- **Plug row**: a row that exists only to hit the total. Forbidden; the total is reached by calibrating the occurrence plan.
- **Scorecard**: pass/warn/fail metric report printed by `generate`; one tolerance (pass ±25%, warn ±50%). Hard failures: duplicates, plug rows, price stability, gap rules.
- **`reference.json`**: summary statistics derived from the ledgers, stored in the bundle.
- **Spot-check**: a human compares the output CSV with its bundle, then raises a GitHub ticket; Claude resolves it by changing the repo. There is no corrections file or flag.
