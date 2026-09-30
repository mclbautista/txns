"""Project burst: clustered bursts with quiet gaps between them (FR-E4).

The item's storyline sets when bursts happen (`txns.engine.bursts`: month
weights, burst length range, bursts per quarter, quiet days). Inside a burst
the item occurs on a day with chance `per_burst / mean burst length x day
weight` (weekday, holidays and month-end from `calendar.day_shape`; the month
curve already placed the burst, so it is not applied twice). Outside bursts the
item never occurs. Gap rules hold: at most one row a day, at least
`min_gap_days` between rows (rules.archetypes.project_burst, item params
override). Big-ticket items stay on weekdays (FR-E6). Rows are tagged
"burst", which keeps them in their burst under the daily ceiling.

Item params:   per_burst (float, expected rows of this item per burst, default 1.0)
               burst_scale (float, default 1.0): multiplies the storyline's burst
               rate for this item; normally left out and turned by the levers.
Levers:        occurrences via burst_scale and per_burst (more bursts, and more of
               the item inside each, never a shorter gap), quantities. Not used
               for closing: a lone added row would sit in a quiet gap.

One uniform draw per period day on the item's `dates` stream whatever the
scale, so a larger scale only adds days (up to the gap rules).
"""

from __future__ import annotations

from dataclasses import replace

from txns.engine import bursts as storyline_bursts
from txns.engine import calendar
from txns.engine.archetypes import register
from txns.engine.levers import Levers
from txns.engine.rows import Occurrence
from txns.errors import BundleInvalid

DEFAULT_PER_BURST = 1.0
# Burst rows count toward the daily ceiling but never move under it (calendar.apply_ceiling
# moves untagged rows only), so they stay inside their burst.
TAGS = ("burst",)


def _param(item, name: str, default: float) -> float:
    value = item.params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise BundleInvalid(f"bundle invalid: item `{item.id}`: param `{name}` must be a number >= 0")
    return float(value)


@register("project_burst", levers=Levers(rate_params={"burst_scale": 1.0, "per_burst": DEFAULT_PER_BURST}))
def plan(item, ctx, stream):
    per_burst = _param(item, "per_burst", DEFAULT_PER_BURST)
    scale = _param(item, "burst_scale", 1.0)
    spec = storyline_bursts.spec(ctx.bundle, item.storyline)
    windows = storyline_bursts.bursts(ctx, item.storyline, scale)
    shape = replace(calendar.day_shape(ctx, item), months=(1.0,) * 12)
    weekdays_only = item.price_class == "big_ticket"
    _, min_gap = ctx.bundle.gap_rules(item)  # one draw a day: never more than one row a day
    per_day = per_burst / storyline_bursts.mean_days(spec)
    period = ctx.config.period
    out = []
    last = None
    k = 0  # index of the first burst that may still hold the day
    for day in calendar.days(period.start, period.end):
        u = stream.uniform()  # always one draw a day
        while k < len(windows) and windows[k].end < day:
            k += 1
        if k == len(windows) or day not in windows[k]:
            continue
        if weekdays_only and day.weekday() >= 5:
            continue
        if last is not None and (day - last).days < min_gap:
            continue
        if u < min(1.0, per_day * shape.weight(day)):
            out.append(Occurrence(item.id, item.storyline, day, TAGS))
            last = day
    return out
