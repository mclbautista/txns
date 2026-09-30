---
type: wayfinder:grilling (HITL)
status: done (grilled with Cyril 2026-09-30)
---
## Question
How are qty and unit_price made realistic? Decide rate cards (stable, round-ish prices per item/vendor), rarely-changing price steps, volume tiers, pack-size quantities for stock, day/hour quantity ranges per service, and how the ₱4M floor is reached without plug rows.

Scope note: the ledger is spend-only (ticket 02), so the sold-service parts (service days, hour ranges) are dropped.

## Decision
- **Rate cards:** each catalog item has fixed price points in the bundle. Retail: 2 to 4 points (one per seller); each row picks one. Subscriptions: one price. Big-ticket: a negotiated round figure. No continuous randomness. The seller appears only where item text names it.
- **Price steps:** 0 or 1 step per item per year, on a bundle-fixed date (mostly Jan 1 or the billing anchor); the seed does not move it. Step size 3 to 15% for retail and subscriptions, tidied to a rate-card figure (₱2,642 to ₱2,900, not ₱2,847.31). The old price vanishes after the step, so one item never shows two prices on adjacent days.
- **Quantities:** each item declares a weighted allowed quantity set (pack sizes such as drives 1/2/4/10/15, LTO 5/10/20, petty 1 to 3). The seed picks from it; never a continuous range. Subscriptions: qty = seats. Big-ticket: 1 for a lump, or a unit count. Decimal qty (fuel litres) only on items marked decimal.
- **Volume tiers:** an item may list up to 3 quantity breakpoints, each with its own fixed unit price (for example 1-3 at ₱1,950, 4-9 at ₱1,800, 10+ at ₱1,700). Discounts are never computed. Stock and hardware only.
- **Precision:** integer centavos; unit_price printed with 2 decimals; about 60% whole pesos, the rest tidy figures (.50/.75); never random cents. A derived per-unit price (total / pcs, e.g. ₱1,666.67) is allowed on about 5% of stock rows, only when the item text states the pack size. qty prints as an integer unless the item is decimal.
- **Tier factors:** independent 0.5x, mid 1.0x (calibrated to the ledger), high-end 2.5x. Bundle-configurable, used only as load-test multipliers. They scale occurrences and quantities, never unit prices.
- **Reaching the target without plug rows:** build an occurrence plan from the storylines, then calibrate a global scale so the total lands at the target or up to 2% above it. Closing adds or drops whole small ordinary occurrences (petty daily, top-ups), dated by the ticket 04 rules. No amount is edited after it is drawn. If the band cannot be met, `generate` fails clearly. Target and band are config values.
- **Scaling order** when the target grows, stopping when met: (1) more occurrences per item until ticket 04 gap rules would break; (2) more seats and items; (3) larger quantities from the allowed sets; (4) larger big-ticket scope. Unit prices never move. Optional `target_rows`: calibration reports if it cannot satisfy both it and the total.
- **Scorecard handoff (price and quantity section):** distinct unit prices per item are at most its price points plus its steps in the span; every qty is in the item's allowed set; whole-peso share of unit prices near 60%; row-amount percentiles within a band of the ledger's; no round-thousand row amount unless the item is an approved big-ticket item; largest single row at most a set share of the total. Thresholds are set in the scorecard ticket.
- **Round figures** (₱90,000, ₱125,000) are allowed only on approved big-ticket items; any other round row is a plug tell.
