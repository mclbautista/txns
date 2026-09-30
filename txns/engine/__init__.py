"""Engine: the pure core, (seed, bundle, resolved config) -> rows.

Stages, one module each (edit the one your ticket owns):

    planner.plan        occurrences by archetype (archetypes/<name>.py)
    drawer.draw         rate-card point + allowed quantity
    calibrator.calibrate  total into band (ticket 05)
    text.apply          item text variant
    messiness.apply     batch logging, duplicates, entry order

No file, env, clock or network access in here.
"""

from __future__ import annotations

from txns.bundle.model import Bundle
from txns.config import ResolvedConfig
from txns.engine import calibrator, drawer, messiness, planner, text
from txns.engine.context import EngineContext
from txns.engine.rows import Occurrence, Row
from txns.prng import Streams

__all__ = ["generate", "EngineContext", "Occurrence", "Row"]


def generate(seed: int, bundle: Bundle, config: ResolvedConfig) -> list[Row]:
    ctx = EngineContext(bundle=bundle, config=config, streams=Streams(seed))
    occurrences = planner.plan(ctx)
    rows = drawer.draw(ctx, occurrences)
    rows = calibrator.calibrate(ctx, rows)
    rows = text.apply(ctx, rows)
    rows = messiness.apply(ctx, rows)
    return rows
