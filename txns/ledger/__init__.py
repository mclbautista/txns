"""Ledger reader: Xero exports -> spend rows -> `reference.json` (FR-B1 to FR-B4).

    derived = derive(ledgers_dir, root)
    derived.reference        # the reference.json object (write with canonical.pretty_json)
    derived.ledger_hashes    # {file name: sha256 hex} for the bundle manifest
    derived.categories       # author category labels (FR-B2): headings of kept rows
    derived.kept, derived.excluded, derived.warnings

`root` is the working folder holding the committed inputs
(`inputs/spend-only.json`, `inputs/ph-holidays.json`). Every failure is a
`LedgerError` (exit 2). The same ledger bytes always give the same result:
files are read in name order and nothing depends on paths or clocks (T40).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from txns import holidays
from txns.canonical import sha256_hex
from txns.ledger import spend_only, stats, xero
from txns.ledger.xero import Ledger, LedgerError

__all__ = ["Derived", "LedgerError", "derive", "read_ledgers"]


@dataclass
class Derived:
    reference: dict
    ledger_hashes: dict[str, str]
    categories: list[str]
    kept: int
    excluded: Counter = field(default_factory=Counter)  # reason -> rows
    warnings: list[str] = field(default_factory=list)


def read_ledgers(ledgers_dir: Path) -> tuple[list[Ledger], dict[str, str]]:
    """Parse every `*.csv` in the folder (name order); returns ledgers and their SHA-256s."""
    if not ledgers_dir.is_dir():
        raise LedgerError(f"ledgers folder not found: {ledgers_dir}")
    paths = sorted(p for p in ledgers_dir.glob("*.csv") if p.is_file())
    if not paths:
        raise LedgerError(f"no ledger exports (*.csv) in {ledgers_dir}")
    ledgers, hashes = [], {}
    for path in paths:
        ledger, data = xero.read(path)
        ledgers.append(ledger)
        hashes[path.name] = sha256_hex(data)
    spans = sorted(ledgers, key=lambda l: (l.start, l.name))
    for a, b in zip(spans, spans[1:]):
        if b.start <= a.end:
            raise LedgerError(f"ledger periods overlap: {a.name} ({a.start} to {a.end}) and {b.name} ({b.start} to {b.end})")
    return ledgers, hashes


def derive(ledgers_dir: Path, root: Path) -> Derived:
    rules = spend_only.load_committed(root)
    try:
        calendar = holidays.parse(holidays.load_committed(root), str(holidays.COMMITTED_PATH))
    except FileNotFoundError:
        raise LedgerError(f"holiday calendar not found: {holidays.COMMITTED_PATH.as_posix()}") from None
    except (OSError, ValueError) as exc:
        raise LedgerError(f"holiday calendar unreadable: {exc}") from None
    ledgers, hashes = read_ledgers(ledgers_dir)

    kept: list[stats.SpendRow] = []
    excluded: Counter = Counter()
    for ledger in ledgers:
        for row in ledger.rows:
            reason = rules.exclusion(row)
            if reason is None:
                kept.append(stats.spend_row(row, rules))
            else:
                excluded[reason] += 1
    reference, warnings = stats.reference(ledgers, kept, calendar)
    return Derived(
        reference=reference,
        ledger_hashes=hashes,
        categories=sorted({r.category for r in kept}),
        kept=len(kept),
        excluded=excluded,
        warnings=warnings,
    )
