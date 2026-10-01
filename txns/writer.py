"""Writer: formats and checks rows, writes the CSV and run.json (FR-H2, FR-H5, FR-H6).

The CSV is built in memory and written as bytes: UTF-8 without BOM, LF line
endings, RFC 4180 quoting (only fields with `,`, `"` or newlines are quoted).
Files are written via a temp name and renamed, so a failed run leaves no CSV.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path
from typing import Any

from txns.canonical import atomic_write, pretty_json
from txns.engine.rows import Row
from txns.money import format_centavos

HEADER = ("date_of_transaction", "qty", "unit_price", "item/service")
MAX_TEXT = 100
PESO = "₱"


def text_violations(text: str) -> list[str]:
    """FR-H2 rules for item text."""
    problems = []
    if not text or not text.strip():
        problems.append("blank item text")
        return problems
    if len(text) > MAX_TEXT:
        problems.append(f"item text longer than {MAX_TEXT} characters")
    if text != text.strip(" "):
        problems.append("item text has leading or trailing spaces")
    bad = sorted({c for c in text if not (" " <= c <= "~" or c == PESO)})
    if bad:
        problems.append("item text has characters outside ASCII plus ₱: " + ", ".join(repr(c) for c in bad))
    return problems


def row_violations(row: Row, *, decimal: bool = False) -> list[str]:
    """Every FR-H2 problem with one row; empty when the row is writable.

    `decimal`: the row's item is marked decimal, so qty may be a positive Decimal.
    """
    problems = text_violations(row.text)
    qty = row.qty
    if isinstance(qty, Decimal) and qty.is_finite() and qty > 0:
        if not decimal:
            problems.append(f"qty {format_qty(qty)} is a decimal on an item not marked decimal")
    elif isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        problems.append(f"qty must be a positive {'number' if decimal else 'integer'}, got {qty!r}")
    if isinstance(row.unit_price, bool) or not isinstance(row.unit_price, int) or row.unit_price <= 0:
        problems.append(f"unit_price must be positive integer centavos, got {row.unit_price!r}")
    return problems


def format_qty(qty: int | Decimal) -> str:
    """Integers as is; decimal quantities (decimal items only) in plain notation, as the bundle wrote them."""
    return format(qty, "f") if isinstance(qty, Decimal) else str(qty)


def csv_bytes(rows: list[Row]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerow(HEADER)
    for r in rows:
        w.writerow((r.date.isoformat(), format_qty(r.qty), format_centavos(r.unit_price), r.text))
    return buf.getvalue().encode("utf-8")


def write_outputs(out_dir: Path, stem: str, rows: list[Row], run: dict[str, Any]) -> tuple[Path, Path]:
    """Write `<stem>.csv` and `<stem>.run.json` into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{stem}.csv"
    run_path = out_dir / f"{stem}.run.json"
    atomic_write(csv_path, csv_bytes(rows))
    atomic_write(run_path, pretty_json(run).encode("utf-8"))
    return csv_path, run_path
