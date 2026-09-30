"""Strict parser for Xero "Account Transactions" CSV exports (FR-B1).

Layout, one record per CSV row (quoted fields may span lines):

    Account Transactions                       title
    <organisation name>                        title (not kept: it is a name)
    For the period 1 January 2024 to 31 December 2024
    (blank)
    Date,Source,Description,Reference,Debit,Credit,Running Balance,Gross,Tax
    then, per account category:
      <Category heading>                       first cell only
      Opening Balance,...                      optional (balance-sheet accounts)
      <DD Mon YYYY>,<source>,...               transaction rows
      Total <Category heading>,...             section total
      Closing Balance,...                      optional, only after an opening balance
    Total,...                                  grand total
    (blank rows between records are allowed)

Every row has exactly 9 fields. Money is `1,234.56` or `(1,234.56)` for a
negative figure, parsed to int centavos. A section total must equal the sum of
its rows (debit, credit, gross, tax) and its running balance must equal the
opening balance plus debits minus credits; the closing balance must equal that
running balance, and the grand total must equal the sum of the section totals.
Anything else raises `LedgerError` (exit 2).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from txns.errors import MissingInput

HEADER = ("Date", "Source", "Description", "Reference", "Debit", "Credit", "Running Balance", "Gross", "Tax")
TITLE = "Account Transactions"
MONTHS = {m: i for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"), start=1)}
MONTH_ABBR = {name[:3]: i for name, i in MONTHS.items()}

_ROW_DATE = re.compile(r"(\d{2}) ([A-Z][a-z]{2}) (\d{4})")
_PERIOD = re.compile(r"For the period (\d{1,2}) ([A-Z][a-z]+) (\d{4}) to (\d{1,2}) ([A-Z][a-z]+) (\d{4})")
_MONEY = re.compile(r"(\()?(\d{1,3}(?:,\d{3})*|\d+)\.(\d{2})(\))?")
_NON_HEADINGS = ("Total", "Opening Balance", "Closing Balance")
_FIGURES = ("debit", "credit", "running", "gross", "tax")


class LedgerError(MissingInput):
    """Missing, unreadable, unrecognised or non-reconciling ledger (exit 2)."""


@dataclass(frozen=True)
class LedgerRow:
    date: date
    source: str
    description: str  # internal whitespace (incl. line breaks) collapsed to one space
    reference: str
    debit: int  # centavos
    credit: int
    gross: int
    tax: int
    category: str  # the account heading the row sits under
    ledger: str  # file name
    line: int  # physical line where the record ends (for messages)


@dataclass(frozen=True)
class Ledger:
    name: str  # file name
    start: date  # "For the period" line
    end: date
    rows: tuple[LedgerRow, ...]
    categories: tuple[str, ...]  # headings in file order


def _date(day: str, month: str, year: str, months: dict[str, int]) -> date | None:
    if month not in months:
        return None
    try:
        return date(int(year), months[month], int(day))
    except ValueError:
        return None


def parse_money(text: str) -> int | None:
    m = _MONEY.fullmatch(text.strip())
    if not m or bool(m.group(1)) != bool(m.group(4)):
        return None
    value = int(m.group(2).replace(",", "")) * 100 + int(m.group(3))
    return -value if m.group(1) else value


def _figures(fields: list[str], where: str) -> dict[str, int]:
    out = {}
    for key, text in zip(_FIGURES, fields[4:9]):
        v = parse_money(text)
        if v is None:
            raise LedgerError(f"{where}: {key} {text!r} is not an amount like 1,234.56")
        out[key] = v
    return out


def _peso(centavos: int) -> str:
    sign = "-" if centavos < 0 else ""
    c = abs(centavos)
    return f"{sign}{c // 100:,}.{c % 100:02d}"


def parse(text: str, name: str) -> Ledger:
    """Parse one export's text. Raises LedgerError on anything unexpected."""
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    records: list[tuple[int, list[str]]] = []
    try:
        for fields in reader:
            records.append((reader.line_num, fields))
    except csv.Error as exc:
        raise LedgerError(f"{name} line {reader.line_num}: not valid CSV ({exc})") from None

    def where(line: int) -> str:
        return f"{name} line {line}"

    for line, fields in records:
        if len(fields) != len(HEADER):
            raise LedgerError(f"{where(line)}: expected {len(HEADER)} columns, found {len(fields)} (unrecognised layout)")

    def first_only(fields: list[str]) -> bool:
        return bool(fields[0].strip()) and not any(f.strip() for f in fields[1:])

    if len(records) < 5:
        raise LedgerError(f"{name}: too short to be an Account Transactions export")
    (l0, f0), (l1, f1), (l2, f2), (l3, f3), (l4, f4) = records[:5]
    if not (first_only(f0) and f0[0].strip() == TITLE):
        raise LedgerError(f"{where(l0)}: expected the title {TITLE!r} (unrecognised layout)")
    if not first_only(f1):
        raise LedgerError(f"{where(l1)}: expected the organisation line (unrecognised layout)")
    m = _PERIOD.fullmatch(f2[0].strip()) if first_only(f2) else None
    start = _date(m.group(1), m.group(2), m.group(3), MONTHS) if m else None
    end = _date(m.group(4), m.group(5), m.group(6), MONTHS) if m else None
    if start is None or end is None or start > end:
        raise LedgerError(f"{where(l2)}: expected 'For the period D Month YYYY to D Month YYYY' (unrecognised layout)")
    if any(f.strip() for f in f3):
        raise LedgerError(f"{where(l3)}: expected a blank line before the header (unrecognised layout)")
    if tuple(f.strip() for f in f4) != HEADER:
        raise LedgerError(f"{where(l4)}: header must be {', '.join(HEADER)} (unrecognised layout)")

    rows: list[LedgerRow] = []
    categories: list[str] = []
    section_totals: list[dict[str, int]] = []
    heading: str | None = None  # open section
    state = ""  # "", "heading", "rows", "total"
    opening: dict[str, int] | None = None
    sums: dict[str, int] = {}
    grand: dict[str, int] | None = None
    last_line = l4

    for line, fields in records[5:]:
        last_line = line
        cells = [f.strip() for f in fields]
        if not any(cells):
            continue
        if grand is not None:
            raise LedgerError(f"{where(line)}: content after the grand total (unrecognised layout)")
        first = cells[0]
        dm = _ROW_DATE.fullmatch(first)
        if dm:
            if heading is None or state not in ("heading", "rows"):
                raise LedgerError(f"{where(line)}: transaction outside an account section (unrecognised layout)")
            d = _date(dm.group(1), dm.group(2), dm.group(3), MONTH_ABBR)
            if d is None:
                raise LedgerError(f"{where(line)}: bad date {first!r}")
            if not start <= d <= end:
                raise LedgerError(f"{where(line)}: date {d} is outside the export period {start} to {end}")
            fig = _figures(fields, where(line))
            if fig["debit"] < 0 or fig["credit"] < 0:
                raise LedgerError(f"{where(line)}: debit and credit must not be negative")
            for k in ("debit", "credit", "gross", "tax"):
                sums[k] += fig[k]
            rows.append(LedgerRow(
                date=d,
                source=cells[1],
                description=" ".join(fields[2].split()),
                reference=" ".join(fields[3].split()),
                debit=fig["debit"],
                credit=fig["credit"],
                gross=fig["gross"],
                tax=fig["tax"],
                category=heading,
                ledger=name,
                line=line,
            ))
            state = "rows"
        elif first == "Opening Balance":
            if state != "heading" or opening is not None:
                raise LedgerError(f"{where(line)}: Opening Balance must directly follow a heading (unrecognised layout)")
            opening = _figures(fields, where(line))
        elif first == "Closing Balance":
            if state != "total" or opening is None or not section_totals:
                raise LedgerError(f"{where(line)}: Closing Balance without an opening balance and total (unrecognised layout)")
            closing = _figures(fields, where(line))
            if closing["running"] != section_totals[-1]["running"]:
                raise LedgerError(
                    f"{where(line)}: closing balance {_peso(closing['running'])} does not reconcile with "
                    f"the section's running balance {_peso(section_totals[-1]['running'])}"
                )
            state, opening = "", None
        elif first == "Total":
            if state == "heading" or state == "rows":
                raise LedgerError(f"{where(line)}: section {heading!r} has no total (unrecognised layout)")
            if state == "total":
                raise LedgerError(f"{where(line)}: section {heading!r} has an opening balance but no closing balance")
            grand = _figures(fields, where(line))
            for k in _FIGURES:
                want = sum(t[k] for t in section_totals)
                if grand[k] != want:
                    raise LedgerError(
                        f"{where(line)}: grand total {k} {_peso(grand[k])} does not reconcile with "
                        f"the section totals ({_peso(want)})"
                    )
        elif first.startswith("Total "):
            if heading is None or state not in ("heading", "rows") or first != f"Total {heading}":
                raise LedgerError(f"{where(line)}: {first!r} does not close section {heading!r} (unrecognised layout)")
            total = _figures(fields, where(line))
            base = opening["running"] if opening else 0
            expect = dict(sums, running=base + sums["debit"] - sums["credit"])
            for k in _FIGURES:
                if total[k] != expect[k]:
                    raise LedgerError(
                        f"{where(line)}: section total {k} for {heading!r} is {_peso(total[k])} but its rows "
                        f"give {_peso(expect[k])} (does not reconcile)"
                    )
            section_totals.append(total)
            state = "total"
            if opening is None:
                state = ""
        elif first_only(fields) and not first.startswith(_NON_HEADINGS):
            if state in ("heading", "rows"):
                raise LedgerError(f"{where(line)}: section {heading!r} has no total (unrecognised layout)")
            if state == "total":
                raise LedgerError(f"{where(line)}: section {heading!r} has an opening balance but no closing balance")
            if first in categories:
                raise LedgerError(f"{where(line)}: account {first!r} appears twice (unrecognised layout)")
            heading, state, opening = first, "heading", None
            sums = {"debit": 0, "credit": 0, "gross": 0, "tax": 0}
            categories.append(first)
        else:
            raise LedgerError(f"{where(line)}: unrecognised row starting {first[:40]!r}")

    if grand is None:
        raise LedgerError(f"{where(last_line)}: no grand total row (unrecognised layout)")
    return Ledger(name=name, start=start, end=end, rows=tuple(rows), categories=tuple(categories))


def read(path: Path) -> tuple[Ledger, bytes]:
    """Read and parse one export; returns the ledger and the file's raw bytes (for hashing)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise LedgerError(f"ledger {path.name} unreadable: {exc.strerror or exc}") from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LedgerError(f"ledger {path.name} unreadable: not UTF-8 text") from None
    return parse(text, path.name), data
