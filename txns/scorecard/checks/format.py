"""Format check (FR-H5): every row obeys the CSV rules of FR-H2. Hard."""

from txns.scorecard.registry import FAIL, PASS, CheckResult, ScoreContext, check
from txns.writer import row_violations


@check("format", "format", hard=True)
def format_check(ctx: ScoreContext) -> CheckResult:
    bad = []
    for n, row in enumerate(ctx.rows, start=1):
        problems = row_violations(row)
        if problems:
            bad.append(f"row {n}: {'; '.join(problems)}")
    if bad:
        more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
        return CheckResult("format", "format", FAIL, True, "; ".join(bad[:3]) + more, value=len(bad), reference=0)
    return CheckResult("format", "format", PASS, True, "all rows obey the CSV rules", value=0, reference=0)
