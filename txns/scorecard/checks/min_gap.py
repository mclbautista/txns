"""Minimum gap per item (FR-I2, FR-E5): the shortest observed gap in days between
an item's rows on different days, next to the item's rule (`Bundle.gap_rules`).
Report only: a gap below the rule is a hard `gap_rules` failure, so this metric
warns at most. Items with rows on fewer than two days are left out."""

from txns.scorecard.checks.gap_rules import EXEMPT_TAGS
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result


@check("min_gap", "timing")
def min_gap(ctx: ScoreContext) -> CheckResult:
    observed: dict[str, int] = {}
    rule: dict[str, int] = {}
    for item_id, rows in ctx.by_item.items():
        days = sorted({r.date for r in rows if not any(t in EXEMPT_TAGS for t in r.tags)})
        if len(days) < 2:
            continue
        observed[item_id] = min((b - a).days for a, b in zip(days, days[1:]))
        rule[item_id] = ctx.bundle.gap_rules(ctx.bundle.items[item_id])[1]
    if not observed:
        return result("min_gap", PASS, "no item has rows on two or more days", {}, {})
    short = [f"{i} {observed[i]}d (min {rule[i]})" for i in observed if observed[i] < rule[i]]
    if short:
        return result(
            "min_gap", WARN, f"{len(short)} item(s) below their minimum gap: {listing(short)}", observed, rule
        )
    tightest = min(observed, key=lambda i: (observed[i] - rule[i], i))
    detail = (
        f"every item keeps its minimum gap ({len(observed)} items; closest to its rule: "
        f"{tightest} {observed[tightest]}d, min {rule[tightest]})"
    )
    return result("min_gap", PASS, detail, observed, rule)
