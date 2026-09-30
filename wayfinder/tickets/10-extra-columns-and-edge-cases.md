---
type: wayfinder:grilling (HITL)
status: open
---
## Question
Does the import pipeline need anything beyond the 4-column CSV (`date, qty, unit_price, item`)? Decide whether credits/refunds (negative rows), VAT, vendor or project code are needed, and settle the item-text rule below.

## Settled so far (Cyril, 2026-09-30)
- No blank item cells. Rows that would have had no item text (about 43% in the ledgers) get an invented item text; vendor-only text is not allowed.
- Consequences to resolve here: how invented items are drafted by `author`, how the ticket 05 rule "seller appears only where item text names it" changes, and what the ticket 06 distinct-price check keys on.

## Context
- Ticket 01: every row must parse in the import pipeline; output is the 4-column CSV only (MAP out of scope).
- Listed under "Not yet specified" in MAP.md.
