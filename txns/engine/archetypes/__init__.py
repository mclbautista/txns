"""Archetype registry (FR-E3). One archetype per module in this package.

To add an archetype, create `txns/engine/archetypes/<name>.py`:

    from txns.engine.archetypes import register

    @register("fixed_day_subscription")
    def plan(item, ctx, stream):
        '''Return the item's Occurrences for ctx.config.period.'''

`plan(item: Item, ctx: EngineContext, stream: Stream) -> list[Occurrence]`
gets the item's own "dates" stream. Per-archetype rules come from
`ctx.bundle.archetype_rules(name)`, per-item parameters from `item.params`.
Modules are imported automatically; nothing else needs editing.

An archetype exposes its calibration levers (FR-G2) with
`@register(name, levers=Levers(...))`: which params set how often it occurs,
whether quantities may scale, and whether closing may add or drop its
occurrences. See `txns/engine/levers.py`. Without `levers=` an archetype has
no occurrence lever and is never used for closing.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

from txns.engine.levers import NO_LEVERS, Levers

PlanFn = Callable[..., list]

_REGISTRY: dict[str, PlanFn] = {}
_LEVERS: dict[str, Levers] = {}


def register(name: str, *, levers: Levers = NO_LEVERS) -> Callable[[PlanFn], PlanFn]:
    def deco(fn: PlanFn) -> PlanFn:
        if name in _REGISTRY:
            raise RuntimeError(f"archetype `{name}` registered twice")
        _REGISTRY[name] = fn
        _LEVERS[name] = levers
        return fn

    return deco


def _load_all() -> None:
    for mod in sorted(m.name for m in pkgutil.iter_modules(__path__)):
        importlib.import_module(f"{__name__}.{mod}")


def get(name: str) -> PlanFn | None:
    return _REGISTRY.get(name)


def levers(name: str) -> Levers:
    """The calibration levers an archetype exposes (NO_LEVERS if unknown)."""
    return _LEVERS.get(name, NO_LEVERS)


def names() -> list[str]:
    return sorted(_REGISTRY)


_load_all()
