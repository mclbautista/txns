"""Spend-only rule (FR-B3, ADR 0004), driven by the committed list `inputs/spend-only.json`.

The list names the sources that are spend, the sources that are not, the
account categories that are excluded (sold services, write-offs, lines paying
a person) and the categories whose rows are subscriptions. The file's `note`
states the rule; reasons are for the reviewer. A source in neither list is an
error, so a new kind of ledger line is looked at before it counts as spend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from txns.ledger.xero import LedgerError, LedgerRow

COMMITTED_PATH = Path("inputs") / "spend-only.json"
BLANK_SOURCE = "(blank)"  # how a row with an empty Source cell is named in the list

CREDIT_LINE = "credit line (no debit)"


@dataclass(frozen=True)
class SpendRules:
    spend_sources: frozenset[str]
    excluded_sources: frozenset[str]
    excluded_categories: frozenset[str]
    subscription_categories: frozenset[str]

    def exclusion(self, row: LedgerRow) -> str | None:
        """None when the row is spend, else why it is left out (a source, a category or CREDIT_LINE).

        Raises LedgerError for a source the list does not name.
        """
        source = row.source or BLANK_SOURCE
        if source in self.excluded_sources:
            return f"source {source}"
        if source not in self.spend_sources:
            raise LedgerError(
                f"{row.ledger} line {row.line}: source {source!r} is not in {COMMITTED_PATH.as_posix()}; "
                "add it to spend_sources or excluded_sources"
            )
        if row.category in self.excluded_categories:
            return f"category {row.category}"
        if row.debit <= 0 or row.credit != 0:
            return CREDIT_LINE
        return None


def _names(data: dict[str, Any], key: str, *, table: bool) -> frozenset[str]:
    value = data.get(key)
    ok = isinstance(value, dict) if table else isinstance(value, list)
    if not ok or not all(isinstance(k, str) and k for k in value):
        kind = "a table of name = reason" if table else "a list of names"
        raise LedgerError(f"{COMMITTED_PATH.as_posix()}: `{key}` must be {kind}")
    return frozenset(value)


def parse(data: Any) -> SpendRules:
    if not isinstance(data, dict):
        raise LedgerError(f"{COMMITTED_PATH.as_posix()}: must be a JSON object")
    rules = SpendRules(
        spend_sources=_names(data, "spend_sources", table=False),
        excluded_sources=_names(data, "excluded_sources", table=True),
        excluded_categories=_names(data, "excluded_categories", table=True),
        subscription_categories=_names(data, "subscription_categories", table=False),
    )
    both = rules.spend_sources & rules.excluded_sources
    if both:
        raise LedgerError(f"{COMMITTED_PATH.as_posix()}: sources both kept and excluded: {', '.join(sorted(both))}")
    return rules


def load_committed(root: Path) -> SpendRules:
    path = root / COMMITTED_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise LedgerError(f"spend-only list not found: {COMMITTED_PATH.as_posix()}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerError(f"{COMMITTED_PATH.as_posix()} unreadable: {exc}") from None
    return parse(data)
