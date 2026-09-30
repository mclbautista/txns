"""Read an output-format CSV back into scorecard rows (the scorecard-as-a-function seam).

Each row's catalog item is found from its exact text by `itemmap.item_index`;
rows whose text no catalog item uses keep `item_id=None`, so item-keyed
checks skip them while row-level checks (format, plug rows) still apply.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from txns.bundle.model import Bundle
from txns.engine.rows import Row
from txns.scorecard.itemmap import item_index
from txns.writer import HEADER


class CsvUnreadable(ValueError):
    pass


def _centavos(text: str, where: str) -> int:
    try:
        value = Decimal(text) * 100
    except InvalidOperation:
        raise CsvUnreadable(f"{where}: unit_price {text!r} is not a number") from None
    if value != value.to_integral_value():
        raise CsvUnreadable(f"{where}: unit_price {text!r} has fractional centavos")
    return int(value)


def _qty(text: str, where: str) -> int | Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise CsvUnreadable(f"{where}: qty {text!r} is not a number") from None
    # Whole quantities become int; a decimal stays Decimal (the format check flags it).
    return int(value) if value == value.to_integral_value() else value


def read_csv(data: str | bytes, bundle: Bundle) -> list[Row]:
    text = data.decode("utf-8") if isinstance(data, bytes) else data
    records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if not records or tuple(records[0]) != HEADER:
        raise CsvUnreadable(f"header must be {','.join(HEADER)}")
    index = item_index(bundle)
    rows = []
    for n, rec in enumerate(records[1:], start=2):
        where = f"line {n}"
        if len(rec) != len(HEADER):
            raise CsvUnreadable(f"{where}: expected {len(HEADER)} fields, got {len(rec)}")
        d, qty, price, item_text = rec
        try:
            day = date.fromisoformat(d)
        except ValueError:
            raise CsvUnreadable(f"{where}: date {d!r} is not YYYY-MM-DD") from None
        item_id = index.get(item_text)
        item = bundle.items.get(item_id) if item_id else None
        rows.append(
            Row(
                date=day,
                qty=_qty(qty, where),
                unit_price=_centavos(price, where),
                text=item_text,
                item_id=item_id,
                storyline=item.storyline if item else None,
            )
        )
    return rows
