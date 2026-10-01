"""No plug rows (FR-F6, FR-I5): keep round-thousand amounts off unapproved items.

The drawer draws the price point and quantity only from the choices below, so
an item not approved for round figures never gets a row amount that is a whole
multiple of ₱1,000 (the same as redrawing the quantity until it is not round).
Only when a bundle leaves no other choice does a round amount get through; the
scorecard's plug-row check then fails hard, as it should for such a bundle.
Any later stage that changes a quantity (calibration) must use `qty_choices`,
and price the new quantity with `point.price_for(qty)` (volume tiers, ticket 09).
"""

from __future__ import annotations

from datetime import date

from txns.bundle.model import Item, PricePoint, QtyOption
from txns.money import is_round_thousand


def _pricer(point: PricePoint | int):
    return point.price_for if isinstance(point, PricePoint) else (lambda qty: point)


def qty_choices(item: Item, point: PricePoint | int) -> tuple[QtyOption, ...]:
    """The item's allowed quantities that give a non-round amount at `point`.

    `point` is the row's PricePoint (its volume tier price applies per qty) or a flat unit price.
    """
    if item.round_figures_approved:
        return item.quantities
    price_for = _pricer(point)
    ok = tuple(q for q in item.quantities if not is_round_thousand(q.qty * price_for(q.qty)))
    return ok if sum(q.weight for q in ok) > 0 else item.quantities


def point_choices(item: Item, day: date | None = None) -> tuple[int, ...]:
    """Indexes of the price points (valid on `day`, default the base card) with a non-round quantity."""
    points = item.points_on(day)
    every = tuple(range(len(points)))
    if item.round_figures_approved:
        return every
    ok = tuple(
        i
        for i in every
        if any(q.weight > 0 and not is_round_thousand(q.qty * points[i].price_for(q.qty)) for q in item.quantities)
    )
    return ok or every
