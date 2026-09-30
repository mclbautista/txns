---
label: wayfinder:map
title: Realistic post-production transaction generator
tracker: local-markdown (no repo/issue tracker attached; tickets are files in ./tickets)
---

## Destination

A **design spec** (architecture + data model + decisions) for a one-command, seed-deterministic tool that emits a realistic, human-looking, high-volume ledger CSV (`date, qty, unit_price, item/service`, total targeted by config, currently ₱4M per quarter) for load-testing a post-production facility's financial review system. Deterministic code does the generating; LLM calls only make the inputs (catalogs, rate cards, vocabulary, corrections) more realistic. Ends when someone can hand the spec to an implementer with nothing left to decide.

> Confirmed by Cyril (ticket 01, 2026-09-30): the destination is **spec only**. "≥ ₱4M" is a configurable target, not a constant: the current need is about ₱4M per quarter and it may change, so the spec must not hardcode it. "Spot-check" means a brief human look at a sample of rows.

## Notes

- Domain: Philippine post-production facility (currency ₱), per the ₱4M target. Services: offline/online edit, conform, colour, restoration scan, archive verification, freelance editors, stage hire; stock: LTO tape, optical media.
- **Negative example** (`negative-sample.csv`, don't reproduce): 12 rows totalling exactly ₱4,005,000.00. Observed faults:
  1. Same item type clustered on adjacent days (three Freelance Editor rows, only 2 distinct dates per month for whole categories); no recurrence rhythm.
  2. Unit price for the same item varies wildly with random cents (Freelance Editor 73,675.41 vs 77,054.63; Restoration Scan 24,999.73 vs 27,016.42); real rate cards are round, stable, and change rarely.
  3. Implausible quantities/prices (5 offline edit days at ₱117,880.66; 43 LTO tapes at ₱9,938.05 each; 1 optical media stock at a round ₱5,000.00, which looks like a plug row to hit the total).
  4. Only 12 rows: not high-volume, no weekday/holiday/month-end shape.
- Story tensions to resolve (tickets 03, 06): "byte-identical per seed" vs LLM calls; "statistically patternless" vs "realistic recurrence".
- Standing preference: plan, don't build. No implementation in this map.

## Ticket status

| # | Ticket | Status |
|---|--------|--------|
| 01 | Destination and realism bar | closed |
| 02 | Post-production cost facts | closed |
| 03 | Determinism boundary | closed |
| 04 | Temporal recurrence model | closed |
| 05 | Quantity and price model | closed |
| 06 | Patternless vs realistic | closed |
| 07 | Correction feedback loop | closed |
| 08 | Config and CLI shape | closed |

## Decisions so far

- **Ticket 01 (done):** destination and realism bar, grilled with Cyril. Full detail in `tickets/01-destination-and-realism-bar.md`.
  - Spec only. Reviewer's brief look judges rows and the sample as a whole; row level is the hard gate.
  - Four must-pass tells: stable rate-card price, plausible quantity and price, no plug rows, recurrence rhythm. Tool runs automatic proxy checks; human has final say; a found tell becomes a correction (ticket 07), not a hard fail.
  - Ledgers set aggregate shape; human messiness stays in at real rates, no corrupt values; "patternless" means no formula a detector can key on, realism wins on conflict.

- **Ticket 02 (done):** fact sheet at `research/02-cost-facts.md`, anchored on Cyril's 2023-2025 expense ledgers plus web gaps.
  - Ledger direction is **spend only** (things bought, events); no lines paying a person (labour repairs allowed). Sold services (edit, colour, scan) leave the catalog.
  - Volume: about ₱4M and 700-1,000 rows **per quarter** (about ₱12M a year). Scale by rows and quantities; unit prices anchored, yearly uplift only.
  - Load-test multipliers: per price class (subscriptions by seats/tier, retail by quantity, big-ticket by scope) **and** per storyline, with a project-tier factor (independent / mid / high-end).
  - Eight storylines drive the catalog; prices use three variance classes, no stepped rounding.
  - Real ledger facts: median row about ₱670, about 43% rows without item text, VAT 12% shown on about 13% of rows, per-item unit price derived from pack sizes in text.
- **Ticket 03 (done):** determinism boundary, grilled with Cyril.
  - LLM at authoring time only; `generate` never calls an LLM and needs no network. `author` and `generate` are separate commands. LLM drafts catalog, description variants, vocabulary; anchor prices come from ticket 02 ledger facts.
  - Corrections are data fed to `author` and may change deterministic rules. Rules are declarative data in the bundle, so a correction = new bundle version, never a code change.
  - Byte-identical on the same machine only. Money in integer centavos.
  - Bundle: immutable, identified by content hash plus human label, committed in repo at `bundles/<label>-<hash>/`.
  - Run identity = seed + bundle hash + config hash (all config hashed except output path and logging); generator version recorded as informational. `run.json` sidecar holds resolved config, seed, hashes, row count, total; CSV keeps the plain 4 columns.
  - Omitted seed: one is drawn and recorded; `--seed` repeats a run.
  - Per-component random sub-streams (per storyline and per concern), derived from seed + stable name.
  - `author` writes to a temp folder, promotes only after automatic validation passes; failure leaves the current bundle untouched. Promoted bundles are `reviewed: false` until Cyril approves; `generate` warns on unreviewed but still runs. `generate` uses the latest promoted bundle unless `--bundle` is given.
- **Ticket 04 (done):** temporal recurrence model, grilled with Cyril. Full detail in `tickets/04-temporal-recurrence-model.md`.
  - Seven archetypes: fixed-day subscription, project burst, periodic top-up, petty daily, deposit-then-balance pair, one-off big ticket, batch-logged.
  - Default one row per item per day plus per-archetype minimum gaps; only party-day and batch-logged rows may exceed it. Soft daily ceiling about 3x average.
  - Ledger weekday weights, bundled Philippine holiday calendar, Oct-Dec season; default span is the latest full quarter.
  - Closed menu of named inter-arrival distributions; anchors and archetype assignments fixed in the bundle, seed moves jitter, bursts and slips.
  - Scaling adds occurrences, never shrinks gaps; `generate` fails clearly if gap rules cannot hold. Five timing checks handed to the scorecard.

- **Ticket 05 (done):** quantity and price model, grilled with Cyril. Full detail in `tickets/05-quantity-and-price-model.md`.
  - Rate cards with fixed price points per item (retail 2-4, subscriptions 1, big-ticket negotiated); one tidy price step per item per year on a bundle-fixed date; up to 3 bundle-listed quantity tiers for stock and hardware.
  - Quantities from weighted allowed sets (pack sizes, seats); integer centavos, about 60% whole pesos, no random cents.
  - Tier factors 0.5 / 1.0 / 2.5 scale occurrences and quantities, never prices.
  - Total reached by calibrating an occurrence plan to target..target+2%, closing with whole small ordinary occurrences, never editing an amount; scaling order: occurrences, seats/items, quantities, big-ticket scope.
  - Round figures only on approved big-ticket items. Six price and quantity checks handed to the scorecard.

- **Ticket 06 (done):** patternless vs realistic, grilled with Cyril. Full detail in `tickets/06-patternless-vs-realistic.md`.
  - Pass bar: ledger-relative where the ledgers give a reference, absolute where they don't; realism wins on conflict.
  - Seven anomaly checks (duplicates, round share, Benford report-only, per-item outliers, interval regularity, weekday/holiday/month-end shape, distinct prices and quantities). One fixed tolerance: pass +-25%, warn to +-50%, fail beyond.
  - Protected structure never loosened by corrections. Fingerprints designed out: tight total band, identical monthly counts across seeds, perfect date sorting, always-petty closing rows.
  - Bundle holds ledger summary stats (`reference.json`), never raw rows. CSV is always written; non-zero exit on hard failure (duplicates, plug rows, price stability, gap rules).

- **Ticket 07 (done, Cyril 2026-09-30):** spot-check = human reviewer compares the output CSV with its bundle, then talks to Claude about repo updates. Learning from corrections = a GitHub ticket. No corrections file or `--corrections` flag in the tool.

- **Ticket 08 (done, Cyril 2026-09-30):** prototype at `prototypes/08-config-cli/`. Commands `author`, `approve` (kept, used after hand-editing a bundle) and `generate`; one TOML config; distinct exit codes 0-6 accepted; no corrections input (ticket 07).

## Not yet specified

- Validation/realism scorecard (metrics that prove data is realistic and not just random); temporal and price models are now decided.
- Implementation language, LLM provider/model, cost and offline behaviour.
- Extra columns or edge cases the import pipeline needs (credits/refunds, tax, vendor, project code), if any.

## Out of scope

- Writing the generator.
- Building or integrating with the financial review / import system.
- Output formats other than the 4-column CSV.
