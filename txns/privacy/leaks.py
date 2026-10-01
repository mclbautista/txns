"""Leak check: find real ledger names that are not on the brand allowlist (FR-C2, FR-D2 gate 3).

Deliberately separate from the scrubber: it replaces nothing and searches each
blocked name on its own, so a scrubber gap (a name it misses, a code path that
skips it) is still caught. A hit only passes when it lies inside a longer
allowlisted brand ("Acme" inside an allowlisted "Acme Music").

Reports give *where* a name was found, never the name itself, so a report can
be pasted into a ticket without leaking it.

    find_in_object(payload, index)          -> ["$.categories[2].item_texts[0].text", ...]
    find_in_files(paths, index, root=root)  -> ["bundle/text.json: $.coffee.descriptive[1]", "notes.txt line 3", ...]
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from txns.privacy.names import NameIndex, form_pattern


class LeakDetector:
    def __init__(self, index: NameIndex):
        self._blocked = [(f, re.compile(form_pattern(f))) for n in index.blocked for f in sorted(n.forms)]
        self._allowed = [(f, re.compile(form_pattern(f))) for f in sorted(index.shields)]

    def leaks(self, text: str) -> bool:
        """True when `text` holds a blocked ledger name."""
        folded = " ".join(text.split()).casefold()
        hits = [m.span() for f, rx in self._blocked if f in folded for m in rx.finditer(folded)]
        if not hits:
            return False
        shields = [m.span() for f, rx in self._allowed if f in folded for m in rx.finditer(folded)]
        return any(
            not any(s <= a and b <= e and (e - s) > (b - a) for s, e in shields)
            for a, b in hits
        )


def _walk(value: Any, path: str) -> Iterable[tuple[str, str]]:
    """(location, string) for every string in a JSON-like object, keys included."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            k = str(k)
            yield f"{path} (key)", k
            yield from _walk(v, f"{path}.{k}" if re.fullmatch(r"\w+", k) else f"{path}[{json.dumps(k)}]")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def _locations(value: Any, detector: LeakDetector, root: str = "$") -> list[str]:
    seen: list[str] = []
    for where, text in _walk(value, root):
        if detector.leaks(text) and where not in seen:
            seen.append(where)
    return seen


def find_in_object(value: Any, index: NameIndex) -> list[str]:
    """Locations (JSON paths) of strings in `value` that hold a blocked ledger name."""
    return _locations(value, LeakDetector(index))


def find_in_files(paths: Iterable[Path], index: NameIndex, *, root: Path | None = None) -> list[str]:
    """Locations of blocked ledger names in files: the reusable check for promotion gates.

    `.json` files are parsed, so escaped text (`\\u00f1`) is checked too; other files
    are checked line by line. A file's own path (relative to `root`) is checked as well.
    Folders are searched recursively.
    """
    detector = LeakDetector(index)
    out: list[str] = []
    files: list[Path] = []
    for p in paths:
        files.extend(sorted(q for q in p.rglob("*") if q.is_file()) if p.is_dir() else [p])
    for path in files:
        name = path.relative_to(root).as_posix() if root else path.as_posix()
        if detector.leaks(name):
            out.append(f"{name}: file name")
        data = path.read_bytes().decode("utf-8", errors="replace")
        if path.suffix == ".json":
            try:
                parsed = json.loads(data)
            except ValueError:
                pass
            else:
                out.extend(f"{name}: {where}" for where in _locations(parsed, detector))
                continue
        for no, line in enumerate(data.splitlines(), start=1):
            if detector.leaks(line):
                out.append(f"{name} line {no}")
    return out
