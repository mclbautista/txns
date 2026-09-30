"""Data passed between engine stages.

Occurrence -> (drawer) Row without text -> (text) Row with text -> (messiness) ordered rows.
Rows keep their catalog item id and storyline for the scorecard; only
date, qty, unit_price and text reach the CSV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Occurrence:
    """One planned purchase of one item on one date (no amounts yet)."""

    item_id: str
    storyline: str
    date: date
    tags: tuple[str, ...] = ()  # e.g. ("deposit",), ("batch",); free-form per archetype


@dataclass(frozen=True)
class Row:
    date: date
    qty: int | Decimal  # Decimal only on items marked decimal (FR-F3)
    unit_price: int  # centavos
    text: str
    item_id: str | None = None  # None when scoring an external CSV
    storyline: str | None = None
    price_point: int | None = None  # index into the item's rate-card points
    tags: tuple[str, ...] = field(default=())

    @property
    def amount(self) -> int | Decimal:
        """qty x unit_price, in centavos (an int whenever it is whole, as it always is for bundle rows)."""
        amount = self.qty * self.unit_price
        if isinstance(amount, Decimal) and amount == amount.to_integral_value():
            return int(amount)
        return amount
