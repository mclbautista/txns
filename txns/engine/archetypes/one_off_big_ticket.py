"""One-off big ticket (FR-E3, FR-E6, FR-F6): a rare negotiated purchase on a weekday.

A day-by-day chance of `per_quarter` / 91.3125 times the item's day shape
(`calendar.day_shape`: season, holidays, month-end), on weekdays only, and at
least `min_gap_days` apart (gap rule, default 30 days). The price is the item's
rate card: a big-ticket item has one negotiated round figure (FR-F1), approved
for round-figure amounts (`Item.round_figures_approved`); its quantity is 1 or
a unit count from the allowed set, which is the big-ticket scope lever (FR-G2
step 4). Rows are tagged "one_off", so the daily ceiling never moves them (off
their weekday) and the messiness stage never duplicates them.

Item params:   per_quarter (float, expected purchases per quarter, default 1.0)
Levers:        occurrences via per_quarter (retail items only; big-ticket items
               grow by scope, not by count), quantities. Not used for closing.

One draw a period day on the item's `dates` stream whatever the rate.
"""

from __future__ import annotations

from txns.engine import calendar
from txns.engine.archetypes import register
from txns.engine.levers import Levers
from txns.engine.rows import Occurrence
from txns.errors import BundleInvalid

DAYS_PER_QUARTER = 365.25 / 4
DEFAULT_PER_QUARTER = 1.0
TAGS = ("one_off",)


def per_quarter(item, default: float = DEFAULT_PER_QUARTER) -> float:
    value = item.params.get("per_quarter", default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise BundleInvalid(f"bundle invalid: item `{item.id}`: param `per_quarter` must be a number >= 0")
    return float(value)


@register("one_off_big_ticket", levers=Levers(rate_params={"per_quarter": DEFAULT_PER_QUARTER}))
def plan(item, ctx, stream):
    shape = calendar.day_shape(ctx, item)
    per_day = per_quarter(item) / DAYS_PER_QUARTER
    _, min_gap = ctx.bundle.gap_rules(item)
    period = ctx.config.period
    out = []
    last = None
    for day in calendar.days(period.start, period.end):
        u = stream.uniform()  # always one draw a day
        if day.weekday() >= 5:
            continue
        if last is not None and (day - last).days < min_gap:
            continue
        if u < min(1.0, per_day * shape.weight(day)):
            out.append(Occurrence(item.id, item.storyline, day, TAGS))
            last = day
    return out
