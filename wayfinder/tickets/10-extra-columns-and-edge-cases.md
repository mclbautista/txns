---
type: wayfinder:grilling (HITL)
status: done (grilled with Cyril 2026-09-30)
---
## Question
Does the import pipeline need anything beyond the 4-column CSV (`date, qty, unit_price, item`)? Decide whether credits/refunds (negative rows), VAT, vendor or project code are needed, and settle the item-text rule below.

## Settled so far (Cyril, 2026-09-30)
- No blank item cells. Rows that would have had no item text (about 43% in the ledgers) get an invented item text; vendor-only text is not allowed.
- Consequences to resolve here: how invented items are drafted by `author`, how the ticket 05 rule "seller appears only where item text names it" changes, and what the ticket 06 distinct-price check keys on.

## Context
- Ticket 01: every row must parse in the import pipeline; output is the 4-column CSV only (MAP out of scope).
- Was listed under "Not yet specified" in MAP.md.

## Decision
- **Columns:** exactly four (`date_of_transaction, qty, unit_price, item/service`). No VAT, vendor or project-code column. No VAT wording in item text; `unit_price` is the amount as booked.
- **Credits and refunds:** none. No negative rows, so qty and unit_price are always positive.
- **No blank item cells.** Rows that would have had no item text (about 43% in the ledgers) get an invented item text; vendor-only text is not allowed.
- **Drafting by `author`:** per catalog item, the LLM drafts at least 3 descriptive and at least 2 terse variants (for example "Delivery fee"). Every variant must name the thing bought; validation rejects vendor-only text. The bundle holds all variants, so `generate` stays LLM-free.
- **Terse share:** per item or category, from `reference.json` (share of textless ledger rows in that category), not a flat 43%. Big-ticket items are never terse.
- **Vendor prefix:** only on a minority of descriptive variants, as "Vendor - item" with the item words present; names go through the ticket 09 brand allowlist and fabricated replacements. Terse variants carry no vendor.
- **Amends ticket 05 seller rule:** the price point is chosen first; a vendor-prefixed variant may be drawn only if its vendor is the seller of that price point. Terse variants are unattributed and go with any price point of the item.
- **Amends ticket 06 checks:** distinct-price/quantity (check 7) and duplicates (check 1) key on catalog item id, not text. Author-time guard: each variant string belongs to exactly one item; a terse string may be shared by two items only if their price-point sets are identical.
- **New check (eighth anomaly check):** overall terse share versus the ledger, warn only, usual +-25% pass / +-50% warn tolerance.
- **CSV format:** UTF-8 without BOM, LF line endings, header always written, ISO `YYYY-MM-DD` dates, RFC 4180 quoting, item text at most 100 characters, ASCII plus `₱` only, no control characters, no leading or trailing spaces. Enforced by `author` validation and re-checked by `generate`, which fails hard on any row that would not parse.
