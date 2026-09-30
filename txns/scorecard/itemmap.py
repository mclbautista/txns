"""Map item text back to catalog item ids, for scoring a CSV that has no ids.

Exact match against every descriptive and terse variant in the bundle. A text
used by several items (a shared terse string, FR-D2 item 5) maps to the first
item id in sorted order; those items share their price-point sets, so the
price checks are unaffected. Ticket 10 extends this (vendor prefixes etc.).
"""

from __future__ import annotations

from txns.bundle.model import Bundle


def item_index(bundle: Bundle) -> dict[str, str]:
    index: dict[str, str] = {}
    for item in bundle.items.values():  # sorted by id
        for text in item.descriptive + item.terse:
            index.setdefault(text, item.id)
    return index
