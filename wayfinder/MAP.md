---
label: wayfinder:map
title: Realistic post-production transaction generator
tracker: local-markdown in this repo (tickets in ./tickets)
---

## Destination

A **design spec** (architecture + data model + decisions) for a one-command, seed-deterministic tool that emits a realistic, human-looking, high-volume ledger CSV (`date, qty, unit_price, item/service`, ≥ ₱4M per run) for load-testing a post-production facility's financial review system. Deterministic code does the generating; LLM calls only make the inputs (catalogs, rate cards, vocabulary, corrections) more realistic. Ends when someone can hand the spec to an implementer with nothing left to decide.

> Drafted from the topic + `user stories.md`, not yet confirmed by Cyril. See ticket 01.

## Notes

- Domain: Philippine post-production facility (currency ₱), per the ₱4M target. Services: offline/online edit, conform, colour, restoration scan, archive verification, freelance editors, stage hire; stock: LTO tape, optical media.
- **Negative example** (`negative-sample.csv`, don't reproduce): 12 rows totalling exactly ₱4,005,000.00. Observed faults:
  1. Same item type clustered on adjacent days (three Freelance Editor rows, only 2 distinct dates per month for whole categories); no recurrence rhythm.
  2. Unit price for the same item varies wildly with random cents (Freelance Editor 73,675.41 vs 77,054.63; Restoration Scan 24,999.73 vs 27,016.42); real rate cards are round, stable, and change rarely.
  3. Implausible quantities/prices (5 offline edit days at ₱117,880.66; 43 LTO tapes at ₱9,938.05 each; 1 optical media stock at a round ₱5,000.00, which looks like a plug row to hit the total).
  4. Only 12 rows: not high-volume, no weekday/holiday/month-end shape.
- Story tensions to resolve (tickets 03, 06): "byte-identical per seed" vs LLM calls; "statistically patternless" vs "realistic recurrence".
- Standing preference: plan, don't build. No implementation in this map.

## Decisions so far

_(none yet)_

## Not yet specified

- Validation/realism scorecard (metrics that prove data is realistic and not just random), once the temporal and price models are decided.
- Volume shape: rows-per-run, how "minimum ₱4M" is met without plug rows, and scaling up to load-test sizes.
- Implementation language, LLM provider/model, cost and offline behaviour.
- Extra columns or edge cases the import pipeline needs (credits/refunds, tax, vendor, project code), if any.

## Out of scope

- Writing the generator.
- Building or integrating with the financial review / import system.
- Output formats other than the 4-column CSV.
