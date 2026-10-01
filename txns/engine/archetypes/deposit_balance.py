"""Deposit-then-balance pair (FR-E3, FR-E4): an event booked with a deposit, settled weeks later.

One catalog item (a venue, catering, an equipment rental) gives two rows per
event: its deposit, then its balance `offset_days` [min, max] later (item
params, else `rules.archetypes.deposit_balance`, default 14 to 42 days; checked
at load in `txns.bundle.events`). The rate card has the deposit figure as point
0 and the balance figure as point 1 (a card with one point settles both at that
figure). Per event:

- the deposit falls on a day with chance `per_quarter` / 91.3125 times the
  item's day shape (`calendar.day_shape`);
- the balance day is picked among the days `offset_days` later by the same
  day shape;
- both land inside the period (an event whose balance would fall after the
  period end, or on no allowed day, is not planned), so every balance row in
  the output follows its deposit by the configured offset (T24);
- the item's gap rules hold over all its rows: one row a day, and at least
  `min_gap_days` between any two of its rows (deposits and balances alike).

Big-ticket items land on weekdays only (FR-E6). Rows are tagged "deposit" /
"balance": the daily ceiling never moves them (that would break the offset)
and the messiness stage never duplicates them. A balance occurrence carries
its deposit's day (`Occurrence.parent_date`), so the drawer gives it the
deposit's quantity (a two-day venue deposit is settled for two days), also
after a scope change.

Item params:   per_quarter (float, expected events per quarter, default 1.0)
               offset_days ([min, max] days from deposit to balance)
Levers:        occurrences via per_quarter (retail items), quantities
               (big-ticket: scope, FR-G2 step 4). Never used for closing:
               adding or dropping one row would break a pair.

Two draws a period day on the item's `dates` stream whatever the rate.
"""

from __future__ import annotations

from datetime import date, timedelta

from txns.bundle import events
from txns.engine import calendar
from txns.engine.archetypes import register
from txns.engine.levers import Levers
from txns.engine.rows import Occurrence
from txns.errors import BundleInvalid

DAYS_PER_QUARTER = 365.25 / 4
DEFAULT_PER_QUARTER = 1.0
DEPOSIT, BALANCE = "deposit", "balance"


def _per_quarter(item) -> float:
    value = item.params.get("per_quarter", DEFAULT_PER_QUARTER)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise BundleInvalid(f"bundle invalid: item `{item.id}`: param `per_quarter` must be a number >= 0")
    return float(value)


def pairs(ctx, item, stream) -> list[tuple[date, date]]:
    """(deposit day, balance day) of each of the item's events in the period, by deposit day."""
    lo, hi = events.offset_days(ctx.bundle, item)
    shape = calendar.day_shape(ctx, item)
    _, min_gap = ctx.bundle.gap_rules(item)
    per_day = _per_quarter(item) / DAYS_PER_QUARTER
    weekdays_only = item.price_class == "big_ticket"
    period = ctx.config.period

    def weight(d: date) -> float:
        return 0.0 if weekdays_only and d.weekday() >= 5 else shape.weight(d)

    def free(d: date, taken: list[date]) -> bool:
        return all(abs((d - t).days) >= min_gap for t in taken)

    out: list[tuple[date, date]] = []
    taken: list[date] = []  # every deposit and balance day so far
    for day in calendar.days(period.start, period.end):
        u, v = stream.uniform(), stream.uniform()  # always two draws a day
        if u >= min(1.0, per_day * weight(day)) or not free(day, taken):
            continue
        options = []
        for k in range(lo, hi + 1):
            cand = day + timedelta(days=k)
            if cand > period.end:
                break
            w = weight(cand)
            if w > 0 and free(cand, taken + [day]):
                options.append((cand, w))
        if not options:
            continue
        pick = v * sum(w for _, w in options)
        balance = options[-1][0]
        for cand, w in options:
            pick -= w
            if pick < 0:
                balance = cand
                break
        out.append((day, balance))
        taken += [day, balance]
    return out


@register("deposit_balance", levers=Levers(rate_params={"per_quarter": DEFAULT_PER_QUARTER}))
def plan(item, ctx, stream):
    last = len(item.price_points) - 1
    out = []
    for deposit, balance in pairs(ctx, item, stream):
        out.append(Occurrence(item.id, item.storyline, deposit, (DEPOSIT,), price_point=0))
        out.append(Occurrence(item.id, item.storyline, balance, (BALANCE,), parent_date=deposit, price_point=last))
    return out
