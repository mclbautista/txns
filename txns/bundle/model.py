"""Typed, read-only view of a loaded bundle.

Bundle files (all JSON, top level of the bundle folder):

- manifest.json   label, hash, promotion, reviewed, interpreter, generator,
                  model, served_models, ledger_hashes, format
- catalog.json    {"items": {item_id: {storyline, category, class, archetype, params}}}
- rate_cards.json {item_id: {"points": [{unit_price (int centavos), seller, tiers?}],
                             "quantities": [{qty (int), weight (int)}],
                             "steps"?: [{date, points}]}}
                  (volume tiers, price steps, decimal quantities: see
                  `txns.bundle.prices`, which parses and checks rate cards)
- text.json       {item_id: {"descriptive": [...], "terse": [...],
                             "vendor": [{"seller": seller id, "text": "Vendor - item"}]}}
                  `vendor` (optional) holds the vendor-prefixed descriptive variants;
                  each is used only on rows whose price point that seller sells (FR-H1).
                  Gates on this text: `txns.bundle.text_rules`.
- storylines.json {name: {description, month_weights?, burst_days?, bursts_per_quarter?,
                          quiet_days?}} (FR-E10, see `txns.bundle.storylines`)
- rules.json      {"archetypes": {name: {...}}, "tier_factors": {...},
                   "calendar": {...} (day shape, see txns.engine.calendar)}
- holidays.json   Philippine holiday calendar for the bundle's years (txns.holidays)
- reference.json  ledger statistics (filled by later tickets)

Any other *.json file is loaded into `Bundle.data[<stem>]` untouched, so a new
file can be added to the bundle without changing this loader. To add a field
to an item, read it from `Item.params` (archetype parameters) or `Item.raw`
(the item's catalog entry), or add a typed field here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from txns import holidays
from txns.errors import BundleInvalid

REQUIRED_FILES = (
    "manifest",
    "catalog",
    "rate_cards",
    "text",
    "storylines",
    "rules",
    "reference",
    "holidays",
)
PRICE_CLASSES = ("subscription", "retail", "big_ticket")


@dataclass(frozen=True)
class Tier:
    """A volume tier (FR-F4): from `min_qty` up, the point's unit price is `unit_price`."""

    min_qty: int
    unit_price: int  # centavos


@dataclass(frozen=True)
class PricePoint:
    unit_price: int  # centavos; the price below the first tier
    seller: str | None = None
    tiers: tuple[Tier, ...] = ()  # ascending min_qty, descending price; at most 2 (3 tiers in all)

    def price_for(self, qty: int | Decimal) -> int:
        """The fixed unit price for `qty` (volume tiers are looked up, never computed)."""
        price = self.unit_price
        for tier in self.tiers:
            if qty >= tier.min_qty:
                price = tier.unit_price
        return price

    @property
    def figures(self) -> tuple[int, ...]:
        """Every unit price this point can show: the base price and its tier prices."""
        return (self.unit_price,) + tuple(t.unit_price for t in self.tiers)


@dataclass(frozen=True)
class PriceStep:
    """A dated price step (FR-F2): from `date` on, the item's points are `points` (same sellers, same order)."""

    date: date
    points: tuple[PricePoint, ...]


@dataclass(frozen=True)
class VendorVariant:
    """A vendor-prefixed descriptive variant ("Vendor - item") and the seller id it names."""

    seller: str
    text: str


@dataclass(frozen=True)
class QtyOption:
    qty: int | Decimal  # Decimal only on items marked `decimal`
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
    vendor: tuple[VendorVariant, ...] = ()  # vendor-prefixed descriptive variants
    decimal: bool = False  # catalog `decimal`: qty may be a decimal (FR-F3)
    goods: str | None = None  # catalog `goods`: "stock" | "hardware" (volume tiers allowed, FR-F4)
    steps: tuple[PriceStep, ...] = ()  # dated price steps, ascending, at most one per year (FR-F2)

    @property
    def variants(self) -> tuple[str, ...]:
        """Every text string of the item: descriptive, vendor-prefixed, terse."""
        return self.descriptive + tuple(v.text for v in self.vendor) + self.terse

    def points_on(self, day: date | None) -> tuple[PricePoint, ...]:
        """The rate card valid on `day`: the last step dated on or before it, else the base points.

        `price_points` is the base card; `Row.price_point` indexes either (sellers keep their index).
        """
        points = self.price_points
        for step in self.steps:
            if day is not None and step.date <= day:
                points = step.points
        return points

    def prices_between(self, start: date, end: date) -> set[int]:
        """Every unit price (points and tiers) the item may show on some day in [start, end]."""
        versions = [self.points_on(start)] + [s.points for s in self.steps if start < s.date <= end]
        return {price for points in versions for p in points for price in p.figures}

    def retired_prices(self, day: date) -> set[int]:
        """Prices replaced by a step on or before `day` that the card valid on `day` no longer lists."""
        taken = [s for s in self.steps if s.date <= day]
        if not taken:
            return set()
        older = (self.price_points,) + tuple(s.points for s in taken[:-1])
        current = {price for p in taken[-1].points for price in p.figures}
        return {price for points in older for p in points for price in p.figures} - current

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
    calendar: holidays.HolidayCalendar = field(default_factory=holidays.HolidayCalendar.empty)

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


def build(bundle_id: str, folder: Path, full_hash: str, data: dict[str, Any]) -> Bundle:
    """Structural checks that `generate` relies on; failures exit 4."""
    from txns.bundle import prices  # rate-card rules; imports this module
    from txns.bundle import storylines as storyline_rules

    storylines = data["storylines"]
    if not isinstance(storylines, dict) or not storylines:
        raise _bad("storylines.json must map at least one storyline name to its settings")
    storyline_rules.check(storylines, data["rules"])
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
        rate_card = prices.parse_card(where, entry, card)
        variants = text.get(item_id)
        if not isinstance(variants, dict):
            raise _bad(f"{where}: no text variants")
        descriptive = tuple(variants.get("descriptive") or ())
        terse = tuple(variants.get("terse") or ())
        vendor_raw = variants.get("vendor") or []
        if not isinstance(vendor_raw, list) or not all(
            isinstance(v, dict) and isinstance(v.get("seller"), str) and isinstance(v.get("text"), str)
            for v in vendor_raw
        ):
            raise _bad(f"{where}: vendor variants must be a list of {{seller, text}} tables")
        vendor = tuple(VendorVariant(v["seller"], v["text"]) for v in vendor_raw)
        if not descriptive and not terse:
            raise _bad(f"{where}: no text variants")
        items[item_id] = Item(
            id=item_id,
            storyline=entry["storyline"],
            category=entry["category"],
            price_class=entry["class"],
            archetype=entry["archetype"],
            params=MappingProxyType(dict(entry.get("params") or {})),
            price_points=rate_card.points,
            quantities=rate_card.quantities,
            descriptive=descriptive,
            terse=terse,
            raw=MappingProxyType(entry),
            vendor=vendor,
            decimal=rate_card.decimal,
            goods=rate_card.goods,
            steps=rate_card.steps,
        )
    try:
        calendar = holidays.parse(data["holidays"])
    except holidays.CalendarInvalid as exc:
        raise _bad(str(exc)) from None
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
        calendar=calendar,
    )
