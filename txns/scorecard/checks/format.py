"""Format check (FR-H5): every row obeys the CSV rules of FR-H2. Hard."""

from txns.scorecard.registry import FAIL, PASS, CheckResult, ScoreContext, check, listing, result
from txns.writer import row_violations


@check("format", "format", hard=True)
def format_check(ctx: ScoreContext) -> CheckResult:
    bad = []
    for n, row in enumerate(ctx.rows, start=1):
        item = ctx.item(row.item_id)
        problems = row_violations(row, decimal=item is not None and item.decimal)
        if problems:
            bad.append(f"row {n}: {'; '.join(problems)}")
    if bad:
        return result("format", FAIL, listing(bad), value=len(bad), reference=0)
    return result("format", PASS, "all rows obey the CSV rules", value=0, reference=0)
