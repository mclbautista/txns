"""Storyline settings in `storylines.json` (FR-E10), checked at bundle load (failures exit 4).

    {name: {"description": str,
            "month_weights"?: {"1".."12": factor >= 0},   missing months 1.0
            "burst_days"?: [min, max],                    days a project burst lasts, 1 <= min <= max <= 92
            "bursts_per_quarter"?: number >= 0,           expected bursts in a quarter of weight-1.0 months
            "quiet_days"?: int >= 0,                      least quiet days between one burst's end and the next start
            "parties_per_quarter"?: number >= 0}}         expected party days in a quarter of weight-1.0 months

`month_weights` shape every item of the storyline (`txns.engine.calendar.day_shape`)
and the start of its bursts. The burst keys are read by project-burst items
(`txns.engine.bursts`); a storyline without them falls back to
`rules.archetypes.project_burst`, then the code defaults in that module. The same
keys are accepted (and checked) in `rules.archetypes.project_burst`.
`parties_per_quarter` makes the storyline a party storyline (`txns.bundle.events`):
its project-burst items occur on its party days (`txns.engine.parties`), several
rows of an item a day up to the item's cap, instead of in bursts.

A seasonal storyline (parties, a festival trip) gives its off-season months
weight 0; petty spend and the subscription stack leave `month_weights` out and
run all year.
"""

from __future__ import annotations

from typing import Any, Mapping

from txns.errors import BundleInvalid

MAX_BURST_DAYS = 92
BURST_KEYS = ("burst_days", "bursts_per_quarter", "quiet_days")


def _bad(where: str, msg: str) -> BundleInvalid:
    return BundleInvalid(f"bundle invalid: {where}: {msg}")


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_settings(where: str, entry: Mapping[str, Any]) -> None:
    """Check month weights and burst settings of one storyline (or of the project_burst rules)."""
    months = entry.get("month_weights")
    if months is not None:
        if not isinstance(months, dict):
            raise _bad(where, "`month_weights` must map month numbers \"1\"..\"12\" to factors")
        for key, value in months.items():
            if key not in {str(m) for m in range(1, 13)}:
                raise _bad(where, f"`month_weights` key `{key}` is not a month number 1..12")
            if not _is_number(value) or value < 0:
                raise _bad(where, f"`month_weights` for month {key} must be a number >= 0")
    days = entry.get("burst_days")
    if days is not None:
        if (
            not isinstance(days, list)
            or len(days) != 2
            or not all(isinstance(d, int) and not isinstance(d, bool) for d in days)
            or not 1 <= days[0] <= days[1] <= MAX_BURST_DAYS
        ):
            raise _bad(where, f"`burst_days` must be [min, max] whole days, 1 <= min <= max <= {MAX_BURST_DAYS}")
    per_quarter = entry.get("bursts_per_quarter")
    if per_quarter is not None and (not _is_number(per_quarter) or per_quarter < 0):
        raise _bad(where, "`bursts_per_quarter` must be a number >= 0")
    parties = entry.get("parties_per_quarter")
    if parties is not None and (not _is_number(parties) or parties < 0):
        raise _bad(where, "`parties_per_quarter` must be a number >= 0")
    quiet = entry.get("quiet_days")
    if quiet is not None and (not isinstance(quiet, int) or isinstance(quiet, bool) or quiet < 0):
        raise _bad(where, "`quiet_days` must be a whole number of days >= 0")


def check(storylines: Mapping[str, Any], rules: Mapping[str, Any]) -> None:
    for name in sorted(storylines):
        entry = storylines[name]
        if not isinstance(entry, dict):
            raise _bad(f"storyline `{name}`", "settings must be a table")
        check_settings(f"storyline `{name}`", entry)
    archetypes = rules.get("archetypes") if isinstance(rules, dict) else None
    burst_rules = archetypes.get("project_burst") if isinstance(archetypes, dict) else None
    if isinstance(burst_rules, dict):
        check_settings("rules.archetypes.project_burst", burst_rules)
