"""No plug rows (FR-F6, FR-I5): keep round-thousand amounts off unapproved items.

The drawer draws the price point and quantity only from the choices below, so
an item not approved for round figures never gets a row amount that is a whole
multiple of ₱1,000 (the same as redrawing the quantity until it is not round).
Only when a bundle leaves no other choice does a round amount get through; the
scorecard's plug-row check then fails hard, as it should for such a bundle.
Any later stage that changes a quantity (calibration) must use `qty_choices`.
"""

from __future__ import annotations

from txns.bundle.model import Item, QtyOption
from txns.money import is_round_thousand


def qty_choices(item: Item, unit_price: int) -> tuple[QtyOption, ...]:
    """The item's allowed quantities that give a non-round amount at `unit_price`."""
    if item.round_figures_approved:
        return item.quantities
    ok = tuple(q for q in item.quantities if not is_round_thousand(q.qty * unit_price))
    return ok if sum(q.weight for q in ok) > 0 else item.quantities


def point_choices(item: Item) -> tuple[int, ...]:
    """Indexes of the price points that have at least one non-round quantity."""
    every = tuple(range(len(item.price_points)))
    if item.round_figures_approved:
        return every
    ok = tuple(
        i
        for i in every
        if any(q.weight > 0 and not is_round_thousand(q.qty * item.price_points[i].unit_price) for q in item.quantities)
    )
    return ok or every
