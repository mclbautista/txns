"""Quantity and price drawer (FR-F): a rate-card point and an allowed quantity per occurrence.

Price steps, volume tiers and decimal quantities arrive with ticket 09.
"""

from __future__ import annotations

from txns.engine import round_figures
from txns.engine.context import EngineContext
from txns.engine.rows import Occurrence, Row


def draw(ctx: EngineContext, occurrences: list[Occurrence]) -> list[Row]:
    rows: list[Row] = []
    streams: dict[str, tuple] = {}
    for occ in occurrences:
        if occ.item_id not in streams:
            streams[occ.item_id] = (
                ctx.item_stream(occ.item_id, "prices"),
                ctx.item_stream(occ.item_id, "quantities"),
            )
        price_stream, qty_stream = streams[occ.item_id]
        item = ctx.bundle.items[occ.item_id]
        points = round_figures.point_choices(item)
        point = points[price_stream.below(len(points))]
        options = round_figures.qty_choices(item, item.price_points[point].unit_price)
        q = options[qty_stream.weighted_index([o.weight for o in options])]
        rows.append(
            Row(
                date=occ.date,
                qty=q.qty,
                unit_price=item.price_points[point].unit_price,
                text="",
                item_id=item.id,
                storyline=item.storyline,
                price_point=point,
                tags=occ.tags,
            )
        )
    return rows
