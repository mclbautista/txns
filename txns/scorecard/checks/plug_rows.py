"""Plug rows (FR-F6, FR-I5, T18): no round-thousand row amount on an item not
approved for round figures (`Item.round_figures_approved`). Rows whose text
maps to no catalog item count as unapproved. Hard."""

from txns.money import format_pesos, is_round_thousand
from txns.scorecard.registry import FAIL, PASS, CheckResult, ScoreContext, check, listing, result


@check("plug_rows", "price_quantity", hard=True)
def plug_rows(ctx: ScoreContext) -> CheckResult:
    bad = []
    for n, row in enumerate(ctx.rows, start=1):
        item = ctx.item(row.item_id)
        if is_round_thousand(row.amount) and not (item is not None and item.round_figures_approved):
            bad.append(f"row {n} {row.date} {row.item_id or repr(row.text)} {format_pesos(int(row.amount))}")
    if bad:
        return result(
            "plug_rows",
            FAIL,
            f"{len(bad)} round-thousand amount(s) on items not approved for round figures: {listing(bad)}",
            value=len(bad),
            reference=0,
        )
    return result("plug_rows", PASS, "no round-thousand amounts outside approved big-ticket items", value=0, reference=0)
