"""Pack sizes and derived per-unit prices (FR-F5, T19).

A stock item whose rate-card unit is a pack of N pieces declares it in its
catalog entry as `pack_pcs: N` (an int >= 2; only on `goods: "stock"`, never on
decimal items). A text variant *states the pack size* when it names N pieces:
"15pcs", "15 pcs", "15 pieces", "pack of 15", "box of 15", "15-pack", ...
(`states_pack`).

On about 5% of such an item's rows whose chosen text states the pack size, the
row is written per piece instead of per pack, the way a bookkeeper divides a
receipt's total by the pieces it lists:

    qty        = packs x N
    unit_price = pack price / N, to the centavo (`derived_prices`: rounded down
                 or up; the engine alternates so the run total never drops)

These are the only rows whose unit price is not a rate-card figure and may
carry random cents. `is_per_unit` recognises them from the row itself (item,
text, qty, price, date), so the price-stability, tidy-cent and quantity checks
accept exactly these rows, for generated rows and external CSVs alike.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Mapping

from txns.errors import BundleInvalid

PACK_GOODS = "stock"
_UNITS = r"(?:pcs|pc|pieces|piece|ct|count)"
_HOLDERS = r"(?:pack|box|set|bundle|case|bag)"


@lru_cache(maxsize=256)
def _pattern(pcs: int) -> re.Pattern:
    n = rf"(?<![0-9.,]){pcs}(?![0-9.,][0-9])"
    return re.compile(
        rf"{n} ?{_UNITS}\b|\b{_HOLDERS}s? of {n}\b|{n}[- ]?(?:pack|pk)\b",
        re.IGNORECASE,
    )


def check_entry(where: str, entry: Mapping[str, Any], *, goods: str | None, decimal: bool) -> None:
    """Bundle rule for a catalog entry's `pack_pcs` (a bad one exits 4)."""
    pcs = entry.get("pack_pcs")
    if pcs is None:
        return
    if isinstance(pcs, bool) or not isinstance(pcs, int) or pcs < 2:
        raise BundleInvalid(f"bundle invalid: {where}: `pack_pcs` must be an integer of at least 2")
    if goods != PACK_GOODS:
        raise BundleInvalid(f"bundle invalid: {where}: `pack_pcs` is only for items with goods \"{PACK_GOODS}\"")
    if decimal:
        raise BundleInvalid(f"bundle invalid: {where}: `pack_pcs` cannot be used on a decimal item")


def pack_pcs(item) -> int | None:
    """Pieces per pack of a stock item's rate-card unit, or None."""
    pcs = item.raw.get("pack_pcs") if item.raw else None
    if item.goods != PACK_GOODS or isinstance(pcs, bool) or not isinstance(pcs, int) or pcs < 2:
        return None
    return pcs


def states_pack(text: str, pcs: int) -> bool:
    """True when the text names the pack size (N pieces)."""
    return bool(_pattern(pcs).search(text))


def derived_prices(pack_price: int, pcs: int) -> tuple[int, int]:
    """Per-piece price of a pack, rounded down and up to the centavo (equal when it divides)."""
    return pack_price // pcs, -(-pack_price // pcs)


def is_per_unit(item, row) -> bool:
    """The row is a derived per-unit row: its text states the pack size, its qty is
    whole packs in pieces, and its unit price is a pack price valid on its date divided
    by the pieces. An ordinary rate-card row is never one."""
    if item is None:
        return False
    pcs = pack_pcs(item)
    if pcs is None or not isinstance(row.qty, int) or isinstance(row.qty, bool) or row.qty % pcs:
        return False
    if not states_pack(row.text, pcs):
        return False
    packs = row.qty // pcs
    points = item.points_on(row.date)
    if any(row.unit_price == p.price_for(row.qty) for p in points):
        return False  # a rate-card price at this qty: an ordinary row
    return any(row.unit_price in derived_prices(p.price_for(packs), pcs) for p in points)
