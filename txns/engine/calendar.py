"""Calendar shape for the planner (FR-E5, FR-E6, FR-E7): weekdays, holidays, seasons.

An archetype asks `day_shape(ctx, item)` for its item's `DayShape` and scales
its daily rate by `shape.weight(day)` (1.0 = an ordinary weekday-average day):

    weight = weekday x month (season) x holiday x Holy Week x month-end

Every factor is bundle data. Lookup order per key: the storyline (month
weights only, FR-E10), then `rules.archetypes.<archetype>`, then
`rules.calendar`, then the code default below.

rules.json `calendar` table (all optional):

    weekday_weights     [Mon..Sun] relative rates, default 17,17,17,17,17,9,5
    month_weights       {"1".."12": factor}, missing months 1.0 (Oct-Dec event
                        season lift; storylines may carry their own `month_weights`)
    holiday_leak        factor on regular holidays for non-subscription items,
                        default 0.05 (after-the-fact logging)
    special_day_factor  factor on special (non-working) days, default 0.5
    holy_week_factor    factor on the other days of Holy Week (Palm Sunday to
                        Easter), default 1.0; Maundy Thursday and Good Friday
                        are regular holidays and Black Saturday a special day
    month_end           {"days": N, "factor": f, "business_days": b}: last N days of a
                        month (with `business_days: true`, its last N business days:
                        no weekends or holidays), default 3 / 1.0 / false
    daily_ceiling       soft cap as a multiple of the average daily row count, default 3.0

Subscription-class items ignore holidays, Holy Week and month-end here: their
archetype pins them to an anchor day and uses `roll_forward` (FR-E8).

`apply_ceiling` runs after calibration and moves rows off days above the soft
ceiling (FR-E5, per storyline) to the nearest day where the item's gap rules
still hold.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Iterator, Mapping, Sequence

from txns import holidays
from txns.holidays import HolidayCalendar

# Monday..Sunday, the ledger's weekday shape (FR-E6); bundles may override per archetype.
DEFAULT_WEEKDAY_WEIGHTS = (17, 17, 17, 17, 17, 9, 5)
DEFAULT_HOLIDAY_LEAK = 0.05
DEFAULT_SPECIAL_DAY_FACTOR = 0.5
DEFAULT_HOLY_WEEK_FACTOR = 1.0
DEFAULT_MONTH_END = {"days": 3, "factor": 1.0}
DEFAULT_DAILY_CEILING = 3.0
# Rows with these tags are not "ordinary": they neither count toward nor move under the ceiling.
CEILING_EXEMPT_TAGS = ("batch", "duplicate", "party")
MAX_SHIFT_DAYS = 7
# Archetypes whose dates are a schedule (anchor day, period end +- jitter, FR-E4, FR-E8):
# their rows count toward the ceiling but never move under it.
SCHEDULED_ARCHETYPES = ("fixed_day_subscription", "periodic_top_up")


def days(start: date, end: date) -> Iterator[date]:
    d = start
    one = timedelta(days=1)
    while d <= end:
        yield d
        d += one


def month_end_days(d: date, n: int) -> bool:
    """True when d is within the last n days of its month."""
    return (d + timedelta(days=n)).month != d.month


def is_business_day(d: date, cal: HolidayCalendar) -> bool:
    return d.weekday() < 5 and cal.get(d) is None


def month_end_business_days(d: date, n: int, cal: HolidayCalendar) -> bool:
    """True when d is one of the last n business days of its month."""
    if not is_business_day(d, cal):
        return False
    later, one = 0, timedelta(days=1)
    nxt = d + one
    while nxt.month == d.month:
        if is_business_day(nxt, cal):
            later += 1
            if later >= n:
                return False
        nxt += one
    return True


def roll_forward(d: date, cal: HolidayCalendar) -> date:
    """d, or the next day that is neither a weekend nor a holiday (FR-E8)."""
    while not is_business_day(d, cal):
        d += timedelta(days=1)
    return d


def calendar_rules(rules: Mapping[str, Any]) -> Mapping[str, Any]:
    return rules.get("calendar", {}) or {}


def _lookup(key: str, *tables: Mapping[str, Any], default: Any) -> Any:
    for t in tables:
        if t and key in t and t[key] is not None:
            return t[key]
    return default


@dataclass(frozen=True)
class DayShape:
    weekday: tuple[float, ...]  # Mon..Sun, mean 1.0
    months: tuple[float, ...]  # Jan..Dec
    holiday_leak: float
    special_day_factor: float
    holy_week_factor: float
    month_end_n: int
    month_end_factor: float
    calendar: HolidayCalendar
    month_end_business: bool = False
    _month_end: dict = field(default_factory=dict, compare=False, repr=False, init=False)  # day -> in the month-end window

    def in_month_end(self, d: date) -> bool:
        if not self.month_end_business:
            return month_end_days(d, self.month_end_n)
        hit = self._month_end.get(d)
        if hit is None:
            hit = self._month_end[d] = month_end_business_days(d, self.month_end_n, self.calendar)
        return hit

    def weight(self, d: date) -> float:
        w = self.weekday[d.weekday()] * self.months[d.month - 1]
        kind = self.calendar.get(d)
        if kind is not None:
            w *= self.holiday_leak if kind.kind == "regular" else self.special_day_factor
        elif self.holy_week_factor != 1.0:
            first, last = holidays.holy_week(d.year)
            if first <= d <= last:
                w *= self.holy_week_factor
        if self.month_end_n and self.month_end_factor != 1.0 and self.in_month_end(d):
            w *= self.month_end_factor
        return w


def weekday_weights(values: Sequence[float]) -> tuple[float, ...]:
    """Normalise Mon..Sun weights to a mean of 1.0."""
    values = tuple(float(v) for v in values)
    total = sum(values)
    if len(values) != 7 or total <= 0 or min(values) < 0:
        raise ValueError(f"weekday_weights must be 7 non-negative numbers with a positive sum, got {list(values)}")
    return tuple(v * 7 / total for v in values)


def _months(table: Mapping[str, Any] | None) -> tuple[float, ...]:
    table = table or {}
    return tuple(float(table.get(str(m), 1.0)) for m in range(1, 13))


def day_shape(ctx, item) -> DayShape:
    """The item's day shape from its storyline, archetype and the bundle's calendar rules."""
    bundle = ctx.bundle
    arch = bundle.archetype_rules(item.archetype)
    cal = calendar_rules(bundle.rules)
    story = bundle.storylines.get(item.storyline, {})
    month_end = _lookup("month_end", arch, cal, default=DEFAULT_MONTH_END)
    shape = DayShape(
        weekday=weekday_weights(_lookup("weekday_weights", arch, cal, default=DEFAULT_WEEKDAY_WEIGHTS)),
        months=_months(_lookup("month_weights", story, arch, cal, default=None)),
        holiday_leak=float(_lookup("holiday_leak", arch, cal, default=DEFAULT_HOLIDAY_LEAK)),
        special_day_factor=float(_lookup("special_day_factor", arch, cal, default=DEFAULT_SPECIAL_DAY_FACTOR)),
        holy_week_factor=float(_lookup("holy_week_factor", arch, cal, default=DEFAULT_HOLY_WEEK_FACTOR)),
        month_end_n=int(month_end.get("days", DEFAULT_MONTH_END["days"])),
        month_end_factor=float(month_end.get("factor", DEFAULT_MONTH_END["factor"])),
        calendar=bundle.calendar,
        month_end_business=month_end.get("business_days") is True,
    )
    if item.price_class == "subscription":
        shape = replace(shape, holiday_leak=1.0, special_day_factor=1.0, holy_week_factor=1.0, month_end_factor=1.0)
    return shape


def coverage_warning(calendar: HolidayCalendar, start: date, end: date) -> str | None:
    missing = calendar.missing_years(start, end)
    if not missing:
        return None
    have = f"{calendar.years[0]}-{calendar.years[-1]}" if calendar.years else "no years"
    return (
        f"bundle holiday calendar covers {have}; no holiday shaping for "
        f"{', '.join(map(str, missing))} (add the year to inputs/ph-holidays.json and re-author)"
    )


# ---- soft daily ceiling (FR-E5) -------------------------------------------------


def is_ordinary(row) -> bool:
    return not any(t in CEILING_EXEMPT_TAGS for t in row.tags)


def ceiling(rows: Sequence, start: date, end: date, ratio: float) -> int:
    """Most ordinary rows a day may hold: ceil(ratio x average ordinary rows per day)."""
    n_days = (end - start).days + 1
    n = sum(1 for r in rows if is_ordinary(r))
    return max(1, math.ceil(ratio * n / n_days)) if n_days > 0 else 1


def storyline_ceilings(rows: Sequence, start: date, end: date, ratio: float) -> dict[str | None, int]:
    """The ceiling of each storyline that has ordinary rows: `ceiling` over its own rows."""
    groups: dict[str | None, list] = defaultdict(list)
    for r in rows:
        if is_ordinary(r):
            groups[r.storyline].append(r)
    return {s: ceiling(g, start, end, ratio) for s, g in groups.items()}


def ceiling_ratio(rules: Mapping[str, Any]) -> float:
    return float(calendar_rules(rules).get("daily_ceiling", DEFAULT_DAILY_CEILING))


def apply_ceiling(ctx, rows: list) -> list:
    """Move ordinary rows off days above the soft ceiling (FR-E5).

    The ceiling holds per storyline: a storyline's ordinary rows on one day stay
    within `daily_ceiling` x that storyline's average ordinary rows a day, so
    adding or removing another storyline never moves this one's rows (T5). The
    storylines' caps add up to about the same multiple of the whole file's
    average (each rounds up), which the scorecard's `daily_ceiling` check measures.

    Only untagged rows of non-subscription, unscheduled items move (tagged rows
    belong to a structure such as a deposit/balance pair; subscriptions and
    top-ups keep their schedule, `SCHEDULED_ARCHETYPES`). A row moves to the nearest day
    (later first) within MAX_SHIFT_DAYS that is inside the period, has weight
    for the item (weekday, not a regular holiday), stays below its storyline's
    ceiling, keeps the item's gap rules and has the same rate card as the row's
    day (a move never crosses a price step, so the drawn amount stays valid and
    the calibrated total is kept). A row with no such day stays: the ceiling is
    soft. Draws are on the storyline's own `ceiling` stream.
    """
    period = ctx.config.period
    caps = storyline_ceilings(rows, period.start, period.end, ceiling_ratio(ctx.bundle.rules))
    by_storyline: dict[str | None, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if is_ordinary(r):
            by_storyline[r.storyline].append(i)
    out = list(rows)
    item_days: dict[str, Counter] | None = None
    shapes: dict[str, DayShape] = {}
    for storyline in sorted(by_storyline, key=lambda s: (s is None, s or "")):
        idx = by_storyline[storyline]
        cap = caps[storyline]
        per_day = Counter(rows[i].date for i in idx)
        if storyline is None or max(per_day.values()) <= cap:
            continue  # rows without a storyline (not from the engine) never move
        if item_days is None:
            item_days = defaultdict(Counter)
            for r in rows:
                if r.item_id is not None:
                    item_days[r.item_id][r.date] += 1
        stream = ctx.stream("storyline", storyline, "ceiling")
        by_day: dict[date, list[int]] = defaultdict(list)
        for i in idx:
            by_day[out[i].date].append(i)
        for day in sorted(d for d, c in per_day.items() if c > cap):
            movable = [i for i in by_day[day] if _movable(ctx, out[i])]
            stream.shuffle(movable)
            excess = per_day[day] - cap
            for i in movable:
                if excess <= 0:
                    break
                row = out[i]
                item = ctx.bundle.items[row.item_id]
                shape = shapes.setdefault(item.id, day_shape(ctx, item))
                target = _target(ctx, item, shape, row.date, item_days[item.id], per_day, cap, period)
                if target is None:
                    continue
                out[i] = replace(row, date=target)
                item_days[item.id][row.date] -= 1
                item_days[item.id][target] += 1
                per_day[row.date] -= 1
                per_day[target] += 1
                excess -= 1
    return out


def _movable(ctx, row) -> bool:
    if row.tags or row.item_id is None or row.item_id not in ctx.bundle.items:
        return False
    item = ctx.bundle.items[row.item_id]
    return item.price_class != "subscription" and item.archetype not in SCHEDULED_ARCHETYPES


def _target(ctx, item, shape: DayShape, day: date, mine: Counter, per_day: Counter, cap: int, period):
    max_per_day, min_gap = ctx.bundle.gap_rules(item)
    card = item.points_on(day)
    others = [d for d, c in mine.items() if c > 0 and d != day] + ([day] if mine[day] > 1 else [])
    for k in range(1, MAX_SHIFT_DAYS + 1):
        for cand in (day + timedelta(days=k), day - timedelta(days=k)):
            if not (period.start <= cand <= period.end) or per_day[cand] >= cap:
                continue
            if shape.weekday[cand.weekday()] <= 0 or shape.calendar.get(cand) is not None:
                continue
            if item.price_class == "big_ticket" and cand.weekday() >= 5:  # FR-E6
                continue
            if mine[cand] >= max_per_day:
                continue
            if item.points_on(cand) != card:  # FR-F2: never across a price step (amounts are never re-priced)
                continue
            if all(d == cand or abs((cand - d).days) >= min_gap for d in others):
                return cand
    return None
