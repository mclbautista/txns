"""Calendar helpers for the planner. Holidays, seasons and month-end shape go here (ticket 04)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterator

# Monday..Sunday, the ledger's weekday shape (FR-E6); bundles may override per archetype.
DEFAULT_WEEKDAY_WEIGHTS = (17, 17, 17, 17, 17, 9, 5)


def days(start: date, end: date) -> Iterator[date]:
    d = start
    one = timedelta(days=1)
    while d <= end:
        yield d
        d += one
