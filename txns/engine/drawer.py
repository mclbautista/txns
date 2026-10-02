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

Minimum amount (`[calibration] min_transaction_amount`, off by default): a row
drawn below the floor is re-drawn (point unless fixed, then quantity) from the
item's own `rerolls` stream, up to `max_reroll_attempts` times; if none reaches
the floor, `DraftGenerationError` (exit 4). The `prices` and `quantities`
streams are never touched by a re-roll, so rows at or above the floor draw
exactly as without one. Pre-flight (`without_unreachable`, before any plan is
drawn): an item whose rate card and allowed quantities cannot reach the floor
at all is left out of the plan, and when none can, `DraftGenerationError`.
A balance row takes its deposit's quantity only when that keeps it at the floor.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from txns.bundle.model import Bundle, Item
from txns.config import ResolvedConfig
from txns.engine import round_figures
from txns.engine.context import EngineContext
from txns.engine.rows import Occurrence, Row
from txns.errors import DraftGenerationError
from txns.money import format_pesos


def max_amount(item: Item, points) -> int:
    """The largest row amount (centavos) a rate card's points and the item's allowed quantities give."""
    best = 0
    for point in points:
        for q in round_figures.qty_choices(item, point):
            if q.weight > 0:
                best = max(best, int(q.qty * point.price_for(q.qty)))
    return best


def unreachable(bundle: Bundle, config: ResolvedConfig) -> list[tuple[Item, int]]:
    """Items (with their largest possible row, centavos) that cannot reach the floor on some
    rate card valid in the period. Empty when no floor is set."""
    floor = config.calibration.min_amount_centavos
    if floor is None:
        return []
    start, end = config.period.start, config.period.end
    out = []
    for item in bundle.items.values():
        cards = [item.points_on(start)] + [s.points for s in item.steps if start < s.date <= end]
        best = min(max_amount(item, card) for card in cards)
        if best < floor:
            out.append((item, best))
    return out


def without_unreachable(bundle: Bundle, config: ResolvedConfig) -> Bundle:
    """Pre-flight for the floor: the bundle without the items that can never reach it.

    Such an item could only ever draw rows under the floor, so it is left out of
    the plan (`txns generate` lists it as a warning). When no item is left,
    `DraftGenerationError` (exit 4).
    """
    gone = unreachable(bundle, config)
    if not gone:
        return bundle
    ids = {item.id for item, _ in gone}
    items = {k: v for k, v in bundle.items.items() if k not in ids}
    if not items:
        floor = config.calibration.min_amount_centavos
        raise DraftGenerationError(
            f"no item in bundle {bundle.id} can reach the minimum transaction amount {format_pesos(floor)}: "
            "raise allowed quantities or price points in the bundle, or lower `calibration.min_transaction_amount`."
        )
    return replace(bundle, items=MappingProxyType(items))


def draw(ctx: EngineContext, occurrences: list[Occurrence]) -> list[Row]:
    floor = ctx.config.calibration.min_amount_centavos
    rows: list[Row] = []
    streams: dict[str, tuple] = {}
    rerolls: dict[str, object] = {}
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
        if floor is not None and q.qty * card[point].price_for(q.qty) < floor:
            if occ.item_id not in rerolls:
                rerolls[occ.item_id] = ctx.item_stream(occ.item_id, "rerolls")
            point, q = reroll(ctx, occ, rerolls[occ.item_id], floor)
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


def reroll(ctx: EngineContext, occ: Occurrence, stream, floor: int):
    """(point, qty option) re-drawn until the row reaches `floor`, at most `max_reroll_attempts` times."""
    item = ctx.bundle.items[occ.item_id]
    card = item.points_on(occ.date)
    points = round_figures.point_choices(item, occ.date)
    attempts = ctx.config.calibration.max_rerolls
    for _ in range(attempts):
        point = occ.price_point if occ.price_point is not None else points[stream.below(len(points))]
        q = draw_qty(stream, round_figures.qty_choices(item, card[point]))
        if q.qty * card[point].price_for(q.qty) >= floor:
            return point, q
    raise DraftGenerationError(
        f"failed to draw a row of at least {format_pesos(floor)} after {attempts} attempts for item "
        f"`{item.id}` (archetype `{item.archetype}`) on {occ.date}: too few of its price and quantity "
        "choices reach the floor. Raise its allowed quantities or price points, or raise "
        "`calibration.max_reroll_attempts`."
    )


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
        floor = ctx.config.calibration.min_amount_centavos
        if floor is not None and parent.qty * point.price_for(parent.qty) < floor:
            continue
        if any(o.qty == parent.qty and o.weight > 0 for o in round_figures.qty_choices(item, point)):
            rows[i] = replace(row, qty=parent.qty, unit_price=point.price_for(parent.qty))
    return rows
