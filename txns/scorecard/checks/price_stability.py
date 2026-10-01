"""Price stability (FR-F1, FR-F2, FR-F4, FR-I5, T16). Hard. Per catalog item:

- no more distinct unit prices than the rate card allows in the span: its
  points and their volume tiers, plus the prices of every step dated in the
  span (`Item.prices_between`);
- no old price on or after the date of the step that replaced it
  (`Item.retired_prices`).

Derived per-unit rows (`bundle.packs.is_per_unit`: a pack price valid on the
row's date over the pieces its text states) are the one allowed exception
(FR-F5) and are left out of both rules.

The span is the run's period, or for an external CSV the rows' first to last
date. The value maps each failing item to its number of distinct prices.
"""

from datetime import date

from txns.bundle.model import Item
from txns.bundle.packs import is_per_unit
from txns.money import format_pesos
from txns.scorecard.registry import FAIL, PASS, CheckResult, ScoreContext, check, listing, result


def span(ctx: ScoreContext) -> tuple[date, date] | None:
    if ctx.config is not None:
        return ctx.config.period.start, ctx.config.period.end
    days = [r.date for r in ctx.rows]
    return (min(days), max(days)) if days else None


def allowed_prices(ctx: ScoreContext, item: Item) -> int:
    """Distinct unit prices an item may show in the span: points and tiers, plus steps in the span."""
    bounds = span(ctx)
    if bounds is None:
        return len({price for p in item.price_points for price in p.figures})
    return len(item.prices_between(*bounds))


@check("price_stability", "price_quantity", hard=True)
def price_stability(ctx: ScoreContext) -> CheckResult:
    failing: dict[str, int] = {}
    notes = []
    for item_id, rows in ctx.by_item.items():
        item = ctx.bundle.items[item_id]
        rows = [r for r in rows if not is_per_unit(item, r)]
        distinct = len({r.unit_price for r in rows})
        allowed = allowed_prices(ctx, item)
        if distinct > allowed:
            failing[item_id] = distinct
            notes.append(f"{item_id}: {distinct} prices, rate card allows {allowed}")
        if item.steps:
            old = [r for r in rows if r.unit_price in item.retired_prices(r.date)]
            if old:
                failing[item_id] = distinct
                first = min(old, key=lambda r: r.date)
                notes.append(
                    f"{item_id}: old price {format_pesos(first.unit_price)} on {first.date} after its step"
                    + (f" (+{len(old) - 1} more row(s))" if len(old) > 1 else "")
                )
    if failing:
        return result("price_stability", FAIL, f"{len(failing)} item(s) with unstable prices: {listing(notes)}", failing)
    return result(
        "price_stability",
        PASS,
        f"every item within its rate-card points, tiers and steps ({len(ctx.by_item)} items)",
        {},
    )
