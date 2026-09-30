---
type: wayfinder:grilling (HITL)
status: done (grilled with Cyril 2026-09-30)
---
## Question
Story 3 wants data that defeats simple anomaly detection while stories above want recognisable business rhythm. What target does "patternless" really mean (no trivially detectable generator fingerprints, natural variance) and which structure must stay? Decide which anomaly checks the output must survive.

## Decision
- **Pass bar:** ledger-relative where the ledgers give a reference; absolute limits where they don't (gap rules from ticket 04, price and quantity rules from ticket 05). Realism wins on conflict.
- **Seven anomaly checks (the "anomaly section" of the scorecard):**
  1. Duplicates: same date, item and amount rows only at the real-ledger rate.
  2. Round-amount share of row amounts (plug-row hunt; ticket 05 rules apply).
  3. Benford first-digit test on row amounts. Report only, never fails.
  4. Per-item outliers (z-score or IQR on amount and unit price).
  5. Interval regularity per item: not metronomic, not clumped.
  6. Weekday, holiday and month-end shape versus the ledger.
  7. Distinct unit prices and quantities per item versus the ledger.
- **Tolerance:** one config value. Pass within +-25% of the ledger figure, warn to +-50%, fail beyond. No per-check hand tuning. Checks with no ledger reference use the absolute rules from tickets 04 and 05.
- **Protected structure** (never loosened by a correction): rate-card prices, pack-size quantities, subscription anchor days, minimum gaps, round figures only on approved big-ticket items. Corrections change parameters, weights and rate-card points, never add continuous randomness to price or quantity, never break a gap rule.
- **Generator fingerprints designed out:**
  - Tight total band every run: the band stays a config value and can be widened.
  - Identical monthly row counts across seeds: counts vary by seed within a ledger-derived spread.
  - Perfect date sorting: output in ledger entry order, with rare small back-dated rows.
  - Closing rows always petty or top-up: closing draws from a wider set of small ordinary occurrences.
  - Uniform jitter is already covered by the closed menu of named distributions (ticket 04).
- **Measurement:** metric bands only. No classifier test.
- **Reference data:** `author` derives a `reference.json` of summary statistics from Cyril's ledgers into the bundle. The raw ledgers live in `inputs/ledgers/` in this private repo (Cyril, 2026-09-30; ADR 0005, supersedes the earlier "never committed" rule). `generate` never reads them; bundles carry only `reference.json`.
- **Failure behaviour:** `generate` always writes the CSV and prints the scorecard (pass / warn / fail). It exits non-zero on a hard failure. Hard set: duplicates, plug rows, price stability, gap rules. Everything else, Benford included, only warns.
- **Dropped:** a cross-seed comparison command (not needed).
