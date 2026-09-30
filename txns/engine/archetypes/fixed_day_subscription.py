"""Fixed-day subscription: a charge on the bundle's anchor day every cycle (FR-E4, FR-E8).

Item params (the bundle fixes the schedule, FR-E9):
    anchor_day     1..31, required; clamped to the last day of shorter months
    every_months   cycle length in months, default 1 (3 = quarterly, 12 = yearly)
    anchor_month   1..12, a month the cycle bills in, default 1 (phase for every_months > 1)
    slip_share, slip_days, skip_share, double_share   per-item overrides of the rules below
Bundle rules (rules.archetypes.fixed_day_subscription, all optional):
    slip_share     share of charges that post 1-2 days late, default 0.04
    slip_days      [min, max] days of a slip, default [1, 2]
    skip_share     share of cycles with no charge (a skipped month), default 0.01
    double_share   share of charges posted early, up to EARLY_MAX_DAYS before the
                   anchor; an early-month anchor then lands in the previous
                   month (a doubled month), default 0.01
    min_gap_days   default 25 (`Bundle.gap_rules`; items may override)

Each cycle's charge posts on the anchor day rolled forward over weekends and
holidays (`calendar.roll_forward`), so subscriptions ignore weekday weights
(FR-E6). The seed moves only slips, skips and early postings. A slip or early
posting that would break the minimum gap to a neighbouring charge is not
taken; the charge stays on its rolled-forward anchor. Seats (qty) are the
quantity lever (FR-F3, FR-G2); the occurrence count never scales.
"""

from __future__ import annotations

import calendar as pycal
from datetime import date, timedelta
from typing import Any

from txns.engine import calendar
from txns.engine.archetypes import register
from txns.engine.levers import Levers
from txns.engine.rows import Occurrence
from txns.errors import BundleInvalid

NAME = "fixed_day_subscription"
DEFAULTS = {"slip_share": 0.04, "slip_days": (1, 2), "skip_share": 0.01, "double_share": 0.01}
EARLY_MAX_DAYS = 7


def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _anchor_on(month_index: int, anchor_day: int) -> date:
    year, month = divmod(month_index, 12)
    month += 1
    return date(year, month, min(anchor_day, pycal.monthrange(year, month)[1]))


def schedule(item) -> tuple[int, int, int]:
    """(anchor_day, every_months, anchor_month) from the item's params; bad values exit 4."""
    p = item.params
    where = f"bundle invalid: item `{item.id}` ({NAME})"
    day = p.get("anchor_day")
    if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31:
        raise BundleInvalid(f"{where}: params.anchor_day must be a whole day of the month, 1 to 31")
    every = p.get("every_months", 1)
    if not isinstance(every, int) or isinstance(every, bool) or not 1 <= every <= 12:
        raise BundleInvalid(f"{where}: params.every_months must be a whole number of months, 1 to 12")
    month = p.get("anchor_month", 1)
    if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
        raise BundleInvalid(f"{where}: params.anchor_month must be 1 to 12")
    return day, every, month


def anchors(item, first: date, last: date) -> list[date]:
    """The item's anchor dates (before roll-forward) in cycles billing from `first`'s month to `last`'s."""
    day, every, month = schedule(item)
    out = []
    for m in range(_month_index(first), _month_index(last) + 1):
        if (m - (month - 1)) % every == 0:
            out.append(_anchor_on(m, day))
    return out


def on_schedule(item, cal, start: date, end: date) -> set[date]:
    """Dates a charge of this item may land on and count as on time: each anchor and its roll-forward."""
    days: set[date] = set()
    for a in anchors(item, start - timedelta(days=31), end):
        days.add(a)
        days.add(calendar.roll_forward(a, cal))
    return days


def _rule(item, rules, key: str) -> Any:
    return item.params.get(key, rules.get(key, DEFAULTS[key]))


@register(NAME, levers=Levers(rate_params={}, quantities=True, closing=False))
def plan(item, ctx, stream):
    cal = ctx.bundle.calendar
    rules = ctx.bundle.archetype_rules(NAME)
    slip_share = float(_rule(item, rules, "slip_share"))
    slip_lo, slip_hi = (int(x) for x in _rule(item, rules, "slip_days"))
    skip_share = float(_rule(item, rules, "skip_share"))
    double_share = float(_rule(item, rules, "double_share"))
    min_gap = max(1, ctx.bundle.gap_rules(item)[1])
    period = ctx.config.period
    _, every, _ = schedule(item)

    # Two cycles before the period (a late charge may land inside it, and gaps
    # to it hold), one after (the last charge keeps its gap to the next).
    first = date(period.start.year, period.start.month, 1) - timedelta(days=31 * (2 * every))
    last = date(period.end.year, period.end.month, 28) + timedelta(days=31 * every)
    bases = [calendar.roll_forward(a, cal) for a in anchors(item, first, last)]

    out = []
    prev: date | None = None
    for k, base in enumerate(bases):
        u = stream.uniform()  # two draws per cycle, whatever happens, so cycles stay aligned
        slip = stream.randint(slip_lo, slip_hi)
        nxt = bases[k + 1] if k + 1 < len(bases) else None
        if u < skip_share:
            continue
        day = base
        if u < skip_share + double_share:
            day = _early(base, prev, min_gap, cal)
        elif u < skip_share + double_share + slip_share:
            late = calendar.roll_forward(base + timedelta(days=slip), cal)
            if nxt is None or (nxt - late).days >= min_gap:
                day = late
        if prev is not None and (day - prev).days < min_gap:
            # Anchors themselves too close (a long holiday run before the last
            # one): the charge waits until the gap holds.
            day = calendar.roll_forward(prev + timedelta(days=min_gap), cal)
        prev = day
        if period.start <= day <= period.end:
            out.append(Occurrence(item.id, item.storyline, day))
    return out


def _early(base: date, prev: date | None, min_gap: int, cal) -> date:
    """The earliest business day up to EARLY_MAX_DAYS before `base` that keeps the gap to `prev`."""
    for back in range(EARLY_MAX_DAYS, 0, -1):
        d = base - timedelta(days=back)
        if calendar.is_business_day(d, cal) and (prev is None or (d - prev).days >= min_gap):
            return d
    return base
