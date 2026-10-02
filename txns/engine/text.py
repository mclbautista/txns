"""Item text (FR-H1): pick a variant for each row after its price point is chosen.

Per item, a share of its rows is terse: the terse share of the item's category
from reference.json `terse_share` (else rules.json `text.default_terse_share`,
else DEFAULT_TERSE_SHARE). Items with no terse variants and big-ticket items
are never terse. The count is rounded stochastically per item and the terse
rows are picked at random among the item's rows, so the share holds even for
small items without being exact across seeds.

A descriptive row draws uniformly from the item's plain descriptive variants
plus the vendor-prefixed variants whose seller sells the row's price point, so
a vendor name never contradicts the price. Terse variants are unattributed.

Variants matching the bundle's rules.json `denied_item_patterns`
(`text_rules.is_denied_text`) are never drawn: a row whose pool is all denied
falls back to the item's other allowed variants (terse to descriptive and back).
An item with no allowed variant at all is a bundle error (exit 4). For a bundle
without denied text this changes no draw.

Every draw uses the item's own `text` stream, so other items and storylines
never change an item's text (T5).
"""

from __future__ import annotations

import math
from dataclasses import replace

from txns.bundle import text_rules
from txns.bundle.model import Item
from txns.engine.context import EngineContext
from txns.engine.rows import Row
from txns.errors import BundleInvalid

DEFAULT_TERSE_SHARE = 0.3


def terse_share(ctx: EngineContext, item: Item) -> float:
    """The share of this item's rows that should be terse (0 when it may not be terse)."""
    if not item.terse or item.price_class == "big_ticket":
        return 0.0
    if not item.descriptive and not item.vendor:
        return 1.0
    shares = ctx.bundle.reference.get("terse_share") or {}
    share = shares.get(item.category)
    if share is None:
        share = ctx.bundle.rules.get("text", {}).get("default_terse_share", DEFAULT_TERSE_SHARE)
    return min(1.0, max(0.0, float(share)))


def seller_of(item: Item, row: Row) -> str | None:
    """The seller of the row's price point (None when the row has no point)."""
    if row.price_point is None or not 0 <= row.price_point < len(item.price_points):
        return None
    return item.price_points[row.price_point].seller


def descriptive_choices(item: Item, seller: str | None) -> tuple[str, ...]:
    """Plain descriptive variants plus those vendor-prefixed by this seller."""
    return item.descriptive + tuple(v.text for v in item.vendor if seller is not None and v.seller == seller)


def without_denied(ctx: EngineContext, item: Item) -> Item:
    """The item with its denied variants removed (the item itself when none is denied)."""
    denied = text_rules.denied_patterns(ctx.bundle.rules)
    if not any(text_rules.is_denied_text(t, denied) for t in item.variants):
        return item
    kept = replace(
        item,
        descriptive=text_rules.allowed_texts(item.descriptive, denied),
        terse=text_rules.allowed_texts(item.terse, denied),
        vendor=tuple(v for v in item.vendor if not text_rules.is_denied_text(v.text, denied)),
    )
    if not kept.descriptive and not kept.terse:
        raise BundleInvalid(
            f"bundle invalid: every plain descriptive and terse variant of item `{item.id}` matches "
            f"rules.json `{text_rules.DENIED_KEY}`; add a variant that names the thing bought"
        )
    return kept


def apply(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    positions: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        positions.setdefault(row.item_id, []).append(i)
    out = list(rows)
    for item_id in sorted(positions):
        item = without_denied(ctx, ctx.bundle.items[item_id])
        stream = ctx.item_stream(item_id, "text")
        idx = positions[item_id]
        expected = terse_share(ctx, item) * len(idx)
        whole = math.floor(expected)
        k = whole + (1 if stream.chance(expected - whole) else 0)
        order = list(range(len(idx)))
        stream.shuffle(order)
        terse_rows = set(order[:k])
        for n, i in enumerate(idx):
            row = rows[i]
            if n in terse_rows:
                variants = item.terse
            else:
                # The loader guarantees plain descriptive or terse variants exist, and
                # `without_denied` that some survive the denylist.
                variants = descriptive_choices(item, seller_of(item, row)) or item.terse
            out[i] = replace(row, text=stream.choice(variants))
    return out
