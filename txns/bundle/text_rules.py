"""Promotion gates on item text (FR-D2 items 4-6, FR-C3, FR-H1).

`text_problems(bundle)` lists every violation; empty means the text passes.
`author` and `approve` run it before promoting (any problem exits 4);
`generate` does not, it relies on the scorecard's format check instead (T27).

Rules, per item:
- at least MIN_DESCRIPTIVE descriptive variants (plain plus vendor-prefixed),
  vendor-prefixed ones a minority of them;
- at least MIN_TERSE terse variants, except big-ticket items, which have none;
- no blank or vendor-only variant: no variant equals a vendor name or seller
  id, and a vendor-prefixed variant reads "Vendor - item" with an item part;
- plain descriptive and terse variants are unattributed: they carry no
  "Vendor - " prefix, and terse variants no " - " at all;
- a vendor-prefixed variant names one of the item's own sellers, and each
  seller has one vendor name across the bundle;
- every variant obeys the CSV text rules (FR-H2).
Across items: each string belongs to exactly one item, except a terse string
shared by items with identical price-point sets (FR-D2 item 5).
"""

from __future__ import annotations

from collections import defaultdict

from txns.bundle.model import Bundle, Item
from txns.writer import text_violations

MIN_DESCRIPTIVE = 3
MIN_TERSE = 2
SEPARATOR = " - "


def vendor_name(text: str) -> str | None:
    """"Vendor" from "Vendor - item" (None when there is no prefix)."""
    head, sep, _ = text.partition(SEPARATOR)
    return head.strip() if sep else None


def _item_problems(item: Item, vendor_names: set[str], sellers: set[str]) -> list[str]:
    where = f"item `{item.id}`"
    problems = []
    n_desc = len(item.descriptive) + len(item.vendor)
    if n_desc < MIN_DESCRIPTIVE:
        problems.append(f"{where}: {n_desc} descriptive variant(s), needs at least {MIN_DESCRIPTIVE}")
    if item.vendor and len(item.vendor) >= len(item.descriptive):
        problems.append(f"{where}: vendor-prefixed variants must be a minority of descriptive text")
    if item.price_class == "big_ticket":
        if item.terse:
            problems.append(f"{where}: big-ticket items have no terse variants")
    elif len(item.terse) < MIN_TERSE:
        problems.append(f"{where}: {len(item.terse)} terse variant(s), needs at least {MIN_TERSE}")

    own_sellers = {p.seller for p in item.price_points if p.seller}
    for v in item.vendor:
        name = vendor_name(v.text)
        rest = v.text.partition(SEPARATOR)[2].strip()
        if not name or not rest:
            problems.append(f"{where}: vendor variant {v.text!r} must read `Vendor - item`")
        elif rest.lower() in vendor_names or rest in sellers:
            problems.append(f"{where}: vendor variant {v.text!r} names no item, only a vendor")
        if v.seller not in own_sellers:
            problems.append(f"{where}: vendor variant {v.text!r} names seller `{v.seller}`, which has no price point here")

    for kind, texts in (("descriptive", item.descriptive), ("terse", item.terse)):
        for text in texts:
            name = vendor_name(text)
            if text.strip().lower() in vendor_names or text.strip() in sellers:
                problems.append(f"{where}: {kind} variant {text!r} is vendor-only")
            elif name is not None and (kind == "terse" or name.lower() in vendor_names):
                problems.append(f"{where}: {kind} variant {text!r} carries a vendor; move it to `vendor`")

    seen = set()
    for text in item.variants:
        if text in seen:
            problems.append(f"{where}: variant {text!r} listed twice")
        seen.add(text)
        problems.extend(f"{where}: variant {text!r}: {p}" for p in text_violations(text))
    return problems


def text_problems(bundle: Bundle) -> list[str]:
    problems: list[str] = []
    sellers = {p.seller for item in bundle.items.values() for p in item.price_points if p.seller}

    names_by_seller: dict[str, set[str]] = defaultdict(set)
    for item in bundle.items.values():
        for v in item.vendor:
            name = vendor_name(v.text)
            if name:
                names_by_seller[v.seller].add(name)
    for seller, names in sorted(names_by_seller.items()):
        if len(names) > 1:
            problems.append(f"seller `{seller}` appears under several vendor names: {', '.join(sorted(names))}")
    vendor_names = {n.lower() for names in names_by_seller.values() for n in names}

    for item in bundle.items.values():
        problems.extend(_item_problems(item, vendor_names, sellers))

    owners: dict[str, list[tuple[str, bool]]] = defaultdict(list)  # text -> [(item id, is terse)]
    for item in bundle.items.values():
        for text in set(item.variants):
            owners[text].append((item.id, text in item.terse))
    for text, users in sorted(owners.items()):
        if len(users) < 2:
            continue
        ids = [i for i, _ in users]
        point_sets = {frozenset(bundle.items[i].price_points) for i in ids}
        if not all(terse for _, terse in users) or len(point_sets) > 1:
            problems.append(
                f"variant {text!r} is used by {', '.join(ids)}; only a terse string of items "
                "with identical price-point sets may be shared"
            )
    return problems
