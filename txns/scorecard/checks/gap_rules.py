"""Gap rules (FR-E5, FR-I5, T21): per item, at most `max_per_day` rows on a day
and at least `min_gap_days` between consecutive days (`Bundle.gap_rules`). Hard.

Rows tagged "duplicate" (same-day batch-entry duplicates, ticket 11) are left
out here; the duplicates check judges them.
"""

from collections import Counter

from txns.scorecard.registry import FAIL, PASS, CheckResult, ScoreContext, check, listing, result

EXEMPT_TAGS = ("duplicate",)


@check("gap_rules", "timing", hard=True)
def gap_rules(ctx: ScoreContext) -> CheckResult:
    bad = []
    for item_id, rows in ctx.by_item.items():
        max_per_day, min_gap = ctx.bundle.gap_rules(ctx.bundle.items[item_id])
        per_day = Counter(r.date for r in rows if not any(t in EXEMPT_TAGS for t in r.tags))
        days = sorted(per_day)
        for day in days:
            if per_day[day] > max_per_day:
                bad.append(f"{item_id}: {per_day[day]} rows on {day} (max {max_per_day})")
        for a, b in zip(days, days[1:]):
            if (b - a).days < min_gap:
                bad.append(f"{item_id}: {a} to {b} is {(b - a).days} day(s) (min {min_gap})")
    if bad:
        return result("gap_rules", FAIL, f"{len(bad)} gap-rule violation(s): {listing(bad)}", len(bad), 0)
    return result("gap_rules", PASS, "per-day caps and minimum gaps hold for every item", 0, 0)
