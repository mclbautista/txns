"""Batch-logged (FR-E3, FR-E5, FR-H3): things bought on ordinary days (delivery
fees, small reimbursements) but entered in bunches on a later batch day.

The days they happened are drawn like petty daily (Poisson-style with the day
shape, at most one per day). Each is then dated on the first open batch day at
least `min_lag_days` after it; one batch day takes at most the item's
`max_per_day` rows (its gap rule, `Bundle.gap_rules`), oldest first, and the
rest wait for the next batch day. A row keeps the day it happened as
`original_date` (the messiness stage writes it as a date tail on a few rows)
and is tagged "batch" (exempt from the daily ceiling, never moved or
duplicated). Rows whose batch day falls outside the period are logged in
another period, so the plan looks back LOOKBACK_DAYS before the period start.

Item params:   per_week (float, expected purchases per week on ordinary days)
Bundle rules (`rules.archetypes.batch_logged`, item params override):
    batch_days     days of the month that are batch days, default [15, 31];
                   a day past the month's end is its last day; a batch day that
                   is not a business day rolls forward within its month, else back
    min_lag_days   days from purchase to its earliest batch day, default 1
    skip_chance    chance a batch day is skipped (its rows wait), default 0.2
    max_wait_days  a purchase not logged within this many days (full or skipped
                   batch days) is never logged, default 45
    max_per_day    rows of the item on one batch day (the gap rule's cap,
                   `Bundle.gap_rules`), default 8 (the ledgers show up to 7)
"""

from __future__ import annotations

import calendar as _calendar
from datetime import date, timedelta

from txns.engine import calendar
from txns.engine.archetypes import register
from txns.engine.levers import Levers
from txns.engine.rows import Occurrence

DEFAULT_BATCH_DAYS = (15, 31)
DEFAULT_MIN_LAG_DAYS = 1
DEFAULT_SKIP_CHANCE = 0.2
DEFAULT_MAX_WAIT_DAYS = 45
LOOKBACK_DAYS = 62
BATCH_TAG = "batch"


def _setting(item, rules, key, default):
    return item.params.get(key, rules.get(key, default))


def batch_days(first: date, last: date, days_of_month, cal) -> list[date]:
    """Batch days from `first`'s month through `last`'s month, ascending, unique."""
    out: set[date] = set()
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        n = _calendar.monthrange(y, m)[1]
        for dom in days_of_month:
            d = date(y, m, min(int(dom), n))
            fwd = d
            while not calendar.is_business_day(fwd, cal) and fwd.month == m:
                fwd += timedelta(days=1)
            if fwd.month != m:
                fwd = d
                while not calendar.is_business_day(fwd, cal) and fwd.month == m:
                    fwd -= timedelta(days=1)
            if fwd.month == m:
                out.add(fwd)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(out)


@register("batch_logged", levers=Levers(rate_params={"per_week": 1.0}, closing=True))
def plan(item, ctx, stream):
    rules = ctx.bundle.archetype_rules("batch_logged")
    period = ctx.config.period
    shape = calendar.day_shape(ctx, item)
    per_day = float(item.params.get("per_week", 1.0)) / 7
    lag = timedelta(days=int(_setting(item, rules, "min_lag_days", DEFAULT_MIN_LAG_DAYS)))
    skip = float(_setting(item, rules, "skip_chance", DEFAULT_SKIP_CHANCE))
    max_wait = timedelta(days=int(_setting(item, rules, "max_wait_days", DEFAULT_MAX_WAIT_DAYS)))
    cap, _ = ctx.bundle.gap_rules(item)
    cap = max(1, cap)

    first = period.start - timedelta(days=LOOKBACK_DAYS)
    bought = [d for d in calendar.days(first, period.end) if stream.chance(min(1.0, per_day * shape.weight(d)))]
    days_of_month = _setting(item, rules, "batch_days", DEFAULT_BATCH_DAYS)
    open_days = [b for b in batch_days(first, period.end, days_of_month, ctx.bundle.calendar) if not stream.chance(skip)]

    out = []
    waiting = 0  # index of the oldest purchase not yet logged
    for b in open_days:
        taken = 0
        while waiting < len(bought) and bought[waiting] + max_wait < b:
            waiting += 1  # waited too long: never logged
        while waiting < len(bought) and taken < cap and bought[waiting] + lag <= b:
            if period.start <= b <= period.end:
                out.append(Occurrence(item.id, item.storyline, b, tags=(BATCH_TAG,), original_date=bought[waiting]))
            waiting += 1
            taken += 1
    return out
