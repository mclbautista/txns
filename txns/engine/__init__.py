"""Engine: the pure core, (seed, bundle, resolved config) -> rows.

Stages, one module each (edit the one your ticket owns):

    drawer.without_unreachable  pre-flight: items that cannot reach the floor left out
    calibrator.calibrate  total into band: runs planner.plan and drawer.draw
                          for each scaled plan it tries (levers.py)
    planner.plan        occurrences by archetype (archetypes/<name>.py)
    drawer.draw         rate-card point + allowed quantity
    calendar.apply_ceiling  soft daily ceiling (FR-E5)
    text.apply          item text variant
    messiness.apply     batch logging, duplicates, entry order

No file, env, clock or network access in here.
"""

from __future__ import annotations

from txns.bundle.model import Bundle
from txns.config import ResolvedConfig
from txns.engine import calendar, calibrator, drawer, messiness, text
from txns.engine.context import EngineContext
from txns.engine.rows import Occurrence, Row
from txns.prng import Streams

__all__ = ["generate", "EngineContext", "Occurrence", "Row"]


def generate(seed: int, bundle: Bundle, config: ResolvedConfig) -> list[Row]:
    bundle = drawer.without_unreachable(bundle, config)  # items that can never reach the floor
    ctx = EngineContext(bundle=bundle, config=config, streams=Streams(seed))
    rows = calibrator.calibrate(ctx)  # planner.plan + drawer.draw, scaled into band
    rows = calendar.apply_ceiling(ctx, rows)  # moves rows between days; amounts and count kept
    rows = text.apply(ctx, rows)
    rows = messiness.apply(ctx, rows)
    return rows
