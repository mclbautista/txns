"""Quantity and price drawer (FR-F): a rate-card point and an allowed quantity per occurrence.

Per row: the rate card valid on the row's date (price steps, FR-F2), one of its
points (seller), a quantity from the allowed set (FR-F3), then that point's
fixed price for the quantity (volume tiers, FR-F4). No price is ever computed.

A subscription's qty is its seat count (FR-F3): drawn once per item and kept
on every charge of the run, so seats do not jump from month to month.
"""

from __future__ import annotations

from txns.engine import round_figures
from txns.engine.context import EngineContext
from txns.engine.rows import Occurrence, Row


def draw(ctx: EngineContext, occurrences: list[Occurrence]) -> list[Row]:
    rows: list[Row] = []
    streams: dict[str, tuple] = {}
    seats: dict[str, object] = {}
    for occ in occurrences:
        if occ.item_id not in streams:
            streams[occ.item_id] = (
                ctx.item_stream(occ.item_id, "prices"),
                ctx.item_stream(occ.item_id, "quantities"),
            )
        price_stream, qty_stream = streams[occ.item_id]
        item = ctx.bundle.items[occ.item_id]
        card = item.points_on(occ.date)
        points = round_figures.point_choices(item, occ.date)
        point = points[price_stream.below(len(points))]
        options = round_figures.qty_choices(item, card[point])
        if item.price_class == "subscription" and seats.get(item.id) in options:
            q = seats[item.id]
        else:
            q = options[qty_stream.weighted_index([o.weight for o in options])]
            if item.price_class == "subscription":
                seats[item.id] = q
        rows.append(
            Row(
                date=occ.date,
                qty=q.qty,
                unit_price=card[point].price_for(q.qty),
                text="",
                item_id=item.id,
                storyline=item.storyline,
                price_point=point,
                tags=occ.tags,
            )
        )
    return rows
