"""Plan levers (FR-E11, FR-E12, FR-G2): how tier, multipliers and calibration scale a plan.

Nothing here touches a unit price. A plan is scaled by handing the planner and
drawer a *scaled view* of the bundle, in which

- an item's occurrence-rate parameters are multiplied (more or fewer
  occurrences, still dated by the archetype's own rules and gap rules), and
- an item's allowed-quantity weights are tilted toward larger or smaller
  quantities (the allowed set itself never changes).

Each archetype declares its levers when it registers:

    @register("petty_daily", levers=Levers(rate_params={"per_week": 1.0}, closing=True))

- `rate_params`: the item params that set how often the archetype occurs, with
  their defaults. The occurrence lever multiplies them. Empty = the archetype
  has no occurrence lever (fixed schedules). One-offs, deposit/balance pairs
  and party days declare theirs, but only retail items use them: big-ticket
  items grow by scope.
- `quantities`: whether the quantity lever applies. Which calibration stage
  turns it depends on the item's price class: subscription = seats (stage 2),
  retail = quantities (stage 3), big_ticket = scope (stage 4).
- `closing`: whether calibration may add or drop whole occurrences of this
  archetype's retail items to close the last gap (FR-G3).

Per-class rules (FR-E12): only retail items grow by number of occurrences;
subscriptions grow by seats and big-ticket items by scope, so their occurrence
count is left as planned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Mapping

from txns.bundle.model import Bundle, Item, QtyOption
from txns.config import ResolvedConfig

DEFAULT_TIER_FACTORS = {"independent": 0.5, "mid": 1.0, "high": 2.5}

# Quantity lever stage of each price class, in scaling order (FR-G2 steps 2-4).
QTY_STAGES = ("seats", "quantities", "scope")
QTY_STAGE_OF_CLASS = {"subscription": "seats", "retail": "quantities", "big_ticket": "scope"}

_TILT_WEIGHT = 1_000_000_000  # integer weight given to the most likely quantity after a tilt
_TILT_THETA = 1_000.0  # bisection range for the tilt exponent


@dataclass(frozen=True)
class Levers:
    """What calibration may turn for one archetype (see module docstring)."""

    rate_params: Mapping[str, float] = field(default_factory=dict)
    quantities: bool = True
    closing: bool = False


NO_LEVERS = Levers()


@dataclass(frozen=True)
class Setting:
    """One point in the calibration search.

    `occurrences` multiplies every occurrence-lever item's rate; `seats`,
    `quantities` and `scope` multiply the mean quantity of that stage's items.
    1.0 everywhere = the plan as shaped by tier and multipliers alone.
    """

    occurrences: float = 1.0
    seats: float = 1.0
    quantities: float = 1.0
    scope: float = 1.0

    def qty(self, stage: str) -> float:
        return getattr(self, stage)


def tier_factor(bundle: Bundle, tier: str) -> float:
    """Tier factor from rules.json `tier_factors`, else the spec defaults (FR-E11)."""
    factors = bundle.rules.get("tier_factors") or {}
    value = factors.get(tier, DEFAULT_TIER_FACTORS[tier])
    return float(value)


def plan_factor(item: Item, bundle: Bundle, config: ResolvedConfig) -> float:
    """Tier x class multiplier x storyline multiplier: how much this item's plan is scaled."""
    return (
        tier_factor(bundle, config.tier)
        * float(config.class_multipliers.get(item.price_class, 1.0))
        * float(config.storyline_multipliers.get(item.storyline, 1.0))
    )


def has_occurrence_lever(item: Item, levers: Levers) -> bool:
    return item.price_class == "retail" and bool(levers.rate_params)


def is_closable(item: Item, levers: Levers) -> bool:
    """Small ordinary occurrences that closing may add or drop (FR-G3)."""
    return levers.closing and has_occurrence_lever(item, levers)


