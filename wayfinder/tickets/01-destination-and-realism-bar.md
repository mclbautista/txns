---
type: wayfinder:grilling (HITL)
status: closed
---
## Question
Confirm the destination (spec only?) and define "realistic" concretely: what would a financial reviewer look at to believe a row was human-made? Settle the acceptance bar, the meaning of "≥ ₱4M" (per run? per quarter? gross?), and what the load tester means by "spot-check".

## Resolution

Resolved by grilling Cyril.

- **Destination:** a written design spec only; no generator code in this map. Implementation is a separate later effort.
- **"Minimum ₱4M":** the minimum quarterly total of qty × unit price. Each run covers one quarter of dates.
- **Realism bar:** rows must survive (A) eyeballing for odd prices or quantities, (B) per-item and per-month totals against a budget, and (C) automated anomaly checks.
- **Anomaly checks in scope** (Cyril: "go with reco"): Benford's law on amounts, duplicate/near-duplicate rows, round-number frequency, weekend/holiday postings, same-day clustering by item. Confirm the reviewer's actual list later; design against all five meanwhile.
- **Spot-check and learning:** after a run the load tester marks rows as wrong with a reason (e.g. "editor day rate too high") and the next run's rate cards and rhythms adjust. Format is prototyped in the correction feedback loop ticket.
