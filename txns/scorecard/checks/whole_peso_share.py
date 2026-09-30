"""Whole-peso share of unit prices (FR-F5, FR-I3, T19): the share of rows whose
unit price has no cents, near about 60%. Graded against reference.json
`whole_peso_share` when the ledger gives one, else against rules.json
`scorecard.whole_peso_share` (default 0.60), with the run's tolerance. Warn only.
"""

from txns.scorecard import reference as ref
from txns.scorecard.registry import CheckResult, ScoreContext, check, grade, relative, result

DEFAULT_SHARE = 0.60


def _pct(v: float) -> str:
    return f"{v:.1%}"


@check("whole_peso_share", "price_quantity")
def whole_peso_share(ctx: ScoreContext) -> CheckResult:
    what = "whole-peso unit price share"
    share = ref.whole_peso_share([r.unit_price for r in ctx.rows])
    ledger = ctx.reference("whole_peso_share")
    if ledger is not None or share is None:
        return relative(ctx, "whole_peso_share", share, ledger, what=what, fmt=_pct)
    target = float(ctx.rule("whole_peso_share", DEFAULT_SHARE))
    detail = f"{what} {_pct(share)} vs {_pct(target)} (FR-F5, no ledger reference; tolerance ±{ctx.tolerance_pct}%)"
    return result("whole_peso_share", grade(share, target, ctx.tolerance_pct), detail, share, target)
