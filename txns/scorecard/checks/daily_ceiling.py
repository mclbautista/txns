"""Soft daily ceiling (FR-E5): no ordinary day holds more than about
rules.json `calendar.daily_ceiling` (default 3) x the average daily row count.
Batch-logged, duplicate and party rows are not ordinary. Absolute rule, warn only."""

from collections import Counter

from txns.engine import calendar
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result


@check("daily_ceiling", "timing")
def daily_ceiling(ctx: ScoreContext) -> CheckResult:
    span = ctx.span
    if span is None:
        return result("daily_ceiling", PASS, "no rows", 0, None)
    ratio = calendar.ceiling_ratio(ctx.bundle.rules)
    cap = calendar.ceiling(ctx.rows, *span, ratio)
    per_day = Counter(r.date for r in ctx.rows if calendar.is_ordinary(r))
    busiest = max(per_day.values(), default=0)
    over = sorted(d for d, c in per_day.items() if c > cap)
    if over:
        days = [f"{d} ({per_day[d]})" for d in over]
        return result(
            "daily_ceiling", WARN, f"{len(over)} day(s) above {cap} rows ({ratio:g}x average): {listing(days)}", busiest, cap
        )
    return result("daily_ceiling", PASS, f"busiest ordinary day {busiest} rows, ceiling {cap} ({ratio:g}x average)", busiest, cap)
