"""Occurrence planner: storylines x items -> dated occurrences by archetype (FR-E).

Items are planned in item-id order, each with its own "dates" stream, so the
plan for one item never depends on any other item.
"""

from __future__ import annotations

from txns.engine import archetypes
from txns.engine.context import EngineContext
from txns.engine.rows import Occurrence
from txns.errors import BundleInvalid


def check_archetypes(ctx: EngineContext) -> None:
    for item in ctx.bundle.items.values():
        if archetypes.get(item.archetype) is None:
            raise BundleInvalid(
                f"bundle invalid: item `{item.id}` uses unknown archetype `{item.archetype}` "
                f"(known: {', '.join(archetypes.names())})"
            )


def plan(ctx: EngineContext) -> list[Occurrence]:
    check_archetypes(ctx)
    occurrences: list[Occurrence] = []
    for item in ctx.bundle.items.values():  # sorted by id
        fn = archetypes.get(item.archetype)
        occurrences.extend(fn(item, ctx, ctx.item_stream(item.id, "dates")))
    return occurrences
