---
type: wayfinder:prototype (HITL)
status: done (decision below, Cyril 2026-09-30)
---
## Question
What does spot-checking and "learning from corrections" look like? Prototype a rough correction file/flow (flag row, say why, edit rate or rhythm) and decide how corrections feed back into frozen artifacts or rules without breaking determinism.

## Decision (Cyril, 2026-09-30)
- **Spot-check:** a human reviewer checks the output CSV against its bundle.
- **Learning from corrections:** the reviewer then talks to Claude about how to update the repo. A correction is a GitHub ticket, resolved by Claude changing the repo (a new bundle version via `author`, or a rule change). There is no corrections file, no `--corrections` flag and no correction-ingestion step in the tool.
- Consequences: `generate` stays a pure function of seed + bundle + config; ticket 03's "corrections are data fed to `author`" is satisfied by the GitHub ticket being the record. Protected structure from ticket 06 still applies to whatever Claude changes.
