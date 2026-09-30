"""Price stability (FR-F1, FR-F2, FR-I5, T16): no item has more distinct unit
prices than its rate-card points (plus price steps in the span, ticket 09). Hard."""

from txns.bundle.model import Item
from txns.scorecard.registry import FAIL, PASS, CheckResult, ScoreContext, check, listing, result


def allowed_prices(ctx: ScoreContext, item: Item) -> int:
    """Distinct unit prices an item may show. Ticket 09 adds price steps in the span and volume tiers."""
    return len(item.price_points)


@check("price_stability", "price_quantity", hard=True)
def price_stability(ctx: ScoreContext) -> CheckResult:
    over: dict[str, int] = {}
    notes = []
    for item_id, rows in ctx.by_item.items():
        distinct = len({r.unit_price for r in rows})
        allowed = allowed_prices(ctx, ctx.bundle.items[item_id])
        if distinct > allowed:
            over[item_id] = distinct
            notes.append(f"{item_id}: {distinct} prices, rate card allows {allowed}")
    if over:
        return result("price_stability", FAIL, f"{len(over)} item(s) with unstable prices: {listing(notes)}", over)
    return result("price_stability", PASS, f"every item within its rate-card points ({len(ctx.by_item)} items)", {})
