"""Bundle vocabulary (FR-C3, FR-H3): `vocabulary.json`, optional.

    {"date_tails": ["({mon} {d}, {yyyy})", "for {m}/{d}"]}

`date_tails` are the formats of the short original-date tail a batch-logged row
may carry after its item text ("Delivery fee (Oct 28, 2026)"). `author` drafts
them; without the file or the key, DEFAULT_DATE_TAILS applies. Placeholders:

    {d} {dd}        day of month, 7 / 07
    {m} {mm}        month number, 3 / 03
    {mon} {month}   month name, Mar / March
    {yy} {yyyy}     year, 26 / 2026

A format must name the day ({d} or {dd}) and the month, be printable ASCII
without ` - ` (that separates a vendor prefix), and render to at most
MAX_TAIL characters; a bundle that breaks this exits 4. The tail is appended
after one space. `strip_date_tail` undoes it so the scorecard still maps a
tailed row to its catalog item.
"""

from __future__ import annotations

import calendar as _calendar
import re
from datetime import date
from typing import Any, Mapping, Sequence

from txns.errors import BundleInvalid

DEFAULT_DATE_TAILS = ("({mon} {d}, {yyyy})",)
MAX_TAIL = 24
_FIELD = re.compile(r"\{([a-z]+)\}")
_DAY, _MONTH = ("d", "dd"), ("m", "mm", "mon", "month")
_MONTHS = tuple(_calendar.month_name[1:])


def _render_field(name: str, day: date) -> str:
    return {
        "d": str(day.day),
        "dd": f"{day.day:02d}",
        "m": str(day.month),
        "mm": f"{day.month:02d}",
        "mon": _MONTHS[day.month - 1][:3],
        "month": _MONTHS[day.month - 1],
        "yy": f"{day.year % 100:02d}",
        "yyyy": str(day.year),
    }[name]


_PATTERNS = {
    "d": r"(?:[1-9]|[12][0-9]|3[01])",
    "dd": r"(?:0[1-9]|[12][0-9]|3[01])",
    "m": r"(?:[1-9]|1[0-2])",
    "mm": r"(?:0[1-9]|1[0-2])",
    "mon": "(?:" + "|".join(m[:3] for m in _MONTHS) + ")",
    "month": "(?:" + "|".join(_MONTHS) + ")",
    "yy": r"[0-9]{2}",
    "yyyy": r"[0-9]{4}",
}


def problems(vocabulary: Any) -> list[str]:
    """What is wrong with a `vocabulary.json` document (empty when it is usable)."""
    if vocabulary is None:
        return []
    if not isinstance(vocabulary, Mapping):
        return ["vocabulary.json must be a table"]
    tails = vocabulary.get("date_tails")
    if tails is None:
        return []
    if not isinstance(tails, list) or not tails:
        return ["vocabulary.json `date_tails` must be a non-empty list of formats"]
    out = []
    sample = date(2026, 12, 28)  # longest day and month numbers, a long month name
    longest = date(2026, 9, 28)  # "September"
    for fmt in tails:
        if not isinstance(fmt, str) or not fmt.strip() or fmt != fmt.strip():
            out.append(f"date tail {fmt!r} must be a non-blank string without outer spaces")
            continue
        names = _FIELD.findall(fmt)
        unknown = sorted(set(names) - set(_PATTERNS))
        if unknown:
            out.append(f"date tail {fmt!r} uses unknown placeholder(s) {', '.join(unknown)}")
            continue
        if not any(n in _DAY for n in names) or not any(n in _MONTH for n in names):
            out.append(f"date tail {fmt!r} must name the day and the month")
            continue
        literal = _FIELD.sub("", fmt)
        if "{" in literal or "}" in literal:
            out.append(f"date tail {fmt!r} has a stray brace")
            continue
        if any(not " " <= c <= "~" for c in fmt) or " - " in f" {fmt} ":
            out.append(f"date tail {fmt!r} must be printable ASCII without ' - '")
            continue
        if max(len(render(fmt, sample)), len(render(fmt, longest))) > MAX_TAIL:
            out.append(f"date tail {fmt!r} renders longer than {MAX_TAIL} characters")
    return out


def check(vocabulary: Any) -> None:
    bad = problems(vocabulary)
    if bad:
        raise BundleInvalid(f"bundle invalid: {bad[0]}")


def date_tails(bundle) -> tuple[str, ...]:
    """The bundle's date-tail formats (vocabulary.json), else the default."""
    vocabulary = bundle.data.get("vocabulary") or {}
    tails = vocabulary.get("date_tails") if isinstance(vocabulary, Mapping) else None
    return tuple(tails) if tails else DEFAULT_DATE_TAILS


def render(fmt: str, day: date) -> str:
    return _FIELD.sub(lambda m: _render_field(m.group(1), day), fmt)


def tail_regex(fmt: str) -> str:
    """A regex matching one rendered tail of `fmt`."""
    parts, pos = [], 0
    for m in _FIELD.finditer(fmt):
        parts.append(re.escape(fmt[pos : m.start()]))
        parts.append(_PATTERNS[m.group(1)])
        pos = m.end()
    parts.append(re.escape(fmt[pos:]))
    return "".join(parts)


def tail_pattern(formats: Sequence[str]) -> re.Pattern:
    """Matches `<text> <tail>` for any of the formats; group 1 is the text."""
    return re.compile(r"(.+?) (?:" + "|".join(f"(?:{tail_regex(f)})" for f in formats) + r")\Z", re.DOTALL)


def strip_date_tail(pattern: re.Pattern, text: str) -> str | None:
    """The item text without its date tail, or None when it carries none."""
    m = pattern.match(text)
    return m.group(1) if m else None
