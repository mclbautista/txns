"""Petty daily: Poisson-style with weekday weights, at most one row per item per day.

Item params:   per_week (float, expected rows per week at weekday-average rate).
Bundle rules:  rules.archetypes.petty_daily.weekday_weights (Mon..Sun ints).
"""

from __future__ import annotations

from txns.engine import calendar
from txns.engine.archetypes import register
from txns.engine.rows import Occurrence


@register("petty_daily")
def plan(item, ctx, stream):
    rules = ctx.bundle.archetype_rules("petty_daily")
    weights = tuple(rules.get("weekday_weights", calendar.DEFAULT_WEEKDAY_WEIGHTS))
    per_week = float(item.params.get("per_week", 1.0))
    total = sum(weights)
    out = []
    period = ctx.config.period
    for day in calendar.days(period.start, period.end):
        # Expected rows per day = per_week * share of the week on this weekday.
        p = min(1.0, per_week * weights[day.weekday()] / total)
        if stream.chance(p):
            out.append(Occurrence(item.id, item.storyline, day))
    return out
