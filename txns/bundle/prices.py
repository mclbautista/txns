"""Rate cards (FR-F1 to FR-F5): parse and check one item's `rate_cards.json` entry.

    {
      "points": [                                   the card before any step
        {"unit_price": 195000, "seller": "tape-1",
         "tiers": [{"min_qty": 5, "unit_price": 180000},     volume tiers, stock and
                   {"min_qty": 10, "unit_price": 170000}]}   hardware items only
      ],
      "quantities": [{"qty": 1, "weight": 3}, {"qty": "2.5", "weight": 1}],
      "steps": [                                    optional dated price steps
        {"date": "2026-01-01", "points": [{"unit_price": 205000, "tiers": [...]}]}
      ]
    }

Catalog keys read here: `decimal` (true: quantities may be decimals, written as
JSON strings such as "2.5") and `goods` ("stock" or "hardware": may list tiers).

Rules (a bundle that breaks one exits 4, so `generate` never has to guess):

- every unit price is positive integer centavos with tidy cents (.00, .50, .75);
- price points: subscription 1, big-ticket 1, retail at most 4; a deposit_balance
  item (any class) at most 2: its deposit figure, then its balance figure;
- volume tiers: only on stock or hardware items, at most 2 per point (3 tiers
  counting the base price), `min_qty` ascending from 2, each tier cheaper than
  the one below it. The tier price is looked up, never computed;
- price steps: ascending dates, at most one per calendar year; a step lists the
  same points (sellers carry over by position) with the same tier breaks, and
  for retail and subscription items moves every price up by 3% to 15%;
- quantities: positive integers, or on decimal items positive decimals with at
  most 3 places whose amount at every price is whole centavos.

A step takes effect on its date and the old prices never appear again
(`Item.points_on`, `Item.retired_prices`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from txns.bundle.model import PricePoint, PriceStep, QtyOption, Tier
from txns.errors import BundleInvalid
from txns.money import format_centavos, is_tidy_cents

TIERED_GOODS = ("stock", "hardware")
MAX_TIERS = 2  # tier entries per point besides the base price (FR-F4: up to 3 tiers)
MAX_POINTS = {"subscription": 1, "big_ticket": 1, "retail": 4}  # FR-F1; retail's minimum of 2 is the author gate's
DEPOSIT_BALANCE_POINTS = 2  # any class: point 0 the deposit, the last point the balance (FR-E4)
STEP_MIN_PCT, STEP_MAX_PCT = 3, 15  # FR-F2, retail and subscription steps
STEP_BOUNDED_CLASSES = ("retail", "subscription")
MAX_QTY_PLACES = 3


@dataclass(frozen=True)
class RateCard:
    points: tuple[PricePoint, ...]
    quantities: tuple[QtyOption, ...]
    steps: tuple[PriceStep, ...]
    decimal: bool
    goods: str | None


def _bad(where: str, msg: str) -> BundleInvalid:
    return BundleInvalid(f"bundle invalid: {where}: {msg}")


def _pos_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _price(where: str, value: Any) -> int:
    if not _pos_int(value):
        raise _bad(where, f"unit_price must be a positive integer (centavos), got {value!r}")
    if not is_tidy_cents(value):
        raise _bad(where, f"unit_price {format_centavos(value)} has untidy cents (only .00, .50, .75)")
    return value


def _tiers(where: str, raw: Any, tiered: bool) -> tuple[Tier, ...]:
    if raw in (None, []):
        return ()
    if not tiered:
        raise _bad(where, "volume tiers are allowed only on items whose catalog `goods` is stock or hardware")
    if not isinstance(raw, list) or len(raw) > MAX_TIERS:
        raise _bad(where, f"`tiers` must list at most {MAX_TIERS} {{min_qty, unit_price}} (3 tiers in all)")
    tiers = []
    for t in raw:
        if not isinstance(t, dict) or not _pos_int(t.get("min_qty")) or t["min_qty"] < 2:
            raise _bad(where, "each tier needs an integer `min_qty` of 2 or more")
        tiers.append(Tier(t["min_qty"], _price(where, t.get("unit_price"))))
    return tuple(tiers)


def _point(where: str, raw: Any, tiered: bool, seller: str | None = None) -> PricePoint:
    if not isinstance(raw, dict):
        raise _bad(where, "each price point must be a table with `unit_price`")
    point = PricePoint(_price(where, raw.get("unit_price")), raw.get("seller", seller), _tiers(where, raw.get("tiers"), tiered))
    breaks = [t.min_qty for t in point.tiers]
    if breaks != sorted(set(breaks)):
        raise _bad(where, "tier `min_qty` values must be strictly ascending")
    figures = point.figures
    if any(b >= a for a, b in zip(figures, figures[1:])):
        raise _bad(where, "each volume tier must be cheaper than the one below it")
    return point


def _qty(where: str, raw: Any, decimal: bool) -> int | Decimal:
    if _pos_int(raw):
        return raw
    if not decimal:
        raise _bad(where, f"qty must be a positive integer, got {raw!r} (a decimal qty needs catalog `decimal: true`)")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise _bad(where, f"qty must be a positive number, got {raw!r}")
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        raise _bad(where, f"qty {raw!r} is not a number") from None
    if not value.is_finite() or value <= 0:
        raise _bad(where, f"qty must be positive, got {raw!r}")
    if value == value.to_integral_value():
        return int(value)
    if -value.as_tuple().exponent > MAX_QTY_PLACES:
        raise _bad(where, f"qty {raw!r} has more than {MAX_QTY_PLACES} decimal places")
    return value


def _step(where: str, raw: Any, base: tuple[PricePoint, ...], tiered: bool, price_class: str) -> PriceStep:
    if not isinstance(raw, dict):
        raise _bad(where, "each price step must be a table with `date` and `points`")
    try:
        day = date.fromisoformat(raw.get("date"))
    except (TypeError, ValueError):
        raise _bad(where, f"price step `date` must be YYYY-MM-DD, got {raw.get('date')!r}") from None
    where = f"{where} step {day}"
    raw_points = raw.get("points")
    if not isinstance(raw_points, list) or len(raw_points) != len(base):
        raise _bad(where, f"a price step lists all {len(base)} price point(s), in rate-card order")
    points = []
    for old, new_raw in zip(base, raw_points):
        new = _point(where, new_raw, tiered, old.seller)
        if new.seller != old.seller:
            raise _bad(where, "a price step keeps each point's seller")
        if [t.min_qty for t in new.tiers] != [t.min_qty for t in old.tiers]:
            raise _bad(where, "a price step keeps each point's tier breaks")
        if price_class in STEP_BOUNDED_CLASSES:
            for a, b in zip(old.figures, new.figures):
                if not (100 + STEP_MIN_PCT) * a <= 100 * b <= (100 + STEP_MAX_PCT) * a:
                    raise _bad(
                        where,
                        f"step {format_centavos(a)} to {format_centavos(b)} is outside +{STEP_MIN_PCT}% to +{STEP_MAX_PCT}%",
                    )
        points.append(new)
    return PriceStep(day, tuple(points))


def _weights_ok(raw: Any) -> bool:
    return (
        isinstance(raw, list)
        and bool(raw)
        and all(
            isinstance(q, dict) and isinstance(q.get("weight"), int) and not isinstance(q["weight"], bool) and q["weight"] >= 0
            for q in raw
        )
        and sum(q["weight"] for q in raw) > 0
    )


def parse_card(where: str, entry: dict[str, Any], card: dict[str, Any]) -> RateCard:
    """Check one item's rate card against its catalog entry (`where` names the item in messages)."""
    price_class = entry["class"]
    decimal = entry.get("decimal", False)
    if not isinstance(decimal, bool):
        raise _bad(where, "catalog `decimal` must be true or false")
    goods = entry.get("goods")
    if goods is not None and goods not in TIERED_GOODS:
        raise _bad(where, f"catalog `goods` must be one of {', '.join(TIERED_GOODS)}")
    tiered = goods in TIERED_GOODS

    raw_points = card.get("points") or []
    if not isinstance(raw_points, list) or not raw_points:
        raise _bad(where, "rate card needs price points with positive integer `unit_price` (centavos)")
    most = MAX_POINTS.get(price_class, len(raw_points))
    if entry.get("archetype") == "deposit_balance":
        most = DEPOSIT_BALANCE_POINTS  # the deposit figure and the balance figure (txns.bundle.events)
    if len(raw_points) > most:
        raise _bad(where, f"a {price_class} item has at most {most} price point(s), got {len(raw_points)}")
    points = tuple(_point(where, p, tiered) for p in raw_points)

    raw_steps = card.get("steps") or []
    if not isinstance(raw_steps, list):
        raise _bad(where, "`steps` must be a list of {date, points}")
    steps = tuple(_step(where, s, points, tiered, price_class) for s in raw_steps)
    days = [s.date for s in steps]
    if days != sorted(set(days)):
        raise _bad(where, "price step dates must be strictly ascending")
    if len({d.year for d in days}) != len(days):
        raise _bad(where, "at most one price step per item per year")

    raw_qtys = card.get("quantities")
    if not _weights_ok(raw_qtys):
        raise _bad(where, "rate card needs an allowed quantity set with integer weights")
    quantities = tuple(QtyOption(_qty(where, q.get("qty"), decimal), q["weight"]) for q in raw_qtys)
    for q in quantities:
        if not isinstance(q.qty, Decimal):
            continue
        for version in (points,) + tuple(s.points for s in steps):
            for p in version:
                amount = q.qty * p.price_for(q.qty)
                if amount != amount.to_integral_value():
                    raise _bad(where, f"qty {q.qty} at {format_centavos(p.price_for(q.qty))} is not a whole-centavo amount")
    return RateCard(points, quantities, steps, decimal, goods)
