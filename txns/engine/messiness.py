"""Messiness and entry order (FR-H3, FR-H4).

Walking skeleton: rows sorted by (date, item id). Ticket 11 adds batch logging,
duplicates, realistic intra-day order and rare back-dated rows.
"""

from __future__ import annotations

from txns.engine.context import EngineContext
from txns.engine.rows import Row


def apply(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    return sorted(rows, key=lambda r: (r.date, r.item_id or ""))
