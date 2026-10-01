"""The LLM payload: aggregates and item-text patterns only, scrubbed (FR-C2, ADR 0007).

    payload = build(derived, index)   # scrubbed; still run leaks.find_in_object before sending

Shape (money in int centavos, like reference.json):

    {"format": 1, "currency": "PHP",
     "quarters": {"2024Q1": {"rows", "spend"}, ...},
     "categories": [{"category", "subscription", "rows", "spend", "amount_percentiles",
                     "textless_rows",
                     "item_texts": [{"text", "rows"}],   # distinct item texts, most used first
                     "vendors": [{"name", "rows"}]}]}    # allowlisted brand or fabricated name

No row is ever copied: no dates, no per-row amounts, no references. An item-text
pattern is a kept row's item text (after the first " - ") with ledger names
replaced by their labels, e-mail addresses and runs of 5+ digits masked, and
whitespace collapsed; texts equal up to case are merged (the most used spelling
shows). Anything added to the payload must go through `build` so it is scrubbed,
and the whole object is scrubbed once more at the end.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from txns.ledger import Derived
from txns.privacy.names import NameIndex

FORMAT = 1
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_NUMBER = re.compile(r"(?<!\d)\d{5,}(?!\d)")


def item_pattern(text: str, index: NameIndex) -> str:
    text = index.scrub(" ".join(text.split()))
    text = _EMAIL.sub("[email]", text)
    return _LONG_NUMBER.sub(lambda m: "#" * len(m.group(0)), text)


def _ranked(counter: Counter, key: str) -> list[dict]:
    return [{key: v, "rows": n} for v, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def build(derived: Derived, index: NameIndex) -> dict:
    ref = derived.reference
    by_cat = defaultdict(list)
    for r in derived.rows:
        by_cat[r.category].append(r)

    categories = []
    for cat in sorted(by_cat):
        rows = by_cat[cat]
        spellings: dict[str, Counter] = defaultdict(Counter)  # casefolded pattern -> spelling counts
        vendors: Counter = Counter()
        for r in rows:
            if r.item_text:
                p = item_pattern(r.item_text, index)
                spellings[p.casefold()][p] += 1
            label = index.label(r.vendor) if r.vendor else None
            if label is not None:
                vendors[label] += 1
        texts = Counter()
        for c in spellings.values():
            best = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            texts[best] = sum(c.values())
        categories.append({
            "category": cat,
            "subscription": any(r.subscription for r in rows),
            "rows": len(rows),
            "spend": sum(r.amount for r in rows),
            "amount_percentiles": ref.get("category_amount_percentiles", {}).get(cat, {}),
            "textless_rows": sum(1 for r in rows if not r.item_text),
            "item_texts": _ranked(texts, "text"),
            "vendors": _ranked(vendors, "name"),
        })

    payload = {
        "format": FORMAT,
        "currency": "PHP",
        "quarters": ref.get("quarter_totals", {}),
        "categories": categories,
    }
    return index.scrub_obj(payload)
