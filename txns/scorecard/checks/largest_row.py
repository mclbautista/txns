"""Largest single row as a share of the total (FR-I3): at most rules.json
`scorecard.largest_row_max_share` (default 10%). Absolute rule, warn only."""

from txns.money import format_pesos
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, result

DEFAULT_MAX_SHARE = 0.10


@check("largest_row_share", "price_quantity")
def largest_row_share(ctx: ScoreContext) -> CheckResult:
    limit = float(ctx.rule("largest_row_max_share", DEFAULT_MAX_SHARE))
    if not ctx.rows or ctx.total <= 0:
        return result("largest_row_share", PASS, "largest row share: nothing to measure", None, limit)
    n, row = max(enumerate(ctx.rows, start=1), key=lambda nr: nr[1].amount)
    share = round(float(row.amount / ctx.total), 6)
    status = PASS if share <= limit else WARN
    detail = (
        f"largest row {format_pesos(int(row.amount))} (row {n}, {row.item_id or row.text}) "
        f"is {share:.1%} of the total (max {limit:.0%})"
    )
    return result("largest_row_share", status, detail, share, limit)