def scale_rates(item: Item, levers: Levers, factor: float) -> Item:
    """The item with its occurrence-rate params multiplied by `factor`."""
    if factor == 1.0 or not has_occurrence_lever(item, levers):
        return item
    params = dict(item.params)
    for name, default in levers.rate_params.items():
        params[name] = float(params.get(name, default)) * factor
    return replace(item, params=MappingProxyType(params))


def tilt_quantities(options: tuple[QtyOption, ...], factor: float) -> tuple[QtyOption, ...]:
    """Reweight the allowed quantities so the mean quantity is `factor` x the bundle's.

    Exponential tilt: weight_i x (qty_i / min qty) ** theta, theta solved by
    bisection; the mean is clipped to the smallest and largest allowed
    quantity. Quantities never leave the allowed set, weight-0 quantities stay
    0, and every other quantity keeps a weight of at least 1, so the round-figure
    filter (`round_figures.qty_choices`) always has a non-round choice left.
    """
    live = [o for o in options if o.weight > 0]
    if factor == 1.0 or len({o.qty for o in live}) < 2:
        return options
    qs = [float(o.qty) for o in live]
    q_min, q_max = min(qs), max(qs)
    logw = [math.log(o.weight) for o in live]
    logr = [math.log(q / q_min) for q in qs]

    def weights(theta: float) -> list[float]:
        exps = [lw + theta * lr for lw, lr in zip(logw, logr)]
        top = max(exps)
        return [math.exp(e - top) for e in exps]

    def mean(theta: float) -> float:
        w = weights(theta)
        return sum(wi * q for wi, q in zip(w, qs)) / sum(w)

    goal = min(max(factor * mean(0.0), q_min), q_max)
    lo, hi = -_TILT_THETA, _TILT_THETA
    for _ in range(80):
        mid = (lo + hi) / 2
        if mean(mid) < goal:
            lo = mid
        else:
            hi = mid
    tilted = iter(weights((lo + hi) / 2))
    out = []
    for o in options:
        if o.weight > 0:
            out.append(replace(o, weight=max(1, round(_TILT_WEIGHT * next(tilted)))))
        else:
            out.append(o)
    return tuple(out)


class Scaler:
    """Builds scaled bundle views for calibration settings (cached per item and factor)."""

    def __init__(self, bundle: Bundle, config: ResolvedConfig, levers_of):
        self.bundle = bundle
        self.levers = {i.id: levers_of(i.archetype) for i in bundle.items.values()}
        self.factor = {i.id: plan_factor(i, bundle, config) for i in bundle.items.values()}
        self._tilts: dict[tuple[str, float], tuple[QtyOption, ...]] = {}

    def item_levers(self, item_id: str) -> Levers:
        return self.levers[item_id]

    def stages_present(self) -> list[str]:
        """Quantity stages that have at least one item, in scaling order."""
        present = {
            QTY_STAGE_OF_CLASS[i.price_class] for i in self.bundle.items.values() if self.levers[i.id].quantities
        }
        return [s for s in QTY_STAGES if s in present]

    def has_occurrence_levers(self) -> bool:
        return any(has_occurrence_lever(i, self.levers[i.id]) for i in self.bundle.items.values())

    def _tilt(self, item: Item, factor: float) -> tuple[QtyOption, ...]:
        key = (item.id, factor)
        if key not in self._tilts:
            self._tilts[key] = tilt_quantities(item.quantities, factor)
        return self._tilts[key]

    def bundle_for(self, setting: Setting) -> Bundle:
        items = {}
        for item in self.bundle.items.values():
            lv = self.levers[item.id]
            f = self.factor[item.id]
            scaled = scale_rates(item, lv, f * setting.occurrences)
            if lv.quantities:
                q = f * setting.qty(QTY_STAGE_OF_CLASS[item.price_class])
                tilted = self._tilt(item, q)
                if tilted is not item.quantities:
                    scaled = replace(scaled, quantities=tilted)
            items[item.id] = scaled
        return replace(self.bundle, items=MappingProxyType(items))
