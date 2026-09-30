"""Typed, read-only view of a loaded bundle.

Bundle files (all JSON, top level of the bundle folder):

- manifest.json   label, hash, promotion, reviewed, interpreter, generator,
                  model, served_models, ledger_hashes, format
- catalog.json    {"items": {item_id: {storyline, category, class, archetype, params}}}
- rate_cards.json {item_id: {"points": [{unit_price (int centavos), seller}],
                             "quantities": [{qty (int), weight (int)}]}}
- text.json       {item_id: {"descriptive": [...], "terse": [...]}}
- storylines.json {name: {description, ...}}
- rules.json      {"archetypes": {name: {...}}, "tier_factors": {...}}
- reference.json  ledger statistics (filled by later tickets)

Any other *.json file is loaded into `Bundle.data[<stem>]` untouched, so a new
file can be added to the bundle without changing this loader. To add a field
to an item, read it from `Item.params` (archetype parameters) or `Item.raw`
(the item's catalog entry), or add a typed field here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from txns.errors import BundleInvalid

REQUIRED_FILES = (
    "manifest",
    "catalog",
    "rate_cards",
    "text",
    "storylines",
    "rules",
    "reference",
)
PRICE_CLASSES = ("subscription", "retail", "big_ticket")


@dataclass(frozen=True)
class PricePoint:
    unit_price: int  # centavos
    seller: str | None = None


@dataclass(frozen=True)
class QtyOption:
    qty: int
    weight: int


@dataclass(frozen=True)
class Item:
    id: str
    storyline: str
    category: str
    price_class: str
    archetype: str
    params: Mapping[str, Any]
    price_points: tuple[PricePoint, ...]
    quantities: tuple[QtyOption, ...]
    descriptive: tuple[str, ...]
    terse: tuple[str, ...]
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def round_figures_approved(self) -> bool:
        """Only approved big-ticket items may carry round-thousand amounts (FR-F6).

        The one place that decides it; the drawer and the plug-row check both ask here.
        """
        return self.price_class == "big_ticket"


@dataclass(frozen=True)
class Bundle:
    id: str  # folder name, "<label>-<hash12>"
    path: Path
    hash: str  # full content hash
    manifest: Mapping[str, Any]
    items: Mapping[str, Item]  # sorted by item id
    storylines: Mapping[str, Mapping[str, Any]]  # sorted by name
    rules: Mapping[str, Any]
    reference: Mapping[str, Any]
    data: Mapping[str, Any]  # every JSON file by stem, raw

    @property
    def label(self) -> str:
        return self.manifest["label"]

    @property
    def reviewed(self) -> bool:
        return bool(self.manifest.get("reviewed", False))

    @property
    def interpreter(self) -> str | None:
        return self.manifest.get("interpreter")

    def archetype_rules(self, name: str) -> Mapping[str, Any]:
        return self.rules.get("archetypes", {}).get(name, {})

    def gap_rules(self, item: Item) -> tuple[int, int]:
        """(max rows per day, minimum gap in days) for an item (FR-E5).

        Per-item `params` override the archetype's rules; defaults 1 and 1.
        """
        rules = self.archetype_rules(item.archetype)
        max_per_day = item.params.get("max_per_day", rules.get("max_per_day", 1))
        min_gap = item.params.get("min_gap_days", rules.get("min_gap_days", 1))
        return int(max_per_day), int(min_gap)

    def items_in(self, storyline: str) -> list[Item]:
        return [i for i in self.items.values() if i.storyline == storyline]


def _bad(msg: str) -> BundleInvalid:
    return BundleInvalid(f"bundle invalid: {msg}")


def read_json_files(folder: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for path in sorted(folder.glob("*.json")):
        try:
            data[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise _bad(f"{path.name}: {exc}") from None
    missing = [f"{name}.json" for name in REQUIRED_FILES if name not in data]
    if missing:
        raise _bad(f"missing {', '.join(missing)}")
    return data


def _pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def build(bundle_id: str, folder: Path, full_hash: str, data: dict[str, Any]) -> Bundle:
    """Structural checks that `generate` relies on; failures exit 4."""
    storylines = data["storylines"]
    if not isinstance(storylines, dict) or not storylines:
        raise _bad("storylines.json must map at least one storyline name to its settings")
    catalog = data["catalog"].get("items") if isinstance(data["catalog"], dict) else None
    if not isinstance(catalog, dict) or not catalog:
        raise _bad("catalog.json must have a non-empty `items` table")
    rate_cards, text = data["rate_cards"], data["text"]
    items: dict[str, Item] = {}
    for item_id in sorted(catalog):
        entry = catalog[item_id]
        where = f"item `{item_id}`"
        if not isinstance(entry, dict):
            raise _bad(f"{where}: catalog entry must be a table")
        for key in ("storyline", "category", "class", "archetype"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise _bad(f"{where}: missing `{key}`")
        if entry["storyline"] not in storylines:
            raise _bad(f"{where}: unknown storyline `{entry['storyline']}`")
        if entry["class"] not in PRICE_CLASSES:
            raise _bad(f"{where}: class must be one of {', '.join(PRICE_CLASSES)}")
        card = rate_cards.get(item_id)
        if not isinstance(card, dict):
            raise _bad(f"{where}: no rate card")
        points = card.get("points") or []
        if not points or not all(isinstance(p, dict) and _pos_int(p.get("unit_price")) for p in points):
            raise _bad(f"{where}: rate card needs price points with positive integer `unit_price` (centavos)")
        qtys = card.get("quantities") or []
        if not qtys or not all(
            isinstance(q, dict) and _pos_int(q.get("qty")) and isinstance(q.get("weight"), int) and q["weight"] >= 0
            for q in qtys
        ) or sum(q["weight"] for q in qtys) <= 0:
            raise _bad(f"{where}: rate card needs an allowed quantity set with integer weights")
        variants = text.get(item_id)
        if not isinstance(variants, dict):
            raise _bad(f"{where}: no text variants")
        descriptive = tuple(variants.get("descriptive") or ())
        terse = tuple(variants.get("terse") or ())
        if not descriptive and not terse:
            raise _bad(f"{where}: no text variants")
        items[item_id] = Item(
            id=item_id,
            storyline=entry["storyline"],
            category=entry["category"],
            price_class=entry["class"],
            archetype=entry["archetype"],
            params=MappingProxyType(dict(entry.get("params") or {})),
            price_points=tuple(PricePoint(p["unit_price"], p.get("seller")) for p in points),
            quantities=tuple(QtyOption(q["qty"], q["weight"]) for q in qtys),
            descriptive=descriptive,
            terse=terse,
            raw=MappingProxyType(entry),
        )
    return Bundle(
        id=bundle_id,
        path=folder,
        hash=full_hash,
        manifest=MappingProxyType(data["manifest"]),
        items=MappingProxyType(items),
        storylines=MappingProxyType({k: storylines[k] for k in sorted(storylines)}),
        rules=MappingProxyType(data["rules"]),
        reference=MappingProxyType(data["reference"]),
        data=MappingProxyType(data),
    )
