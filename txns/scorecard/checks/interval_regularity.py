"""Interval regularity per item (FR-I4 (5)): neither metronomic nor clumped. Absolute rule, warn only.

For each item with at least `interval_min_rows` distinct row dates (default 8),
take the gaps in days between consecutive dates. Their coefficient of
variation (CV, population stdev / mean) is near 1 for random arrivals.

- metronomic: CV of the gaps between rows below `interval_cv_min` (default
  0.25), e.g. the same item every 7 days. Items on a fixed schedule are
  regular by design and are not judged here: subscription-class items and
  archetypes listed in `interval_scheduled_archetypes` (default
  fixed_day_subscription, periodic_top_up).
- clumped: CV of the gaps taken around the span as a circle above
  `interval_cv_max` (default 2.0), e.g. a run of consecutive days then nothing.
  The circle adds one gap from the last date past the span end round to the
  first date, so an item whose rows all sit in one corner of the span shows
  one long gap (the gaps then add up to the span length).
  `rules.archetypes.<archetype>.interval_cv_max` overrides the limit per
  archetype: project bursts are clustered by design (code default 3.0).

Thresholds come from rules.json `scorecard`. Each metric warns when any item is
flagged and lists them; value = {item id: CV} of flagged items.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Sequence

from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result

DEFAULT_MIN_ROWS = 8
DEFAULT_CV_MIN = 0.25
DEFAULT_CV_MAX = 2.0
DEFAULT_CV_MAX_BY_ARCHETYPE = {"project_burst": 3.0}
DEFAULT_SCHEDULED = ("fixed_day_subscription", "periodic_top_up")


def gaps(dates: Sequence[date]) -> list[int]:
    """Days between consecutive distinct dates."""
    days = sorted(set(dates))
    return [(b - a).days for a, b in zip(days, days[1:])]


def circular_gaps(dates: Sequence[date], start: date, end: date) -> list[int]:
    """`gaps` plus the wrap-around gap past the span end; they sum to the span length."""
    days = sorted(set(dates))
    span = (end - start).days + 1
    return gaps(days) + [span - (days[-1] - days[0]).days]


def cv(values: Sequence[int]) -> float:
    mean = statistics.fmean(values)
    return statistics.pstdev(values) / mean if mean else 0.0


@check("interval_regularity", "anomaly")
def interval_regularity(ctx: ScoreContext) -> list[CheckResult]:
    min_rows = int(ctx.rule("interval_min_rows", DEFAULT_MIN_ROWS))
    cv_min = float(ctx.rule("interval_cv_min", DEFAULT_CV_MIN))
    cv_max = float(ctx.rule("interval_cv_max", DEFAULT_CV_MAX))
    scheduled = set(ctx.rule("interval_scheduled_archetypes", DEFAULT_SCHEDULED))
    span = ctx.span
    looked = {"metronomic": 0, "clumped": 0}
    flagged: dict[str, dict[str, float]] = {"metronomic": {}, "clumped": {}}
    limits: dict[str, float] = {}
    for item_id, rows in ctx.by_item.items():
        dates = {r.date for r in rows}
        if span is None or len(dates) < min_rows:
            continue
        item = ctx.bundle.items[item_id]
        if item.price_class != "subscription" and item.archetype not in scheduled:
            looked["metronomic"] += 1
            spacing = round(cv(gaps(dates)), 3)
            if spacing < cv_min:
                flagged["metronomic"][item_id] = spacing
        limit = ctx.bundle.archetype_rules(item.archetype).get("interval_cv_max")
        limit = float(limit if limit is not None else DEFAULT_CV_MAX_BY_ARCHETYPE.get(item.archetype, cv_max))
        looked["clumped"] += 1
        spread = round(cv(circular_gaps(dates, *span)), 3)
        if spread > limit:
            flagged["clumped"][item_id] = spread
            limits[item_id] = limit
    out = []
    for kind, rule in (("metronomic", f"CV below {cv_min:g}"), ("clumped", f"CV above {cv_max:g}")):
        name = f"interval_regularity.{kind}"
        reference = cv_min if kind == "metronomic" else cv_max
        if not looked[kind]:
            out.append(result(name, PASS, f"{kind} intervals: no item to judge ({min_rows}+ row dates)", {}, reference))
            continue
        found = flagged[kind]
        if not found:
            detail = f"no {kind} item among {looked[kind]} with {min_rows}+ row dates ({rule})"
            out.append(result(name, PASS, detail, {}, reference))
            continue
        shown = [
            f"{i} CV {v:.2f}" + (f" (max {limits[i]:g})" if kind == "clumped" and limits[i] != cv_max else "")
            for i, v in found.items()
        ]
        detail = f"{len(found)} of {looked[kind]} items have {kind} intervals ({rule}): {listing(shown)}"
        out.append(result(name, WARN, detail, found, reference))
    return out
