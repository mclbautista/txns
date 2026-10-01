"""Round-amount share (FR-I4 (2)): share of rows whose amount is a whole ₱100,
against reference.json `round_amount_share`. Ledger-relative, warn only."""

from txns.scorecard import reference as ref
from txns.scorecard.registry import CheckResult, ScoreContext, check, relative


@check("round_amount_share", "anomaly")
def round_amount_share(ctx: ScoreContext) -> CheckResult:
    return relative(
        ctx,
        "round_amount_share",
        ref.round_amount_share(ctx.amounts),
        ctx.reference("round_amount_share"),
        what="round-₱100 amount share",
        fmt=lambda v: f"{v:.1%}",
    )
