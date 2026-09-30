"""Tidy cents (FR-F5, T19): every unit price is whole pesos or .50 / .75, never
random cents. Derived per-unit rows (pack price / pieces on a row whose text
states the pack size, `bundle.packs.is_per_unit`) are exempt. Absolute rule,
warn only.
"""

from txns.bundle.packs import is_per_unit
from txns.money import format_centavos, is_tidy_cents
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result


@check("tidy_cents", "price_quantity")
def tidy_cents(ctx: ScoreContext) -> CheckResult:
    bad = [
        f"row {n} {row.date} {row.item_id or repr(row.text)} {format_centavos(row.unit_price)}"
        for n, row in enumerate(ctx.rows, start=1)
        if not is_tidy_cents(row.unit_price) and not is_per_unit(ctx.item(row.item_id), row)
    ]
    if bad:
        return result("tidy_cents", WARN, f"{len(bad)} unit price(s) with random cents: {listing(bad)}", len(bad), 0)
    return result("tidy_cents", PASS, "every unit price is whole pesos or .50 / .75", 0, 0)
