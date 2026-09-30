---
type: wayfinder:grilling (HITL)
status: done (grilled with Cyril 2026-09-30; ticket 01 now done, ₱4M and rows-per-quarter taken from ticket 02)
---
## Question
How do transactions land on dates so same-type items never chunk onto one day (the negative sample's failure)? Decide recurrence archetypes (project-driven bursts, weekly freelancer days, monthly retainers, month-end stock top-ups, ad-hoc), weekday/holiday/month-end shaping, and inter-arrival distributions per item.

## Decision
- **Archetypes (7, named in the bundle):** fixed-day subscription, project burst, periodic top-up, petty daily, deposit-then-balance pair, one-off big ticket, batch-logged. "Weekly freelancer days" dropped (ledger is spend-only). Each catalog item has one primary archetype; storylines can override.
- **No chunking:** default at most one row per item per day, plus a minimum gap per archetype (e.g. subscription >= 25 days, petty daily >= 1 day, top-up >= 7 days), overridable per item. Only party-day and batch-logged archetypes may exceed it, capped by count. No hard daily cap across items, but a soft ceiling of about 3x the average daily row count on ordinary days.
- **Weekday:** ledger weights Mon-Fri 17% each, Sat 9%, Sun 5%, with per-archetype overrides. Subscriptions ignore weekday (pinned to anchor day); petty daily and top-ups skew to weekdays; big-ticket weekdays only.
- **Holidays:** Philippine holiday calendar is bundle data for the year range. Regular holidays get near-zero non-subscription rows with a small leak (after-the-fact logging). Oct-Dec event-season uplift and Holy Week shape on top.
- **Span:** configurable start and end dates, default the latest full quarter. Seasonality keyed to calendar month; yearly price uplift applied by date, not by run.
- **Inter-arrival:** closed menu of named distributions, parameters per item: fixed schedule with jitter (subscriptions); Poisson-style with weekday weights (petty daily); clustered bursts with quiet gaps (project bursts); anchored to period end with jitter (top-ups); offset from a parent row (deposit/balance). Corrections change parameters, never the math.
- **Recurring dates:** anchor day with weekend/holiday roll-forward; about 5% slip 1-2 days; skipped or doubled month rare. Amount changes belong to ticket 05.
- **Bundle vs seed:** anchor days, archetype assignments, holiday calendar fixed in the bundle. Seed moves jitter, burst start dates, one-offs and slips.
- **Storylines:** each carries a month-weight curve, burst length range and bursts per quarter. Petty spend and subscription stack run all year; parties and festival trip are seasonal.
- **Batch-logged rows:** `date` is the batch date; description gets a short original-date tail ("for 12/14, 12/16"), about 5% of eligible rows; tail format is a vocabulary variant drafted by `author`.
- **Scaling:** load-test multipliers add occurrences, bursts and seats (extra items or quantity), never shrink gaps. If gap rules would break at an extreme multiplier, `generate` fails clearly rather than chunking.
- **Scorecard handoff (timing section):** max same-item rows per day and minimum gap per item; weekday shares vs ledger; share of non-subscription rows on holidays; subscription anchor-day hit rate; monthly row-count and spend spread. Tolerances set in the scorecard ticket.
