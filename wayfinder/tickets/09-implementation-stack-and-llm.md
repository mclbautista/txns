---
type: wayfinder:grilling (HITL)
status: open
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
