"""Tidy cents (FR-F5, T19): every unit price is whole pesos or .50 / .75, never
random cents. Rows tagged "per_unit" (a derived per-unit price whose text
states the pack size, ticket 11) are exempt. Absolute rule, warn only.
"""

from txns.money import format_centavos, is_tidy_cents
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result

EXEMPT_TAGS = ("per_unit",)


@check("tidy_cents", "price_quantity")
def tidy_cents(ctx: ScoreContext) -> CheckResult:
    bad = [
        f"row {n} {row.date} {row.item_id or repr(row.text)} {format_centavos(row.unit_price)}"
        for n, row in enumerate(ctx.rows, start=1)
        if not is_tidy_cents(row.unit_price) and not any(t in EXEMPT_TAGS for t in row.tags)
    ]
    if bad:
        return result("tidy_cents", WARN, f"{len(bad)} unit price(s) with random cents: {listing(bad)}", len(bad), 0)
    return result("tidy_cents", PASS, "every unit price is whole pesos or .50 / .75", 0, 0)
