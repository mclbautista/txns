"""Map item text back to catalog item ids, so item checks key on the catalog item
id rather than on text (FR-D2 item 5, FR-I4 (1) and (7)).

Exact match against every variant in the bundle: plain descriptive,
vendor-prefixed and terse. The bundle gate (`txns.bundle.text_rules`) makes
each string belong to one item, except a terse string shared by items with
identical price-point sets; that string maps to the first of those item ids in
sorted order, which leaves the price checks unaffected. A batch-logged row may
carry an original-date tail (FR-H3); `TextMatcher` strips it with the bundle's
vocabulary formats and maps the rest.
"""

from __future__ import annotations

from dataclasses import dataclass

from txns.bundle import vocabulary
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


class TextMatcher:
    """Item text -> TextMatch: an exact variant, or a variant followed by a date tail."""

    def __init__(self, bundle: Bundle):
        self.index = text_index(bundle)
        self.tail = vocabulary.tail_pattern(vocabulary.date_tails(bundle))

    def match(self, text: str) -> TextMatch | None:
        found = self.index.get(text)
        if found is None:
            core = vocabulary.strip_date_tail(self.tail, text)
            if core is not None:
                found = self.index.get(core)
        return found

    def item_id(self, text: str) -> str | None:
        found = self.match(text)
        return found.item_id if found else None
