"""Calibrator (FR-G): scale the occurrence plan until the total is in band.

Walking skeleton: pass-through. Ticket 05 implements the scaling order, the
closing step, tier factors and multipliers, and exits 5/6
(`txns.errors.GapsImpossible`, `txns.errors.TargetUnsatisfiable`).
"""

from __future__ import annotations

from txns.engine.context import EngineContext
from txns.engine.rows import Row


def calibrate(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    return rows
