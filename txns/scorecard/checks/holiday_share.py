"""Holiday share (FR-I2, FR-I4 (6), FR-E7, T23): share of non-subscription rows
on regular Philippine holidays (bundle calendar). Near zero is the goal, so the
figure passes at or below reference.json `holiday_share` and is graded above it
(`relative_noisy`, one-sided). Without a ledger figure the absolute rule is
rules.json `scorecard.holiday_share_max` (default 2%). Warn only."""

from txns.scorecard import reference as ref
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, relative_noisy, result, share_se

DEFAULT_MAX = 0.02


@check("holiday_share", "timing")
def holiday_share(ctx: ScoreContext) -> CheckResult:
    span = ctx.span
    holidays = ctx.bundle.calendar.regular_between(*span) if span else []
    if not holidays:
        return result("holiday_share", PASS, "no regular holiday in the period", None, ctx.reference("holiday_share"))
    rows = [(r.date, (item := ctx.item(r.item_id)) is not None and item.price_class == "subscription") for r in ctx.rows]
    value, n = ref.holiday_share(rows, holidays)
    what = f"non-subscription rows on {len(holidays)} regular holiday(s)"
    want = ctx.reference("holiday_share")
    if want is not None or value is None:
        se = share_se(want, n) if want is not None else None
        return relative_noisy(ctx, "holiday_share", value, want, se, what=what, fmt=lambda v: f"{v:.1%}", one_sided=True)
    limit = ctx.rule("holiday_share_max", DEFAULT_MAX)
    status = PASS if value <= limit else WARN
    return result("holiday_share", status, f"{what}: {value:.1%} (rule: at most {limit:.1%})", value, limit)
