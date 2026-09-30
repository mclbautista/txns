"""Month-end shape (FR-I4 (6)): share of rows in the last few days of their month
against reference.json `month_end_share`, with a sampling-noise floor. Warn only."""

from txns.scorecard import reference as ref
from txns.scorecard.registry import CheckResult, ScoreContext, check, relative_noisy, share_se


@check("month_end_shape", "anomaly")
def month_end_shape(ctx: ScoreContext) -> CheckResult:
    dates = [r.date for r in ctx.rows]
    want = ctx.reference("month_end_share")
    se = share_se(want, len(dates)) if want is not None else None
    return relative_noisy(
        ctx,
        "month_end_shape",
        ref.month_end_share(dates),
        want,
        se,
        what=f"rows in the last {ref.MONTH_END_DAYS} days of a month",
        fmt=lambda v: f"{v:.1%}",
    )
