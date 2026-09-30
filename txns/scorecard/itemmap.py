"""Map item text back to catalog item ids, so item checks key on the catalog item
id rather than on text (FR-D2 item 5, FR-I4 (1) and (7)).

Exact match against every variant in the bundle: plain descriptive,
vendor-prefixed and terse. The bundle gate (`txns.bundle.text_rules`) makes
each string belong to one item, except a terse string shared by items with
identical price-point sets; that string maps to the first of those item ids in
sorted order, which leaves the price checks unaffected. Ticket 11 extends this
(original-date tails on batch-logged rows).
"""

from __future__ import annotations

from dataclasses import dataclass

from txns.bundle.model import Bundle

DESCRIPTIVE, VENDOR, TERSE = "descriptive", "vendor", "terse"


@dataclass(frozen=True)
class TextMatch:
    item_id: str
    kind: str  # descriptive | vendor | terse
    seller: str | None = None  # the seller a vendor-prefixed variant names


def text_index(bundle: Bundle) -> dict[str, TextMatch]:
    index: dict[str, TextMatch] = {}
    for item in bundle.items.values():  # sorted by id
        for text in item.descriptive:
            index.setdefault(text, TextMatch(item.id, DESCRIPTIVE))
        for v in item.vendor:
            index.setdefault(v.text, TextMatch(item.id, VENDOR, v.seller))
        for text in item.terse:
            index.setdefault(text, TextMatch(item.id, TERSE))
    return index


def item_index(bundle: Bundle) -> dict[str, str]:
    return {text: m.item_id for text, m in text_index(bundle).items()}
