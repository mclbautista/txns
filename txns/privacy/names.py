"""Ledger names, the brand allowlist and the deterministic scrubber (FR-C2, ADR 0007).

A *ledger name* is any of (ticket 13):

- the vendor part of a description (text before the first " - ", or the whole
  description when it has none), on every parsed row, kept or not;
- the Reference column of every row (projects, events, clients);
- the organisation line of each export.

Names are compared by a *key*: whitespace collapsed, casefolded, outer
punctuation trimmed and trailing legal suffixes dropped ("Acme Trading, Inc."
-> "acme trading"). A name is searched in text by its full form and its key
form, whole words only, case-insensitive. Names without a letter or shorter
than 2 characters are ignored.

The brand allowlist (`inputs/brands-allowlist.txt`, supplied by the owner) holds
one brand per line; blank lines and lines starting with `#` are ignored. A
ledger name whose key equals an entry's key is kept (vendor lists show the
allowlist's spelling). Every other ledger name is replaced by a stable
fabricated name (`fakes.fake_name`).

Allowlisted brands and the ledgers' account headings are *shields*: a blocked
name inside a longer shield is left alone by the scrubber and the leak check
(a heading like "Network and IT Expense" survives a vendor called "Network").
Everywhere else the name is replaced. The real-to-fake map (`NameIndex.private_map`)
must stay on this machine: never in a payload, a bundle or git.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from txns.ledger.stats import vendor as vendor_part
from txns.ledger.xero import Ledger
from txns.privacy import fakes

ALLOWLIST_PATH = Path("inputs") / "brands-allowlist.txt"  # relative to the working folder
LEGAL_SUFFIXES = frozenset(
    ("inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
     "llc", "opc", "pty", "ag", "gmbh", "plc")
)
_OUTER = " .,;:-_'\"()"
_MAX_ATTEMPTS = 1000
_WORD = re.compile(r"\w+")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def normalize(name: str) -> str:
    """Whitespace collapsed, casefolded, outer punctuation trimmed."""
    return _collapse(name).casefold().strip(_OUTER)


def key(name: str) -> str:
    """The comparison key: `normalize` minus trailing legal suffixes ("Acme, Inc." -> "acme")."""
    tokens = normalize(name).split(" ")
    while len(tokens) > 1 and tokens[-1].strip(".,") in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens).strip(_OUTER)


def _usable(k: str) -> bool:
    return len(k) >= 2 and any(ch.isalpha() for ch in k)


def _forms(name: str) -> set[str]:
    return {f for f in (normalize(name), key(name)) if _usable(f)}


def form_pattern(form: str) -> str:
    """Regex for one casefolded form: whole words, any run of whitespace between them."""
    return r"(?<!\w)" + r"\s+".join(re.escape(part) for part in form.split(" ")) + r"(?!\w)"


# ---------------------------------------------------------------------------
# Allowlist


@dataclass(frozen=True)
class Allowlist:
    entries: dict[str, str]  # key -> spelling as written in the file
    found: bool  # False when the file does not exist (treated as empty)

    def spelling(self, k: str) -> str | None:
        return self.entries.get(k)


def parse_allowlist(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = _collapse(line)
        if not line or line.startswith("#"):
            continue
        k = key(line)
        if _usable(k):
            out.setdefault(k, line)
    return out


def load_allowlist(root: Path) -> Allowlist:
    """Read `inputs/brands-allowlist.txt` under `root`; a missing file is an empty list."""
    path = root / ALLOWLIST_PATH
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return Allowlist({}, found=False)
    return Allowlist(parse_allowlist(text), found=True)


# ---------------------------------------------------------------------------
# Name index


@dataclass(frozen=True)
class LedgerName:
    key: str
    kind: str  # "organisation" | "vendor" | "reference" (fakes.KINDS)
    spellings: tuple[str, ...]  # real spellings seen (sorted): local reports only
    forms: frozenset[str]  # casefolded search forms
    allowlisted: bool
    label: str  # what the payload shows: the allowlist spelling, else the fake


def collect(ledgers: Sequence[Ledger]) -> dict[str, tuple[str, set[str]]]:
    """Every ledger name: key -> (kind, spellings)."""
    found: dict[str, tuple[str, set[str]]] = {}

    def add(raw: str, kind: str) -> None:
        raw = _collapse(raw)
        k = key(raw)
        if not _usable(k):
            return
        old_kind, spellings = found.get(k, (kind, set()))
        spellings.add(raw)
        found[k] = (min(old_kind, kind, key=fakes.KINDS.index), spellings)

    for ledger in ledgers:
        add(ledger.organisation, "organisation")
        for row in ledger.rows:
            add(vendor_part(row.description), "vendor")
            add(row.reference, "reference")
    return found


class NameIndex:
    """Ledger names with their labels; scrubs text (`scrub`) and objects (`scrub_obj`)."""

    def __init__(self, ledgers: Sequence[Ledger], allowlist: Allowlist):
        self.allowlist = allowlist
        found = collect(ledgers)
        self.names: dict[str, LedgerName] = {}
        pending = []
        for k in sorted(found):
            kind, spellings = found[k]
            forms = frozenset(f for s in spellings for f in _forms(s))
            spellings_seen = tuple(sorted(spellings))
            brand = allowlist.spelling(k)
            if brand is not None:
                self.names[k] = LedgerName(k, kind, spellings_seen, forms, True, brand)
            else:
                pending.append((k, kind, spellings_seen, forms))

        # Forms a fake must not contain: every real name and every allowlisted brand.
        taboo = {f for n in self.names.values() for f in n.forms}
        taboo |= {f for _, _, _, forms in pending for f in forms}
        taboo |= {f for b in allowlist.entries.values() for f in _forms(b)}
        taboo_re = _alternation(taboo)
        used: set[str] = set()
        for k, kind, spellings_seen, forms in pending:
            for attempt in range(_MAX_ATTEMPTS):
                fake = fakes.fake_name(kind, k, attempt)
                if key(fake) not in used and not (taboo_re and taboo_re.search(fake.casefold())):
                    break
            else:  # pragma: no cover - thousands of free names per kind
                raise RuntimeError("ran out of fabricated names")
            used.add(key(fake))
            self.names[k] = LedgerName(k, kind, spellings_seen, forms, False, fake)

        # Shields: text that may appear although a blocked name sits inside it, when it is
        # longer than that name: allowlisted brands and the account headings (the chart of
        # accounts, used as category labels; "Network and IT Expense" keeps its "Network").
        self.shields: frozenset[str] = frozenset(
            {f for n in self.names.values() if n.allowlisted for f in n.forms}
            | {f for b in allowlist.entries.values() for f in _forms(b)}
            | {f for ledger in ledgers for c in ledger.categories for f in _forms(c)}
        )

        # Every form, longest first, so "Acme Trading Hardware" wins over "Acme Trading"
        # and a shield keeps the names inside it. A blocked name beats an equal shield.
        action: dict[str, LedgerName | None] = {f: None for f in self.shields}
        action.update((f, n) for n in self.blocked for f in n.forms)
        self._forms = sorted(action.items(), key=lambda fa: (-len(fa[0]), fa[0]))
        self._by_word: dict[str, list[int]] = {}
        for i, (f, _) in enumerate(self._forms):
            self._by_word.setdefault(_WORD.search(f).group(0), []).append(i)
        self._compiled: dict[tuple[int, ...], re.Pattern] = {}

    # -- lookups ------------------------------------------------------------

    def label(self, raw: str) -> str | None:
        """The payload label for a raw ledger name (allowlist spelling or fake); None if not a name."""
        n = self.names.get(key(raw))
        return None if n is None else n.label

    @property
    def blocked(self) -> list[LedgerName]:
        """Names that must never leave the machine (not on the allowlist)."""
        return [n for n in self.names.values() if not n.allowlisted]

    def private_map(self) -> dict[str, dict[str, str]]:
        """Every real spelling -> {fake, kind}. LOCAL ONLY: never put this in a payload, bundle or git."""
        return {s: {"fake": n.label, "kind": n.kind} for n in self.blocked for s in n.spellings}

    # -- scrubbing ----------------------------------------------------------

    def scrub(self, text: str) -> str:
        """Replace every non-allowlisted ledger name in `text` by its fake."""
        # A form matches only where its first word is a whole word of the text, so only
        # forms whose first word occurs are tried (one big alternation is far too slow).
        idxs = sorted({i for w in set(_WORD.findall(text.casefold())) for i in self._by_word.get(w, ())})
        if not idxs:
            return text
        rx = self._compiled.get(tuple(idxs))
        if rx is None:
            rx = re.compile("|".join(f"(?P<n{i}>{form_pattern(self._forms[i][0])})" for i in idxs), re.IGNORECASE)
            self._compiled[tuple(idxs)] = rx

        def repl(m: re.Match) -> str:
            n = self._forms[int(m.lastgroup[1:])][1]
            return m.group(0) if n is None else n.label

        return rx.sub(repl, text)

    def scrub_obj(self, value: Any) -> Any:
        """`scrub` applied to every string (keys and values) of a JSON-like object."""
        if isinstance(value, str):
            return self.scrub(value)
        if isinstance(value, dict):
            return {self.scrub(k): self.scrub_obj(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.scrub_obj(v) for v in value]
        return value


def _alternation(forms: Iterable[str]) -> re.Pattern | None:
    forms = sorted(set(forms), key=lambda f: (-len(f), f))
    if not forms:
        return None
    return re.compile("|".join(form_pattern(f) for f in forms), re.IGNORECASE)
