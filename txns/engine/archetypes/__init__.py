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
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable

PlanFn = Callable[..., list]

_REGISTRY: dict[str, PlanFn] = {}


def register(name: str) -> Callable[[PlanFn], PlanFn]:
    def deco(fn: PlanFn) -> PlanFn:
        if name in _REGISTRY:
            raise RuntimeError(f"archetype `{name}` registered twice")
        _REGISTRY[name] = fn
        return fn

    return deco


def _load_all() -> None:
    for mod in sorted(m.name for m in pkgutil.iter_modules(__path__)):
        importlib.import_module(f"{__name__}.{mod}")


def get(name: str) -> PlanFn | None:
    return _REGISTRY.get(name)


def names() -> list[str]:
    return sorted(_REGISTRY)


_load_all()
