"""Storyline bursts (FR-E4, FR-E9, FR-E10): when a storyline's project work happens.

A storyline's bursts are windows of `burst_days` [min, max] days. Burst starts
are a day-by-day chance: `bursts_per_quarter` / 91.3125 per day, times the
storyline's month weight for that day's month, times the calibration scale.
Between one burst's end and the next start at least `quiet_days` days pass
(a start that would crowd a burst already placed is dropped), so bursts never
run into each other, however much calibration scales them up (FR-G2: more
bursts, never shorter gaps). A larger scale only ever adds bursts.

Settings: the storyline (storylines.json), else `rules.archetypes.project_burst`,
else the defaults below; month weights: storyline, else the archetype rules,
else `rules.calendar` (as in `calendar.day_shape`). The bundle fixes all of
them; the seed moves only where bursts start and how long each one lasts
within the range (FR-E9).

Draws come from the storyline's own `bursts` stream, two per day from
`max_days - 1` days before the period start (so a burst may already be running
when the period opens) to its end, whatever the scale, so a storyline's bursts
never depend on any other storyline or item (T5), and every item of the
storyline at the same scale sees the same bursts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from txns.engine import calendar

DAYS_PER_QUARTER = 365.25 / 4
DEFAULT_BURST_DAYS = (3, 10)
DEFAULT_BURSTS_PER_QUARTER = 2.0
DEFAULT_QUIET_DAYS = 7
ARCHETYPE = "project_burst"


@dataclass(frozen=True)
class BurstSpec:
    min_days: int
    max_days: int
    per_quarter: float
    quiet_days: int
    months: tuple[float, ...]  # Jan..Dec


@dataclass(frozen=True)
class Burst:
    start: date
    end: date  # inclusive; either end may lie outside the period

    def __contains__(self, day: date) -> bool:
        return self.start <= day <= self.end

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def spec(bundle, storyline: str) -> BurstSpec:
    story = bundle.storylines.get(storyline, {})
    arch = bundle.archetype_rules(ARCHETYPE)
    cal = calendar.calendar_rules(bundle.rules)
    lo, hi = calendar._lookup("burst_days", story, arch, default=DEFAULT_BURST_DAYS)
    return BurstSpec(
        min_days=int(lo),
        max_days=int(hi),
        per_quarter=float(calendar._lookup("bursts_per_quarter", story, arch, default=DEFAULT_BURSTS_PER_QUARTER)),
        quiet_days=int(calendar._lookup("quiet_days", story, arch, default=DEFAULT_QUIET_DAYS)),
        months=calendar._months(calendar._lookup("month_weights", story, arch, cal, default=None)),
    )


def bursts(ctx, storyline: str, scale: float = 1.0) -> list[Burst]:
    """The storyline's bursts that overlap the period, in date order.

    `scale` multiplies the burst rate (tier, multipliers, calibration); the
    quiet gap between bursts stays at least `quiet_days`.
    """
    s = spec(ctx.bundle, storyline)
    period = ctx.config.period
    stream = ctx.stream("storyline", storyline, "bursts")
    per_day = s.per_quarter / DAYS_PER_QUARTER
    first = period.start - timedelta(days=s.max_days - 1)
    candidates = []  # (key, burst): a day starts a burst when key < scale
    for day in calendar.days(first, period.end):
        u_start, u_len = stream.uniform(), stream.uniform()  # always two draws a day
        rate = per_day * s.months[day.month - 1]
        if rate <= 0 or u_start >= min(1.0, rate * scale):
            continue
        length = s.min_days + int(u_len * (s.max_days - s.min_days + 1))
        candidates.append((u_start / rate, Burst(day, day + timedelta(days=length - 1))))
    # Keep candidates in key order, skipping any that would crowd a kept burst. A
    # larger scale only adds candidates with larger keys, so it only adds bursts:
    # the plan grows steadily with the scale, which calibration relies on.
    kept: list[Burst] = []
    quiet = timedelta(days=s.quiet_days)
    for _, burst in sorted(candidates, key=lambda c: (c[0], c[1].start)):
        if all(burst.start > k.end + quiet or k.start > burst.end + quiet for k in kept):
            kept.append(burst)
    return sorted((b for b in kept if b.end >= period.start), key=lambda b: b.start)


def mean_days(s: BurstSpec) -> float:
    return (s.min_days + s.max_days) / 2
