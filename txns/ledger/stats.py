"""Ledger rows -> `reference.json` figures (FR-B4).

Every measure the scorecard also takes comes from `txns.scorecard.reference`,
so generated rows and ledger rows are measured the same way. How a ledger row
maps onto those measures:

- amount = the Debit column (net of VAT, as recorded), int centavos.
- qty and unit price: a row whose item text states one pack size ("15pcs",
  "4 units") has qty = that size and unit price = amount / qty when that is a
  whole number of centavos; every other row is qty 1 at its amount.
- item text = the description after the first " - " (Xero writes
  "Vendor - item text"). A row with none (vendor only, blank) is textless;
  per-category textless share is the `terse_share` figure (FR-B4).
- a subscription row is one in a `subscription_categories` account.

Nothing from a row's text lands in the output except through these counts:
reference.json keys are metric names, categories (account headings) and
quarter labels only.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from txns.config import quarter_end
from txns.holidays import HolidayCalendar
from txns.ledger.spend_only import SpendRules
from txns.ledger.xero import Ledger, LedgerRow
from txns.scorecard import reference as ref

ANCHOR_MIN_ROWS = 2  # a unit price seen on at least this many rows of a category is an anchor
SHARE_PLACES = 4

_PACK = re.compile(
    r"(?<![\w.,])(\d{1,4})\s*(?:pcs|pc|pieces|piece|units|unit|packs|pack|sets|set|boxes|box|bottles|rolls|reams)(?!\w)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpendRow:
    date: date
    category: str
    amount: int  # centavos
    qty: int
    unit_price: int  # centavos
    item_text: str  # "" when textless
    subscription: bool
    vendor: str = ""  # description before the first " - " (a name: only txns.privacy reads it)


def vendor(description: str) -> str:
    """The vendor part of "Vendor - item text" (the whole description when it has no item)."""
    return description.split(" - ", 1)[0].strip()


def item_text(description: str) -> str:
    """The item part of "Vendor - item text"; "" when the description names no item."""
    if " - " not in description:
        return ""
    return description.split(" - ", 1)[1].strip()


def pack_size(text: str) -> int | None:
    """The one pack size the text states ("15pcs 1TB" -> 15), else None (none or ambiguous)."""
    sizes = {int(m.group(1)) for m in _PACK.finditer(text)}
    if len(sizes) != 1:
        return None
    (n,) = sizes
    return n if n > 1 else None


def spend_row(row: LedgerRow, rules: SpendRules) -> SpendRow:
    text = item_text(row.description)
    qty = pack_size(text) or 1
    if row.debit % qty:
        qty = 1
    return SpendRow(
        date=row.date,
        category=row.category,
        amount=row.debit,
        qty=qty,
        unit_price=row.debit // qty,
        item_text=text,
        subscription=row.category in rules.subscription_categories,
        vendor=vendor(row.description),
    )


def _share(value: float | None) -> float | None:
    return None if value is None else round(value, SHARE_PLACES)


def _counted(values: Iterable[int], key: str, minimum: int) -> list[dict[str, int]]:
    c = Counter(values)
    return [{key: v, "rows": n} for v, n in sorted(c.items()) if n >= minimum]


def full_quarters(start: date, end: date) -> list[tuple[str, date, date]]:
    """Calendar quarters wholly inside start..end, as (label, first day, last day)."""
    out = []
    year, q = start.year, (start.month - 1) // 3
    while True:
        first, last = date(year, 3 * q + 1, 1), quarter_end(year, q)
        if first > end:
            break
        if first >= start and last <= end:
            out.append((f"{year}Q{q + 1}", first, last))
        year, q = (year + 1, 0) if q == 3 else (year, q + 1)
    return out


def reference(
    ledgers: Sequence[Ledger],
    rows: Sequence[SpendRow],
    calendar: HolidayCalendar,
) -> tuple[dict, list[str]]:
    """Kept rows -> (reference.json object, warnings)."""
    warnings: list[str] = []
    out: dict = {}
    if not rows:
        return out, ["no spend rows left after the spend-only rule; reference.json is empty"]
    amounts = [r.amount for r in rows]
    dates = [r.date for r in rows]
    start = min(l.start for l in ledgers)
    end = max(l.end for l in ledgers)
    by_cat: dict[str, list[SpendRow]] = defaultdict(list)
    for r in rows:
        by_cat[r.category].append(r)
    cats = sorted(by_cat)

    out["anchor_prices"] = {
        c: a for c in cats if (a := _counted((r.unit_price for r in by_cat[c]), "unit_price", ANCHOR_MIN_ROWS))
    }
    out["category_amount_percentiles"] = {c: ref.row_amount_percentiles([r.amount for r in by_cat[c]]) for c in cats}
    out["category_totals"] = {c: {"rows": len(by_cat[c]), "spend": sum(r.amount for r in by_cat[c])} for c in cats}
    out["distinct_per_item"] = ref.distinct_per_item(
        (r.category, r.item_text.casefold(), r.unit_price, r.qty) for r in rows if r.item_text
    )
    out["duplicate_group_rate"] = ref.duplicate_group_rate([(r.date, r.category, r.amount) for r in rows])

    covered = [r for r in rows if calendar.covers(r.date.year)]
    missing = sorted({r.date.year for r in rows} - set(calendar.years))
    if missing:
        warnings.append(
            f"holiday calendar has no data for {', '.join(map(str, missing))}; "
            "those years are left out of holiday_share"
        )
    if covered:
        share, _ = ref.holiday_share(((r.date, r.subscription) for r in covered), calendar.regular_between(start, end))
        if share is not None:
            out["holiday_share"] = share

    out["month_end_share"] = ref.month_end_share(dates)
    spread, _ = ref.monthly_spread(((r.date, r.amount) for r in rows), start, end)
    if spread is not None:
        out["monthly_spread"] = spread
    out["pack_sizes"] = {c: p for c in cats if (p := _counted((r.qty for r in by_cat[c] if r.qty > 1), "qty", 1))}
    quarters = {}
    for ledger in ledgers:
        for label, first, last in full_quarters(ledger.start, ledger.end):
            q = [r for r in rows if first <= r.date <= last]
            quarters[label] = {"rows": len(q), "spend": sum(r.amount for r in q)}
    out["quarter_totals"] = dict(sorted(quarters.items()))
    out["round_amount_share"] = _share(ref.round_amount_share(amounts))
    out["row_amount_percentiles"] = ref.row_amount_percentiles(amounts)
    out["terse_share"] = ref.terse_share((r.category, not r.item_text) for r in rows)
    out["weekday_shares"] = ref.weekday_shares(dates)
    out["whole_peso_share"] = _share(ref.whole_peso_share([r.unit_price for r in rows]))
    return out, warnings
