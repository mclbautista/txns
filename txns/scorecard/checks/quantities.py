"""Quantities in allowed sets (FR-F3, FR-I3, T17): every qty is in its item's
weighted allowed set. Absolute rule, warn only."""

from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result


@check("quantities", "price_quantity")
def quantities(ctx: ScoreContext) -> CheckResult:
    bad = []
    for item_id, rows in ctx.by_item.items():
        allowed = {q.qty for q in ctx.bundle.items[item_id].quantities if q.weight > 0}
        for row in rows:
            if row.qty not in allowed:
                bad.append(f"{item_id} qty {row.qty} on {row.date} (allowed {', '.join(map(str, sorted(allowed)))})")
    if bad:
        return result("quantities", WARN, f"{len(bad)} row(s) outside the allowed quantity set: {listing(bad)}", len(bad), 0)
    return result("quantities", PASS, "every qty is in its item's allowed set", 0, 0)
