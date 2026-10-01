"""Soft daily ceiling (FR-E5): no ordinary day holds more than about
rules.json `calendar.daily_ceiling` (default 3) x the average daily row count.
Batch-logged, duplicate and party rows are not ordinary. Absolute rule, warn only.

The engine holds the ceiling per storyline (`calendar.apply_ceiling`, so that adding
a storyline never moves another one's rows, T5), each storyline's cap rounded up.
The ceiling here is the sum of those caps (`calendar.storyline_ceilings`; rows without
a storyline, such as unmapped external rows, form one group): `daily_ceiling` x the
whole file's average, plus at most one row per storyline of rounding."""

from collections import Counter

from txns.engine import calendar
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result


@check("daily_ceiling", "timing")
def daily_ceiling(ctx: ScoreContext) -> CheckResult:
    span = ctx.span
    if span is None:
        return result("daily_ceiling", PASS, "no rows", 0, None)
    ratio = calendar.ceiling_ratio(ctx.bundle.rules)
    cap = sum(calendar.storyline_ceilings(ctx.rows, *span, ratio).values())
    per_day = Counter(r.date for r in ctx.rows if calendar.is_ordinary(r))
    busiest = max(per_day.values(), default=0)
    over = sorted(d for d, c in per_day.items() if c > cap)
    if over:
        days = [f"{d} ({per_day[d]})" for d in over]
        return result(
            "daily_ceiling", WARN, f"{len(over)} day(s) above {cap} rows ({ratio:g}x average): {listing(days)}", busiest, cap
        )
    return result("daily_ceiling", PASS, f"busiest ordinary day {busiest} rows, ceiling {cap} ({ratio:g}x average)", busiest, cap)
