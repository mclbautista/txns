"""Periodic top-up: anchored to the end of each top-up period, with jitter (FR-E4, FR-E6).

Item params:
    per_month      top-ups per month, default 1.0 (the rate the occurrence lever
                   scales). The month is cut into `per_month` equal periods and
                   each top-up is anchored to its period's end: 1.0 = month end,
                   2.0 = mid-month and month end; fractions keep the periods even.
    jitter_days    the top-up lands within this many days either side of the
                   anchor, default 2 (per-item override of the rule below)
Bundle rules (rules.archetypes.periodic_top_up, all optional):
    jitter_days    default 2
    weekday_weights and the other `calendar` keys: the day shape inside the
                   jitter window (weekdays favoured, holidays near zero)
    min_gap_days   default 7 (`Bundle.gap_rules`; items may override)

Within the window a day is drawn with the item's day shape (`calendar.day_shape`),
so top-ups skew to weekdays and avoid holidays; days closer than the minimum gap
to the previous top-up are left out, and a top-up with no such day is dropped.
The seed moves only the jitter.
"""

from __future__ import annotations

import calendar as pycal
import math
from datetime import date, timedelta

from txns.engine import calendar
from txns.engine.archetypes import register
from txns.engine.levers import Levers
from txns.engine.rows import Occurrence

NAME = "periodic_top_up"
DEFAULT_JITTER_DAYS = 2
MAX_PER_MONTH = 31.0  # anchors at most about a day apart; gap rules cap the rows well before


def _anchor(epoch_year: int, t: float) -> date:
    """The date `t` months after the start of `epoch_year` (t = whole months: the day before a month starts)."""
    m = math.floor(t)
    frac = t - m
    year, month = divmod(m, 12)
    year += epoch_year
    first = date(year, month + 1, 1)
    dim = pycal.monthrange(year, month + 1)[1]
    return first + timedelta(days=round(frac * dim) - 1)


def _months_since(epoch_year: int, d: date) -> float:
    dim = pycal.monthrange(d.year, d.month)[1]
    return (d.year - epoch_year) * 12 + d.month - 1 + (d.day - 1) / dim


@register(NAME, levers=Levers(rate_params={"per_month": 1.0}, closing=True))
def plan(item, ctx, stream):
    per_month = min(float(item.params.get("per_month", 1.0)), MAX_PER_MONTH)
    if per_month <= 0:
        return []
    rules = ctx.bundle.archetype_rules(NAME)
    jitter = int(item.params.get("jitter_days", rules.get("jitter_days", DEFAULT_JITTER_DAYS)))
    min_gap = max(1, ctx.bundle.gap_rules(item)[1])
    shape = calendar.day_shape(ctx, item)
    period = ctx.config.period

    epoch = period.start.year
    lead = max(jitter, min_gap) + 31  # anchors before the period set the gap for its first top-up
    k_first = math.floor(_months_since(epoch, period.start - timedelta(days=lead)) * per_month)
    k_last = math.ceil(_months_since(epoch, period.end + timedelta(days=jitter + 1)) * per_month)

    out = []
    prev: date | None = None
    for k in range(k_first, k_last + 1):
        anchor = _anchor(epoch, k / per_month)
        window = [anchor + timedelta(days=j) for j in range(-jitter, jitter + 1)]
        weights = [shape.weight(d) if prev is None or (d - prev).days >= min_gap else 0.0 for d in window]
        total = sum(weights)
        u = stream.uniform()  # one draw per anchor, taken or not
        if total <= 0:
            continue
        x = u * total
        day = next(d for d, w in zip(reversed(window), reversed(weights)) if w > 0)  # float round-off
        for d, w in zip(window, weights):
            if x < w:
                day = d
                break
            x -= w
        prev = day
        if period.start <= day <= period.end:
            out.append(Occurrence(item.id, item.storyline, day))
    return out
