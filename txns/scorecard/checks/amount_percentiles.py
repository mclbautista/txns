"""Row-amount percentiles vs ledger (FR-I3): p10..p90 of qty x unit_price
against reference.json `row_amount_percentiles`. Ledger-relative, warn only."""

from txns.money import format_pesos
from txns.scorecard import reference as ref
from txns.scorecard.registry import CheckResult, ScoreContext, check, relative


@check("row_amount_percentiles", "price_quantity")
def row_amount_percentiles(ctx: ScoreContext) -> list[CheckResult]:
    got = ref.row_amount_percentiles(ctx.amounts) if ctx.amounts else {}
    ledger = ctx.reference("row_amount_percentiles") or {}
    return [
        relative(
            ctx,
            f"row_amount_percentiles.p{p}",
            got.get(f"p{p}"),
            ledger.get(f"p{p}"),
            what=f"p{p} row amount",
            fmt=format_pesos,
        )
        for p in ref.PERCENTILES
    ]
