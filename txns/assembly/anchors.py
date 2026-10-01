"""Committed price anchors: `inputs/price-anchors.json` (ticket 02 fact sheet, items the ledgers never show).

    anchors = load(root)                    # list[Anchor]; a missing or malformed file exits 2
    anchor = find(anchors, texts)           # the first entry one of whose keywords names the item

File schema (money in int centavos per rate-card unit):

    {"format": 1, "note": "...",
     "anchors": [{"key": "lto8_cartridge",
                  "keywords": ["lto-8", "lto8"],          whole words, any case; "-" and spaces alike
                  "unit_prices"?: [400000, 460000],       one per seller, cheapest first; absent = no
                                                          sourced price (a matching item is left out)
                  "pack_pcs"?: 25,                        prices are per pack of this many pieces
                  "quantities"?: [{"qty": 1, "weight": 2}, ...],
                  "confidence"?: "low", "source": "where the figure comes from"}]}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from txns.errors import MissingInput
from txns.money import is_tidy_cents

COMMITTED_PATH = Path("inputs") / "price-anchors.json"  # relative to the working folder
_WORDS = re.compile(r"[a-z0-9]+")


def words(text: str) -> str:
    """Casefolded words joined by single spaces ("LTO-8 tape" -> "lto 8 tape")."""
    return " ".join(_WORDS.findall(text.casefold()))


@dataclass(frozen=True)
class Anchor:
    key: str
    keywords: tuple[str, ...]  # normalised with `words`
    unit_prices: tuple[int, ...]  # empty: no sourced price
    pack_pcs: int | None
    quantities: tuple[dict, ...]
    confidence: str
    source: str

    def names(self, text: str) -> bool:
        padded = f" {words(text)} "
        return any(f" {k} " in padded for k in self.keywords)

    def record(self) -> dict[str, Any]:
        """What the bundle keeps of the entry (`anchors.json`), so `approve` can re-check prices offline."""
        out: dict[str, Any] = {"key": self.key, "unit_prices": list(self.unit_prices),
                               "confidence": self.confidence, "source_note": self.source}
        if self.pack_pcs:
            out["pack_pcs"] = self.pack_pcs
        if self.quantities:
            out["quantities"] = [dict(q) for q in self.quantities]
        return out


def _bad(msg: str) -> MissingInput:
    return MissingInput(f"{COMMITTED_PATH.as_posix()}: {msg}")


def _pos_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def parse(data: Any) -> list[Anchor]:
    if not isinstance(data, dict) or not isinstance(data.get("anchors"), list):
        raise _bad("must be an object with an `anchors` list")
    out, keys = [], set()
    for i, e in enumerate(data["anchors"]):
        where = f"anchors[{i}]"
        if not isinstance(e, dict) or not isinstance(e.get("key"), str) or not e["key"]:
            raise _bad(f"{where} needs a `key`")
        if e["key"] in keys:
            raise _bad(f"{where}: key `{e['key']}` listed twice")
        keys.add(e["key"])
        kws = e.get("keywords")
        if not isinstance(kws, list) or not kws or not all(isinstance(k, str) and words(k) for k in kws):
            raise _bad(f"{where} needs a non-empty `keywords` list")
        prices = e.get("unit_prices") or []
        if not isinstance(prices, list) or not all(_pos_int(p) and is_tidy_cents(p) for p in prices):
            raise _bad(f"{where}: `unit_prices` must be positive int centavos with tidy cents (.00/.50/.75)")
        pcs = e.get("pack_pcs")
        if pcs is not None and not (_pos_int(pcs) and pcs >= 2):
            raise _bad(f"{where}: `pack_pcs` must be an integer of at least 2")
        qtys = e.get("quantities") or []
        if not isinstance(qtys, list) or not all(
            isinstance(q, dict) and _pos_int(q.get("qty")) and isinstance(q.get("weight"), int) and q["weight"] >= 0
            for q in qtys
        ):
            raise _bad(f"{where}: `quantities` must list {{qty, weight}} with whole numbers")
        out.append(Anchor(
            key=e["key"],
            keywords=tuple(words(k) for k in kws),
            unit_prices=tuple(prices),
            pack_pcs=pcs,
            quantities=tuple({"qty": q["qty"], "weight": q["weight"]} for q in qtys),
            confidence=str(e.get("confidence", "unsourced")),
            source=str(e.get("source", "")),
        ))
    return out


def load(root: Path) -> list[Anchor]:
    path = root / COMMITTED_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MissingInput(f"committed price anchors not found: {COMMITTED_PATH.as_posix()}") from None
    except (OSError, ValueError) as exc:
        raise _bad(f"unreadable: {exc}") from None
    return parse(data)


def find(anchors: Iterable[Anchor], texts: Iterable[str]) -> Anchor | None:
    texts = list(texts)
    for a in anchors:
        if any(a.names(t) for t in texts):
            return a
    return None
