"""`reference.json`: the ledger figures the scorecard compares with (FR-B4, FR-I1).

Schema: one JSON object in the bundle. Every key is optional and names one
metric; unknown keys are ignored. A check whose key is absent passes its
metric marked "no ledger reference", so keys can be added one at a time.
Money is integer centavos, shares are fractions 0..1, counts are numbers.
Per-category figures are keyed by the catalog `category` string.

Keys in use (add yours here, one line each, in alphabetical order):

    distinct_per_item       {category: {"prices": float, "quantities": float}}
                            mean distinct unit prices / quantities per item
    holiday_share           float, share of non-subscription rows dated on a regular
                            holiday (`holiday_share`, calendar from the bundle)
    month_end_share         float, share of rows in the last MONTH_END_DAYS days of
                            their month (`month_end_share`)
    monthly_spread          {"rows": float, "spend": float}: coefficient of variation
                            of per-day monthly row count / spend, averaged over
                            calendar quarters (`monthly_spread`)
    round_amount_share      float, share of rows whose amount is a whole ₱100
                            (`txns.money.is_round_amount`)
    row_amount_percentiles  {"p10": int, "p25": int, "p50": int, "p75": int, "p90": int}
                            row amount (qty x unit_price) in centavos, nearest rank
    terse_share             {category: float}, share of the category's rows whose
                            item text is terse (short, unattributed, e.g. "Coffee");
                            the engine draws terse text at this rate (FR-H1)
    weekday_shares          {"mon": float, ..., "sun": float}, share of rows per weekday
    whole_peso_share        float, share of rows whose unit price has no cents
                            (`txns.money.is_whole_peso`)

The functions below are the one definition of each measure: the scorecard
applies them to generated rows and the ledger reader (ticket 12) to ledger
rows, so both sides always measure the same thing.
"""

from __future__ import annotations

import calendar as _calendar
import math
import statistics
from collections import Counter, defaultdict
from datetime import date
from typing import Iterable, Mapping, Sequence

from txns.money import is_round_amount, is_whole_peso

PERCENTILES = (10, 25, 50, 75, 90)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MONTH_END_DAYS = 3


def percentile(values: Sequence, p: float):
    """Nearest-rank percentile (always an observed value, so centavos stay integers)."""
    if not values:
        raise ValueError("percentile of no values")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def row_amount_percentiles(amounts: Sequence) -> dict[str, int]:
    return {f"p{p}": int(percentile(amounts, p)) for p in PERCENTILES}


def round_amount_share(amounts: Sequence) -> float | None:
    if not amounts:
        return None
    return sum(1 for a in amounts if is_round_amount(a)) / len(amounts)


def whole_peso_share(unit_prices: Sequence) -> float | None:
    if not unit_prices:
        return None
    return sum(1 for p in unit_prices if is_whole_peso(p)) / len(unit_prices)


def distinct_per_item(rows: Iterable[tuple[str, str, object, object]]) -> dict[str, dict[str, float]]:
    """Rows as (category, item key, unit_price, qty) -> per-category mean distinct counts per item.

    The item key is the catalog item id (generated rows) or the item text (ledger).
    """
    prices: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    qtys: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for category, key, unit_price, qty in rows:
        prices[category][key].add(unit_price)
        qtys[category][key].add(qty)
    return {
        cat: {
            "prices": _mean(len(s) for s in prices[cat].values()),
            "quantities": _mean(len(s) for s in qtys[cat].values()),
        }
        for cat in sorted(prices)
    }


def terse_share(rows: Iterable[tuple[str, bool]]) -> dict[str, float]:
    """Rows as (category, is_terse) -> per-category share of terse rows.

    Generated rows are classed by the bundle variant their text is; the ledger
    reader (ticket 12) supplies its own terse/descriptive classification.
    """
    seen: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for category, is_terse in rows:
        seen[category][0] += 1
        seen[category][1] += 1 if is_terse else 0
    return {cat: round(seen[cat][1] / seen[cat][0], 4) for cat in sorted(seen)}


def _mean(counts: Iterable[int]) -> float:
    xs = list(counts)
    return round(sum(xs) / len(xs), 4)


def weekday_shares(dates: Sequence[date]) -> dict[str, float] | None:
    if not dates:
        return None
    c = Counter(d.weekday() for d in dates)
    return {name: round(c[i] / len(dates), 4) for i, name in enumerate(WEEKDAYS)}


def is_month_end(d: date, days: int = MONTH_END_DAYS) -> bool:
    return d.day > _calendar.monthrange(d.year, d.month)[1] - days


def month_end_share(dates: Sequence[date], days: int = MONTH_END_DAYS) -> float | None:
    if not dates:
        return None
    return round(sum(1 for d in dates if is_month_end(d, days)) / len(dates), 4)


def holiday_share(rows: Iterable[tuple[date, bool]], regular_holidays: Iterable[date]) -> tuple[float | None, int]:
    """Rows as (date, is_subscription) -> (share of non-subscription rows on a regular holiday, their count)."""
    hol = set(regular_holidays)
    dates = [d for d, subscription in rows if not subscription]
    if not dates:
        return None, 0
    return round(sum(1 for d in dates if d in hol) / len(dates), 4), len(dates)


def quarter_months(start: date, end: date) -> list[list[tuple[int, int]]]:
    """Full calendar months inside start..end, grouped by calendar quarter; quarters with < 2 full months dropped."""
    groups: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last = _calendar.monthrange(y, m)[1]
        if date(y, m, 1) >= start and date(y, m, last) <= end:
            groups[(y, (m - 1) // 3)].append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return [groups[k] for k in sorted(groups) if len(groups[k]) >= 2]


def monthly_spread(rows: Iterable[tuple[date, int]], start: date, end: date) -> tuple[dict[str, float] | None, int]:
    """Rows as (date, amount) -> ({"rows": cv, "spend": cv}, degrees of freedom).

    Per full month, rows and spend are divided by the month's days; the
    coefficient of variation (population) is taken within each calendar
    quarter and averaged over quarters, so a one-quarter run and three ledger
    years measure the same thing. Needs a quarter with 2+ full months.
    """
    quarters = quarter_months(start, end)
    if not quarters:
        return None, 0
    count: Counter = Counter()
    spend: Counter = Counter()
    for d, amount in rows:
        count[(d.year, d.month)] += 1
        spend[(d.year, d.month)] += amount
    cvs: dict[str, list[float]] = {"rows": [], "spend": []}
    for months in quarters:
        for key, table in (("rows", count), ("spend", spend)):
            per_day = [table[ym] / _calendar.monthrange(*ym)[1] for ym in months]
            mean = statistics.fmean(per_day)
            cvs[key].append(statistics.pstdev(per_day) / mean if mean else 0.0)
    dof = sum(len(q) - 1 for q in quarters)
    return {k: round(statistics.fmean(v), 4) for k, v in cvs.items()}, dof


def figure(reference: Mapping, *path: str):
    """reference[path[0]][path[1]]..., or None when any step is missing."""
    node = reference
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return node
