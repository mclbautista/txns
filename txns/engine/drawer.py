"""Quantity and price drawer (FR-F): a rate-card point and an allowed quantity per occurrence.

Per row: the rate card valid on the row's date (price steps, FR-F2), one of its
points (seller), a quantity from the allowed set (FR-F3), then that point's
fixed price for the quantity (volume tiers, FR-F4). No price is ever computed.

A subscription's qty is its seat count (FR-F3): drawn once per item and kept
on every charge of the run, so seats do not jump from month to month.

Deposit/balance rows come with a fixed point (`Occurrence.price_point`: the
deposit figure, the balance figure), and a balance row takes its deposit's
quantity when the balance point allows it (`match_pairs`): the event is
settled at the scope it was booked at. The usual draws are still made, so
streams never shift.
"""

from __future__ import annotations

from dataclasses import replace

from txns.engine import round_figures
from txns.engine.context import EngineContext
from txns.engine.rows import Occurrence, Row


def draw(ctx: EngineContext, occurrences: list[Occurrence]) -> list[Row]:
    rows: list[Row] = []
    streams: dict[str, tuple] = {}
    seats: dict[str, object] = {}
    linked: list[tuple[int, Occurrence]] = []
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
        point = points[price_stream.below(len(points))]  # always drawn, so streams never shift
        if occ.price_point is not None:  # a fixed point (deposit/balance)
            point = occ.price_point
        options = round_figures.qty_choices(item, card[point])
        if item.price_class == "subscription" and seats.get(item.id) in options:
            q = seats[item.id]
        else:
            q = draw_qty(qty_stream, options)
            if item.price_class == "subscription":
                seats[item.id] = q
        if occ.parent_date is not None:
            linked.append((len(rows), occ))
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
                original_date=occ.original_date,
            )
        )
    return match_pairs(ctx, rows, linked) if linked else rows


def draw_qty(stream, options):
    """One allowed quantity by inverse CDF over the options in ascending qty.

    Monotone in the weights: when calibration tilts them toward larger
    quantities (seats, quantities, big-ticket scope, FR-G2), a row's quantity
    never goes down for the same draw, so the plan's total grows step by step
    and the calibrator can bisect to the band.
    """
    ordered = sorted(options, key=lambda o: o.qty)
    return ordered[stream.quantile_index([o.weight for o in ordered])]


def match_pairs(ctx: EngineContext, rows: list[Row], linked: list[tuple[int, Occurrence]]) -> list[Row]:
    """Give each balance row its deposit row's quantity where the balance's allowed set has it."""
    by_day = {(r.item_id, r.date): r for r in rows}
    for i, occ in linked:
        row = rows[i]
        item = ctx.bundle.items[row.item_id]
        parent = by_day.get((row.item_id, occ.parent_date))
        if parent is None or parent.qty == row.qty:
            continue
        point = item.points_on(row.date)[row.price_point]
        if any(o.qty == parent.qty and o.weight > 0 for o in round_figures.qty_choices(item, point)):
            rows[i] = replace(row, qty=parent.qty, unit_price=point.price_for(parent.qty))
    return rows
