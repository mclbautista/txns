"""Event and big-ticket rules checked at bundle load (FR-E3 to FR-E5, FR-F6); failures exit 4.

Party storylines. A storyline that sets `parties_per_quarter` (storylines.json)
throws parties: one-day bursts on its party days (`txns.engine.parties`). Its
`project_burst` items are *party items*: they occur only on party days, with
several rows of the item a day up to their cap (`max_per_day`, default
PARTY_MAX_PER_DAY). This is FR-E5's party-day exception; there is no separate
party archetype (FR-E3 lists seven).

1. Rows per day (FR-E5): only batch-logged items and party items may have more
   than one row of the item on a day (`max_per_day`, item params or
   `rules.archetypes.<name>`), capped at MAX_ROWS_PER_DAY. Any other item with
   `max_per_day` above 1 is rejected, so a bundle edit cannot loosen the gap rule.
2. Round figures (FR-F6): catalog `round_figures` is a bool. Big-ticket items
   are approved by default (`false` withdraws it). Other items may be approved
   only when they are event items whose rate card is a negotiated or stepped
   round figure: `deposit_balance` items (venue deposits) or party items (prize
   tiers), with every unit price on the card (points, tiers, steps) a whole
   multiple of ₱1,000. So round amounts stay on round-figure rate cards.
3. Deposit-then-balance items (FR-E4): at most 2 price points (point 0 is the
   deposit, the last point the balance; one point = both at that figure,
   checked in `txns.bundle.prices`), and the offset range `offset_days`
   [min, max] (item params, else `rules.archetypes.deposit_balance`, else
   DEFAULT_OFFSET_DAYS) has 1 <= min <= max <= MAX_OFFSET_DAYS.
"""

from __future__ import annotations

from typing import Any

from txns.errors import BundleInvalid
from txns.money import is_round_thousand

BATCH_LOGGED = "batch_logged"
DEPOSIT_BALANCE = "deposit_balance"
PARTY_ARCHETYPE = "project_burst"  # a party storyline's project-burst items are party items
PARTY_KEY = "parties_per_quarter"
PARTY_MAX_PER_DAY = 4  # default cap of a party item's rows on a party day
MAX_ROWS_PER_DAY = 20
DEFAULT_OFFSET_DAYS = (14, 42)  # "weeks later" (FR-E4)
MAX_OFFSET_DAYS = 366


def _bad(where: str, msg: str) -> BundleInvalid:
    return BundleInvalid(f"bundle invalid: {where}: {msg}")


def is_party_storyline(storylines, name: str) -> bool:
    entry = storylines.get(name) or {}
    return entry.get(PARTY_KEY) is not None


def is_party_item(bundle, item) -> bool:
    """A project-burst item of a party storyline: its rows fall on party days, several a day up to its cap."""
    return item.archetype == PARTY_ARCHETYPE and is_party_storyline(bundle.storylines, item.storyline)


def offset_days(bundle, item) -> tuple[int, int]:
    """A deposit_balance item's balance offset from its deposit, in days: item params, rules, default."""
    rules = bundle.archetype_rules(DEPOSIT_BALANCE)
    value: Any = item.params.get("offset_days", rules.get("offset_days", DEFAULT_OFFSET_DAYS))
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        or not 1 <= value[0] <= value[1] <= MAX_OFFSET_DAYS
    ):
        raise _bad(
            f"item `{item.id}`",
            f"`offset_days` must be [min, max] whole days, 1 <= min <= max <= {MAX_OFFSET_DAYS}",
        )
    return int(value[0]), int(value[1])


def _prices(item) -> set[int]:
    versions = [item.price_points] + [s.points for s in item.steps]
    return {price for points in versions for p in points for price in p.figures}


def _check_rows_per_day(bundle, item) -> None:
    where = f"item `{item.id}`"
    try:
        max_per_day, min_gap = bundle.gap_rules(item)
    except (TypeError, ValueError):
        raise _bad(where, "`max_per_day` and `min_gap_days` must be whole numbers") from None
    if max_per_day < 1 or min_gap < 1:
        raise _bad(where, "`max_per_day` and `min_gap_days` must be at least 1")
    if max_per_day > 1 and item.archetype != BATCH_LOGGED and not is_party_item(bundle, item):
        raise _bad(
            where,
            f"`max_per_day` {max_per_day}: only batch_logged items and party items (project_burst items "
            f"of a storyline with `{PARTY_KEY}`) may have more than one row a day (FR-E5)",
        )
    if max_per_day > MAX_ROWS_PER_DAY:
        raise _bad(where, f"`max_per_day` must be at most {MAX_ROWS_PER_DAY}")


def _check_round_figures(bundle, item) -> None:
    where = f"item `{item.id}`"
    flag = item.raw.get("round_figures")
    if flag is None:
        return
    if not isinstance(flag, bool):
        raise _bad(where, "`round_figures` must be true or false")
    if not flag or item.price_class == "big_ticket":
        return
    if item.archetype != DEPOSIT_BALANCE and not is_party_item(bundle, item):
        raise _bad(
            where,
            "`round_figures` is only for big-ticket items and event items (deposit_balance items, party items) (FR-F6)",
        )
    if any(not is_round_thousand(p) for p in _prices(item)):
        raise _bad(where, "`round_figures` needs a round-figure rate card: every unit price a whole ₱1,000")


def check(bundle) -> None:
    for item in bundle.items.values():
        _check_rows_per_day(bundle, item)
        _check_round_figures(bundle, item)
        if item.archetype == DEPOSIT_BALANCE:
            offset_days(bundle, item)
