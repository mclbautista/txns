---
type: wayfinder:grilling (HITL)
status: open, unblocked
---
## Question
Where exactly do LLM calls sit so the same seed gives byte-identical CSV? Candidates: (a) LLM only at authoring time, producing frozen, versioned artifacts (catalog, rate cards, description variants) that the deterministic engine consumes; (b) runtime calls behind a content-addressed response cache; (c) both. Decide which, plus how artifacts are versioned and what changes the "seed" identity (seed + artifact hash + config).
