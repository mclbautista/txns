---
type: wayfinder:grilling (HITL)
status: done. Destination, "≥ ₱4M", "spot-check" and the concrete realism bar all settled by Cyril 2026-09-30.
---
## Question
Confirm the destination (spec only?) and define "realistic" concretely: what would a financial reviewer look at to believe a row was human-made? Settle the acceptance bar, the meaning of "≥ ₱4M" (per run? per quarter? gross?), and what the load tester means by "spot-check".

## Decision so far
- **Destination: spec only.** The map ends with a design spec (architecture, data model, decisions) that an implementer can build from. No generator is written in this map.
- **"≥ ₱4M" is a configurable target, not a fixed constant.** The total is something the tool can be aimed at. Today's need is about ₱4M per quarter, but that may change, so the target is a config value and the spec must not hardcode ₱4M or a quarter. Ticket 02's figures (about ₱4M and 700-1,000 rows per quarter) are the current default.
- **"Spot-check" means a brief human check.** A person glances at a sample of rows and judges whether they look human-made. It is not an audit. The realism bar and any correction flow (ticket 07) must be cheap for a human to apply in a short sitting.

## Realism bar (settled 2026-09-30)
- **What is judged:** both each sampled row and the sample as a whole. The row check is the hard gate; the whole-ledger glance is secondary.
- **Must-pass tells (row level):**
  1. Unit price is a stable rate-card price for that item, not random cents or wild variance.
  2. Quantity and price are plausible for the item.
  3. No plug rows: no round-number row that looks like it exists to hit the total.
  4. Same item follows a recurrence rhythm, not clusters on adjacent days.
- **Nice-to-have:** item text not too clean or uniform; weekday/holiday/month-end shape (covered by ticket 04); enough rows (volume model). No extra bar needed here.
- **Reference:** Cyril's 2023-2025 ledgers set the aggregate shape (median row about ₱670, weekday mix, source mix); the human judges row plausibility. Tolerances are set in the scorecard ticket.
- **Human messiness required** at real-ledger rates (same-day same-amount duplicates, batch-logged rows, blank item text), but no typos or corrupt values: every row must parse in the import pipeline.
- **"Statistically patternless" (story 3)** means no fixed formula or uniform randomness an anomaly detector can key on, not "no recurrence". Realism wins where they conflict.
- **Automatic proxy checks:** the tool checks a proxy for each must-pass tell and prints a short report. The human glance has the final say. Metrics and thresholds belong to the scorecard ticket.
- **Pass rule:** the bar is met when the reviewer finds no tell in a brief look. A tell they find is not a hard fail: it becomes a correction (ticket 07) that yields a new bundle version.
- **Dropped:** a fixed sample size and numeric pass rule; not needed.
