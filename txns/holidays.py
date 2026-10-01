"""Philippine holiday calendar: committed data, carried by every bundle (FR-D1, FR-E7).

The committed calendar lives at `inputs/ph-holidays.json` (spec default 4: input
data, not LLM-drafted). `author` copies the years a bundle needs into the
bundle's `holidays.json` with `for_years`; `generate` reads only the bundle's
copy. Both files share one schema:

    {"format": 1, "country": "PH", "note": "...",
     "years": {"2026": {
        "proclamations": [{"id": "Proclamation No. 1006, s. 2025", "signed": "2025-09-03"?,
                           "subject": "...", "url": "..."?}],
        "days": [{"date": "2026-01-01", "name": "New Year's Day",
                  "kind": "regular" | "special", "source": <a proclamation id of that year>}]}}}

`regular` = regular holiday; `special` = special (non-working) day. Local days
and special working days are not listed. Holy Week comes from `easter()`; the
calendar's Maundy Thursday and Good Friday agree with it (tested).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

COMMITTED_PATH = Path("inputs") / "ph-holidays.json"  # relative to the repo / working folder
BUNDLE_FILE = "holidays"  # holidays.json in a bundle
KINDS = ("regular", "special")


class CalendarInvalid(ValueError):
    pass


@dataclass(frozen=True)
class Holiday:
    date: date
    name: str
    kind: str  # regular | special
    source: str  # proclamation id


@dataclass(frozen=True)
class HolidayCalendar:
    years: tuple[int, ...]  # years the calendar covers, sorted
    days: Mapping[date, Holiday]

    @classmethod
    def empty(cls) -> "HolidayCalendar":
        return cls((), MappingProxyType({}))

    def covers(self, year: int) -> bool:
        return year in self.years

    def get(self, d: date) -> Holiday | None:
        return self.days.get(d)

    def is_regular(self, d: date) -> bool:
        h = self.days.get(d)
        return h is not None and h.kind == "regular"

    def is_special(self, d: date) -> bool:
        h = self.days.get(d)
        return h is not None and h.kind == "special"

    def regular_between(self, start: date, end: date) -> list[date]:
        return sorted(d for d, h in self.days.items() if h.kind == "regular" and start <= d <= end)

    def missing_years(self, start: date, end: date) -> list[int]:
        return [y for y in range(start.year, end.year + 1) if y not in self.years]


@lru_cache(maxsize=None)
def easter(year: int) -> date:
    """Western (Gregorian) Easter Sunday, anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def holy_week(year: int) -> tuple[date, date]:
    """Palm Sunday to Easter Sunday, inclusive."""
    sunday = easter(year)
    return sunday - timedelta(days=7), sunday


def _bad(where: str, msg: str) -> CalendarInvalid:
    return CalendarInvalid(f"{where}: {msg}")


def parse(data: Any, where: str = "holidays.json") -> HolidayCalendar:
    """Validate calendar JSON and return the typed view; raises CalendarInvalid."""
    if not isinstance(data, dict) or not isinstance(data.get("years"), dict):
        raise _bad(where, "must be an object with a `years` table")
    days: dict[date, Holiday] = {}
    years: list[int] = []
    for key, table in data["years"].items():
        if not (isinstance(key, str) and len(key) == 4 and key.isdigit()):
            raise _bad(where, f"year key {key!r} must be a 4-digit year")
        year = int(key)
        years.append(year)
        if not isinstance(table, dict):
            raise _bad(where, f"{key} must be a table")
        procs = table.get("proclamations")
        if not isinstance(procs, list) or not procs:
            raise _bad(where, f"{key} must cite at least one proclamation")
        ids = set()
        for p in procs:
            if not isinstance(p, dict) or not isinstance(p.get("id"), str) or not p["id"]:
                raise _bad(where, f"{key}: every proclamation needs an `id`")
            ids.add(p["id"])
        entries = table.get("days")
        if not isinstance(entries, list):
            raise _bad(where, f"{key} needs a `days` list")
        for e in entries:
            if not isinstance(e, dict):
                raise _bad(where, f"{key}: every day must be a table")
            try:
                d = date.fromisoformat(e.get("date", ""))
            except (TypeError, ValueError):
                raise _bad(where, f"{key}: bad date {e.get('date')!r}") from None
            if d.year != year:
                raise _bad(where, f"{d} is listed under {key}")
            if d in days:
                raise _bad(where, f"{d} is listed twice")
            if e.get("kind") not in KINDS:
                raise _bad(where, f"{d}: kind must be one of {', '.join(KINDS)}")
            if not isinstance(e.get("name"), str) or not e["name"]:
                raise _bad(where, f"{d}: missing `name`")
            if e.get("source") not in ids:
                raise _bad(where, f"{d}: source {e.get('source')!r} is not a proclamation cited for {key}")
            days[d] = Holiday(d, e["name"], e["kind"], e["source"])
    return HolidayCalendar(tuple(sorted(years)), MappingProxyType(dict(sorted(days.items()))))


def load_committed(root: Path) -> dict[str, Any]:
    """The committed calendar JSON under `root` (the repo or working folder)."""
    return json.loads((root / COMMITTED_PATH).read_text(encoding="utf-8"))


def for_years(data: Mapping[str, Any], years: Iterable[int]) -> dict[str, Any]:
    """The calendar JSON cut down to `years` (for `author` to write into a bundle).

    Raises CalendarInvalid when a requested year is not in the calendar.
    """
    wanted = sorted({int(y) for y in years})
    missing = [y for y in wanted if str(y) not in data["years"]]
    if missing:
        raise CalendarInvalid(f"holiday calendar has no data for {', '.join(map(str, missing))}")
    out = {k: v for k, v in data.items() if k != "years"}
    out["years"] = {str(y): data["years"][str(y)] for y in wanted}
    return out
