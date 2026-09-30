"""Item text (FR-H1): pick a variant for each row after its price point is chosen.

Walking skeleton: uniform over the item's descriptive + terse variants.
Ticket 10 adds terse share, seller-matched vendor prefixes, big-ticket rules.
"""

from __future__ import annotations

from dataclasses import replace

from txns.engine.context import EngineContext
from txns.engine.rows import Row


def apply(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    out: list[Row] = []
    streams = {}
    for row in rows:
        if row.item_id not in streams:
            streams[row.item_id] = ctx.item_stream(row.item_id, "text")
        item = ctx.bundle.items[row.item_id]
        variants = item.descriptive + item.terse
        out.append(replace(row, text=streams[row.item_id].choice(variants)))
    return out
