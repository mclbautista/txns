"""Per-item outliers on amount and unit price (FR-I4 (4)). Absolute rule, warn only.

For each item with at least `outlier_min_rows` rows (default 8), a row is an
outlier when log(value) lies beyond Tukey's far-out fences of that item's rows:
below Q1 - k*IQR or above Q3 + k*IQR, k = `outlier_k` (default 3), with the
IQR floored at log(2) so an item whose rows are nearly all one value does not
flag its ordinary pack sizes. With the defaults that is "more than 8x the
item's upper quartile, or under 1/8 of its lower quartile". The metric warns
when more than `outlier_max_share` (default 2%) of the rows it looked at are
outliers. Thresholds come from rules.json `scorecard`.
"""

import math

from txns.money import format_pesos
from txns.scorecard.reference import percentile
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result

DEFAULT_MIN_ROWS = 8
DEFAULT_K = 3.0
DEFAULT_MAX_SHARE = 0.02
MIN_IQR = math.log(2)


def fences(values: list[float], k: float) -> tuple[float, float]:
    """(low, high) bounds on log(value)."""
    logs = [math.log(v) for v in values]
    q1, q3 = percentile(logs, 25), percentile(logs, 75)
    iqr = max(q3 - q1, MIN_IQR)
    return q1 - k * iqr, q3 + k * iqr


@check("outliers", "anomaly")
def outliers(ctx: ScoreContext) -> list[CheckResult]:
    min_rows = int(ctx.rule("outlier_min_rows", DEFAULT_MIN_ROWS))
    k = float(ctx.rule("outlier_k", DEFAULT_K))
    max_share = float(ctx.rule("outlier_max_share", DEFAULT_MAX_SHARE))
    out = []
    for measure, get in (("amount", lambda r: r.amount), ("unit_price", lambda r: r.unit_price)):
        looked = 0
        found = []
        for item_id, rows in ctx.by_item.items():
            values = [float(get(r)) for r in rows]
            if len(values) < min_rows or min(values) <= 0:
                continue
            looked += len(values)
            lo, hi = fences(values, k)
            for row, v in zip(rows, values):
                if not lo <= math.log(v) <= hi:
                    found.append(f"{item_id} {row.date} {format_pesos(int(v))}")
        name = f"outliers.{measure}"
        if not looked:
            out.append(result(name, PASS, f"{measure} outliers: no item has {min_rows}+ rows", 0, max_share))
            continue
        share = round(len(found) / looked, 6)
        status = PASS if share <= max_share else WARN
        detail = f"{len(found)} of {looked} rows are per-item {measure} outliers ({share:.1%}, max {max_share:.0%})"
        if found:
            detail += f": {listing(found)}"
        out.append(result(name, status, detail, share, max_share))
    return out
