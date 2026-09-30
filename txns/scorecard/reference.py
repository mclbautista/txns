"""`reference.json`: the ledger figures the scorecard compares with (FR-B4, FR-I1).

Schema: one JSON object in the bundle. Every key is optional and names one
metric; unknown keys are ignored. A check whose key is absent passes its
metric marked "no ledger reference", so keys can be added one at a time.
Money is integer centavos, shares are fractions 0..1, counts are numbers.
Per-category figures are keyed by the catalog `category` string.

Keys in use (add yours here, one line each, in alphabetical order):

    distinct_per_item       {category: {"prices": float, "quantities": float}}
                            mean distinct unit prices / quantities per item
    round_amount_share      float, share of rows whose amount is a whole ₱100
                            (`txns.money.is_round_amount`)
    row_amount_percentiles  {"p10": int, "p25": int, "p50": int, "p75": int, "p90": int}
                            row amount (qty x unit_price) in centavos, nearest rank

The functions below are the one definition of each measure: the scorecard
applies them to generated rows and the ledger reader (ticket 12) to ledger
rows, so both sides always measure the same thing.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Mapping, Sequence

from txns.money import is_round_amount

PERCENTILES = (10, 25, 50, 75, 90)


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


def _mean(counts: Iterable[int]) -> float:
    xs = list(counts)
    return round(sum(xs) / len(xs), 4)


def figure(reference: Mapping, *path: str):
    """reference[path[0]][path[1]]..., or None when any step is missing."""
    node = reference
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    return node
