"""Petty daily: Poisson-style with weekday weights, at most one row per item per day.

Item params:   per_week (float, expected rows per week on ordinary days).
Bundle rules:  day shape from `calendar.day_shape` (weekday weights, holidays,
               season, Holy Week, month-end; rules.archetypes.petty_daily overrides).
"""

from __future__ import annotations

from txns.engine import calendar
from txns.engine.archetypes import register
from txns.engine.rows import Occurrence


@register("petty_daily")
def plan(item, ctx, stream):
    shape = calendar.day_shape(ctx, item)
    per_day = float(item.params.get("per_week", 1.0)) / 7
    out = []
    period = ctx.config.period
    for day in calendar.days(period.start, period.end):
        # Expected rows on this day = average daily rate x the day's calendar weight.
        p = min(1.0, per_day * shape.weight(day))
        if stream.chance(p):
            out.append(Occurrence(item.id, item.storyline, day))
    return out
