"""Party days of a party storyline (FR-E5, FR-E10): the days it throws a party.

A storyline with `parties_per_quarter` (storylines.json) is a party storyline
(`txns.bundle.events`). A party day is a day-by-day chance:
`parties_per_quarter` / 91.3125 per day, times the storyline's day shape (month
weights, so a seasonal storyline parties in its season; weekday weights and
holidays from `rules.archetypes.project_burst`, else `rules.calendar`), times
the calibration scale. Party days are at least PARTY_GAP_DAYS apart (a
candidate that would crowd a kept one is dropped); a larger scale only adds
party days.

One draw a period day on the storyline's own `parties` stream whatever the
scale, so party days never depend on any other storyline or item (T5), and
every party item of the storyline at the same scale sees the same parties.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from txns.bundle import events
from txns.engine import calendar

DAYS_PER_QUARTER = 365.25 / 4
PARTY_GAP_DAYS = 7


@dataclass(frozen=True)
class _Stand:
    """What `calendar.day_shape` reads of an item, for a storyline's party days."""

    storyline: str
    archetype: str = events.PARTY_ARCHETYPE
    price_class: str = "retail"


def per_quarter(bundle, storyline: str) -> float:
    return float((bundle.storylines.get(storyline) or {}).get(events.PARTY_KEY) or 0.0)


def party_days(ctx, storyline: str, scale: float = 1.0) -> list[date]:
    """The storyline's party days in the period, ascending."""
    period = ctx.config.period
    stream = ctx.stream("storyline", storyline, "parties")
    shape = calendar.day_shape(ctx, _Stand(storyline))
    per_day = per_quarter(ctx.bundle, storyline) / DAYS_PER_QUARTER
    candidates = []  # (key, day): a day is a candidate party day when key < scale
    for day in calendar.days(period.start, period.end):
        u = stream.uniform()  # always one draw a day
        rate = per_day * shape.weight(day)
        if rate <= 0 or u >= min(1.0, rate * scale):
            continue
        candidates.append((u / rate, day))
    kept: list[date] = []
    for _, day in sorted(candidates):
        if all(abs((day - k).days) >= PARTY_GAP_DAYS for k in kept):
            kept.append(day)
    return sorted(kept)
