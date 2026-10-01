"""Rate cards from anchors only (FR-C3, FR-D1, FR-D2 gate 2, FR-F1 to FR-F6).

The LLM never sets a price or a pack size. Every unit price on a card comes from
an anchor, in this order:

1. Committed anchors (`inputs/price-anchors.json`, `txns.assembly.anchors`): an
   item one of whose keywords names it ("LTO-8") takes the entry's prices, pack
   size and quantities. An entry with no price leaves the item out (reported).
2. Ledger anchors of the item's category (`category_figures`: reference.json
   `anchor_prices`, unit prices seen on 2+ rows; a category with none falls back
   to its observed amount percentiles). An item whose text shares words with
   ledger item texts of its category takes the anchor figures of those rows
   (matched locally on this machine; no ledger text reaches the bundle). The
   category's other figures are split, cheapest first, between its items that
   matched no row (the invented items of text-less rows).
3. No figure at all: the item is left out and reported, so a price can be supplied.

From an item's figures (price -> rows seen):

- points: subscription and big-ticket 1 (the most seen figure); deposit_balance
  the cheapest and dearest figure (deposit, balance); retail and party items one
  point per seller, 2 to 4: the most seen figure, other figures within
  `seller_match_pct`, then the base moved by `seller_spread_pct` ("a few percent
  across sellers"). A retail item drafted with one seller gets an unnamed second.
  An event item (deposit_balance, party item) whose most seen figure is a whole
  ₱1,000 takes round figures only (prize tiers, negotiated deposits) and is
  approved for them (`round_figures`).
- every price is tidied to .00/.50/.75 (FR-F5). Items not approved for round
  figures never get a whole-₱1,000 price (a plug-row tell, FR-F6).
- volume tiers (`pricing.tiers`) on stock and hardware items whose quantities
  reach the break; price steps (`pricing.steps`) once a year from the year after
  the ledgers to the last year of the bundle's holiday calendar, +3% to +15%
  tidied, for the classes `pricing.steps.pct` lists (FR-F2, FR-F4).
- quantities from `pricing.quantities` (by archetype, class, goods, then
  retail), plus pack sizes the item's ledger rows show; a stock item's pack size
  (`pack_pcs`) from its rows or the category's `pack_sizes`, its price per pack.
  Decimal quantities need a per-measure anchor, which no source gives yet, so a
  draft's `decimal` is dropped (reported).

`check_anchors(bundle, tolerance_pct)` is FR-D2 gate 2 on a built bundle: the base
card's prices and tiers within tolerance of an anchor, pack sizes and quantities
from the anchors or the committed defaults.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from txns.assembly.anchors import Anchor, find as find_anchor
from txns.bundle import events
from txns.money import format_centavos, is_round_thousand, is_tidy_cents, is_whole_peso

TIDY = (0, 50, 75)
STEP_MIN_PCT, STEP_MAX_PCT = 3, 15
SELLER_CLASSES = ("retail",)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and at by for from in into of on or per the to with x misc pcs pc pieces piece units unit "
    "pack packs box boxes set sets pk ct count".split()
)


# --------------------------------------------------------------------------- figures


def category_figures(reference: Mapping[str, Any], category: str) -> dict[int, int]:
    """The ledger's anchor figures for a category: price (centavos) -> rows seen.

    reference.json `anchor_prices` (unit prices on 2+ rows); a category with none
    falls back to its observed row-amount percentiles (each counted once)."""
    anchors = (reference.get("anchor_prices") or {}).get(category) or []
    figs = {int(a["unit_price"]): int(a.get("rows", 1)) for a in anchors if isinstance(a, dict)}
    if figs:
        return figs
    pct = (reference.get("category_amount_percentiles") or {}).get(category) or {}
    return {int(v): 1 for v in pct.values() if isinstance(v, int) and v > 0}


def category_pack_sizes(reference: Mapping[str, Any], category: str) -> Counter:
    return Counter({int(p["qty"]): int(p.get("rows", 1)) for p in (reference.get("pack_sizes") or {}).get(category) or []})


def tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.casefold()) if len(t) >= 2 and not t.isdigit() and t not in _STOP}


# --------------------------------------------------------------------------- tidy figures


def tidy(x: float, *, avoid_round: bool = False, whole: bool = False) -> int:
    """The nearest price with tidy cents (.00/.50/.75; whole pesos only when `whole`),
    never a whole ₱1,000 when `avoid_round`."""
    cents = (0,) if whole else TIDY
    base = int(x // 100) * 100
    cands = [b + c for b in (base - 100, base, base + 100) for c in cents] + [base + 200]
    cands = [c for c in cands if c > 0 and not (avoid_round and is_round_thousand(c))]
    return min(cands, key=lambda c: (abs(c - x), c))


def tidy_between(lo: float, hi: float, target: float, *, avoid_round: bool, whole: bool = False) -> int | None:
    """The tidy price nearest `target` within [lo, hi] (whole pesos first when `whole`), or None."""
    for cents in ((0,), TIDY) if whole else (TIDY,):
        nearest = tidy(target, avoid_round=avoid_round, whole=cents == (0,))
        if lo <= nearest <= hi:
            return nearest
        cands = [p * 100 + c for p in range(int(lo // 100), int(hi // 100) + 1) for c in cents]
        cands = [c for c in cands if lo <= c <= hi and not (avoid_round and is_round_thousand(c))]
        if cands:
            return min(cands, key=lambda c: (abs(c - target), c))
    return None


def derived(x: float, source: int, *, avoid_round: bool) -> int:
    """A price derived from anchor `source` (seller spread, tier): whole pesos when the source is."""
    return tidy(x, avoid_round=avoid_round, whole=is_whole_peso(source))


# --------------------------------------------------------------------------- results


@dataclass
class Priced:
    card: dict[str, Any]  # rate_cards.json entry
    catalog: dict[str, Any]  # catalog fields set by pricing (goods, pack_pcs, round_figures)
    sellers: list[dict[str, Any]]  # the sellers with a point, in point order ({id, vendor})
    record: dict[str, Any]  # anchors.json entry: where the prices come from
    notes: list[str] = field(default_factory=list)


@dataclass
class Evidence:
    figures: Counter  # price per rate-card unit -> rows
    basis: str  # "item text" | "category" | "committed"
    pack_pcs: int | None = None
    extra_qty: set[int] = field(default_factory=set)
    anchor: Anchor | None = None


def _item_texts(item_id: str, variants: Mapping[str, Any] | None) -> list[str]:
    texts = [item_id.replace("_", " ")]
    if variants:
        texts += list(variants.get("descriptive", [])) + list(variants.get("terse", []))
        texts += [v["text"].partition(" - ")[2] for v in variants.get("vendor", [])]
    return [t.replace("{pcs}", " ") for t in texts]


def _match_rows(item_tokens: dict[str, set[str]], rows: Sequence) -> dict[str, list]:
    """Each ledger row of a category -> the item sharing the most distinctive words with its text."""
    n = len(item_tokens)
    df = Counter(t for toks in item_tokens.values() for t in toks)
    weight = {t: (0.0 if n > 1 and c == n else 1.0 / c) for t, c in df.items()}
    out: dict[str, list] = defaultdict(list)
    for row in rows:
        words = tokens(row.item_text)
        best, best_score = None, 0.0
        for item_id in sorted(item_tokens):
            score = sum(weight[t] for t in words & item_tokens[item_id])
            if score > best_score:
                best, best_score = item_id, score
        if best is not None:
            out[best].append(row)
    return out


def _ledger_evidence(rows: list, cat_figs: Mapping[int, int], stock: bool) -> tuple[Evidence | None, set[int]]:
    """Figures from an item's matched ledger rows (anchor figures only) and the figures it claims."""
    figs: Counter = Counter()
    claimed: set[int] = set()
    packs: Counter = Counter()
    extra: set[int] = set()
    pack_figs: list[tuple[int, int]] = []  # (pcs, pack price)
    for r in rows:
        hit = r.unit_price if r.unit_price in cat_figs else (r.amount if r.amount in cat_figs else None)
        if hit is None:
            continue
        claimed.add(hit)
        per_piece = hit == r.unit_price and r.qty > 1
        if stock and r.qty > 1:
            packs[r.qty] += 1
            pack_figs.append((r.qty, r.unit_price * r.qty if per_piece else r.amount))
        else:
            figs[hit] += 1
            if per_piece:
                extra.add(r.qty)
    pcs = None
    if packs:
        pcs = sorted(packs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        for n, price in pack_figs:
            if n == pcs:
                figs[price] += 1
    if not figs:
        return None, set()
    return Evidence(figs, "item text", pack_pcs=pcs, extra_qty=extra), claimed


def _chunks(values: list[int], k: int) -> list[list[int]]:
    """Split ascending values into k contiguous, non-empty runs (shared when values are fewer)."""
    n = len(values)
    out = []
    for j in range(k):
        a = j * n // k
        b = max((j + 1) * n // k, a + 1)
        out.append(values[a:b])
    return out


def gather_evidence(
    items: Mapping[str, Mapping[str, Any]],
    variants: Mapping[str, Mapping[str, Any]],
    rows: Iterable,
    reference: Mapping[str, Any],
    anchors: Sequence[Anchor],
) -> tuple[dict[str, Evidence], dict[str, str]]:
    """Each drafted item's anchor figures (steps 1-3 of the module docstring), or why it has none."""
    evidence: dict[str, Evidence] = {}
    missing: dict[str, str] = {}
    by_cat: dict[str, list[str]] = defaultdict(list)
    for item_id in sorted(items):
        by_cat[items[item_id]["category"]].append(item_id)
    rows_by_cat: dict[str, list] = defaultdict(list)
    for r in rows:
        if r.item_text:
            rows_by_cat[r.category].append(r)

    for cat, ids in sorted(by_cat.items()):
        ledger_ids = []
        for item_id in ids:
            anchor = find_anchor(anchors, _item_texts(item_id, variants.get(item_id)))
            if anchor is None:
                ledger_ids.append(item_id)
            elif not anchor.unit_prices:
                missing[item_id] = f"named by committed anchor `{anchor.key}`, which has no sourced price"
            else:
                figs = Counter({p: 1 for p in anchor.unit_prices})
                evidence[item_id] = Evidence(figs, "committed", pack_pcs=anchor.pack_pcs, anchor=anchor)
        if not ledger_ids:
            continue
        cat_figs = category_figures(reference, cat)
        if not cat_figs:
            for item_id in ledger_ids:
                missing[item_id] = f"no ledger anchor price for category {cat!r} and no committed anchor"
            continue
        packs = category_pack_sizes(reference, cat)
        cat_pcs = sorted(packs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if packs else None
        item_tokens = {i: set().union(*(tokens(t) for t in _item_texts(i, variants.get(i)))) for i in ledger_ids}
        matched = _match_rows(item_tokens, rows_by_cat.get(cat, []))
        claimed: set[int] = set()
        unmatched = []
        for item_id in ledger_ids:
            stock = items[item_id].get("goods") == "stock"
            ev, took = _ledger_evidence(matched.get(item_id, []), cat_figs, stock)
            if ev is None:
                unmatched.append(item_id)
                continue
            if stock and ev.pack_pcs is None:
                ev.pack_pcs = cat_pcs
            evidence[item_id] = ev
            claimed |= took
        if unmatched:
            pool = sorted(p for p in cat_figs if p not in claimed) or sorted(cat_figs)
            for item_id, chunk in zip(unmatched, _chunks(pool, len(unmatched))):
                stock = items[item_id].get("goods") == "stock"
                evidence[item_id] = Evidence(
                    Counter({p: cat_figs[p] for p in chunk}), "category", pack_pcs=cat_pcs if stock else None
                )
    return evidence, missing


# --------------------------------------------------------------------------- cards


def _base(figs: Counter) -> int:
    return sorted(figs.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _seller_prices(figs: Counter, n: int, rules: Mapping[str, Any], avoid_round: bool) -> list[int]:
    base = _base(figs)
    match = float(rules.get("seller_match_pct", 20)) / 100
    near = sorted(
        (p for p in figs if p != base and abs(p / base - 1) <= match),
        key=lambda p: (abs(p / base - 1), p),
    )
    prices = [base] + near[: n - 1]
    for pct in rules.get("seller_spread_pct", [3, 6, 9]):
        if len(prices) >= n:
            break
        p = derived(base * (100 + pct) / 100, base, avoid_round=avoid_round)
        if p not in prices:
            prices.append(p)
    return prices[:n]


def _choose(figs: Counter, mode: str, n: int, rules, avoid_round: bool) -> list[int]:
    if mode == "deposit":
        ordered = sorted(figs)
        return [ordered[0], ordered[-1]] if len(ordered) > 1 else [ordered[0]]
    if mode == "sellers":
        return _seller_prices(figs, n, rules, avoid_round)
    return [_base(figs)]


def _quantities(draft: Mapping[str, Any], goods: str | None, ev: Evidence, rules) -> list[dict]:
    if ev.anchor is not None and ev.anchor.quantities:
        return [dict(q) for q in ev.anchor.quantities]
    table = rules.get("quantities") or {}
    for key in (draft["archetype"], draft["class"] if draft["class"] != "retail" else None, goods, "retail"):
        if key and table.get(key):
            qtys = [dict(q) for q in table[key]]
            break
    else:
        qtys = [{"qty": 1, "weight": 1}]
    have = {q["qty"] for q in qtys}
    for extra in sorted(ev.extra_qty - have):
        qtys.append({"qty": extra, "weight": 1})
    return sorted(qtys, key=lambda q: q["qty"])


def _tiers(price: int, max_qty: int, rules, avoid_round: bool) -> list[dict]:
    out, last = [], price
    for t in rules.get("tiers") or []:
        if t["min_qty"] < 2 or t["min_qty"] > max_qty:
            continue
        p = derived(price * (100 - t["off_pct"]) / 100, price, avoid_round=avoid_round)
        if p < last:
            out.append({"min_qty": t["min_qty"], "unit_price": p})
            last = p
    return out


def _stepped(point: dict, pct: float, avoid_round: bool) -> dict | None:
    def up(p: int) -> int | None:
        return tidy_between(p * (100 + STEP_MIN_PCT) / 100, p * (100 + STEP_MAX_PCT) / 100,
                            p * (100 + pct) / 100, avoid_round=avoid_round, whole=is_whole_peso(p))

    price = up(point["unit_price"])
    if price is None:
        return None
    new = {"unit_price": price}
    if point.get("tiers"):
        tiers, last = [], price
        for t in point["tiers"]:
            p = up(t["unit_price"])
            if p is None or p >= last:
                return None
            tiers.append({"min_qty": t["min_qty"], "unit_price": p})
            last = p
        new["tiers"] = tiers
    return new


def _steps(points: list[dict], pct: float, years: Sequence[int], month_day: str, avoid_round: bool) -> list[dict]:
    out, current = [], points
    for year in years:
        stepped = [_stepped(p, pct, avoid_round) for p in current]
        if any(s is None for s in stepped):
            return []  # a figure too small to step tidily: the item keeps its card
        out.append({"date": f"{year}-{month_day}", "points": stepped})
        current = stepped
    return out


def price_item(
    item_id: str,
    draft: Mapping[str, Any],
    ev: Evidence,
    *,
    rules: Mapping[str, Any],
    storylines: Mapping[str, Mapping[str, Any]],
    step_years: Sequence[int],
) -> Priced | str:
    """The item's rate card and catalog price fields, or the reason it cannot have one."""
    price_class, archetype = draft["class"], draft["archetype"]
    party = archetype == events.PARTY_ARCHETYPE and events.is_party_storyline(storylines, draft["storyline"])
    goods = draft.get("goods")
    notes = []
    if ev.anchor is not None and ev.anchor.pack_pcs and goods != "stock":
        goods = "stock"  # the committed anchor prices a pack
    pack_pcs = ev.pack_pcs if goods == "stock" else None
    if draft.get("decimal"):
        notes.append("decimal quantities dropped: no per-measure anchor")

    sellers = [dict(s) for s in draft["sellers"]]
    if archetype == events.DEPOSIT_BALANCE:
        mode, n = "deposit", 2
    elif price_class in SELLER_CLASSES or party:
        mode, n = "sellers", min(4, max(2, len(sellers)))
    else:
        mode, n = "single", 1
    while len(sellers) < n:
        sellers.append({"id": f"{item_id.replace('_', '-')}-shop{len(sellers) + 1}", "vendor": None})

    approvable = archetype == events.DEPOSIT_BALANCE or party
    round_ok = price_class == "big_ticket"
    figs: Counter = Counter()
    for p, r in ev.figures.items():
        figs[p if is_tidy_cents(p) else tidy(p)] += r  # FR-F5: tidy cents only
    prices: list[int] = []
    if round_ok:
        prices = _choose(figs, mode, n, rules, avoid_round=False)
    else:
        rounds = Counter({p: r for p, r in figs.items() if is_round_thousand(p)})
        if approvable and rounds and is_round_thousand(_base(figs)):
            # An event item at negotiated or prize-tier figures (₱5,000 / ₱3,000 / ₱2,000):
            # round figures only, so the item can be approved for them (txns.bundle.events).
            if mode == "sellers":
                picked = [p for p, _ in sorted(rounds.items(), key=lambda kv: (-kv[1], kv[0]))][:n]
            else:
                picked = _choose(rounds, mode, n, rules, avoid_round=False)
            if len(picked) >= (2 if mode == "sellers" else 1):
                prices, round_ok = picked, True
        if not round_ok:
            figs = Counter({p: r for p, r in figs.items() if not is_round_thousand(p)})
            if not figs:
                return "every anchor figure is a whole ₱1,000, a plug-row tell on an item not approved for round figures"
            prices = _choose(figs, mode, n, rules, avoid_round=True)

    catalog: dict[str, Any] = {}
    if goods is not None:
        catalog["goods"] = goods
    if pack_pcs:
        catalog["pack_pcs"] = pack_pcs
    if round_ok and price_class != "big_ticket":
        catalog["round_figures"] = True

    quantities = _quantities(draft, goods, ev, rules)
    max_qty = max(q["qty"] for q in quantities)
    if mode == "deposit":
        point_sellers = [sellers[0]["id"]] * len(prices)
        used = [sellers[0]]
    else:
        point_sellers = [s["id"] for s in sellers[: len(prices)]]
        used = sellers[: len(prices)]
    points = []
    for price, seller in zip(prices, point_sellers):
        point: dict[str, Any] = {"unit_price": price, "seller": seller}
        if goods in ("stock", "hardware") and not round_ok:
            tiers = _tiers(price, max_qty, rules, avoid_round=True)
            if tiers:
                point["tiers"] = tiers
        points.append(point)

    card: dict[str, Any] = {"points": points, "quantities": quantities}
    step_rules = rules.get("steps") or {}
    pct = (step_rules.get("pct") or {}).get(price_class)
    if pct and step_years and not round_ok:
        steps = _steps([{k: v for k, v in p.items() if k != "seller"} for p in points], float(pct), step_years,
                       str(step_rules.get("month_day", "01-01")), avoid_round=True)
        if steps:
            card["steps"] = steps

    record: dict[str, Any] = {"basis": ev.basis}
    if ev.anchor is not None:
        record = {"basis": "committed", **ev.anchor.record()}
    else:
        record["figures"] = sorted(ev.figures)
    return Priced(card=card, catalog=catalog, sellers=used, record=record, notes=notes)


# --------------------------------------------------------------------------- gate 2


def _near(price: float, anchor: int, tol: float) -> bool:
    return abs(price - anchor) <= anchor * tol


def check_anchors(bundle, tolerance_pct: float) -> list[str]:
    """FR-D2 gate 2: prices and pack sizes within tolerance of their anchors ([] = pass).

    Checks the base card (points and their tiers); price steps are bounded by the
    rate-card rules (+3% to +15% a year). A committed anchor is read from the
    bundle's `anchors.json` record, so the check needs nothing outside the bundle."""
    tol = float(tolerance_pct) / 100
    records = ((bundle.data.get("anchors") or {}).get("items")) or {}
    defaults = {q["qty"] for qs in ((bundle.rules.get("pricing") or {}).get("quantities") or {}).values() for q in qs}
    out = []
    for item in bundle.items.values():
        where = f"item `{item.id}`"
        rec = records.get(item.id) or {}
        pcs = item.raw.get("pack_pcs")
        if rec.get("basis") == "committed":
            allowed = [int(p) for p in rec.get("unit_prices", [])]
            packs = {rec["pack_pcs"]} if rec.get("pack_pcs") else set()
            qtys = {q["qty"] for q in rec.get("quantities", [])} or defaults
            source = f"committed anchor `{rec.get('key')}`"
        else:
            allowed = sorted(category_figures(bundle.reference, item.category))
            packs = set(category_pack_sizes(bundle.reference, item.category))
            qtys = defaults | packs
            source = f"reference.json anchors of category {item.category!r}"
        if not allowed:
            out.append(f"{where}: no anchor price in {source}")
            continue
        for point in item.price_points:
            for price in point.figures:
                per_piece = price / pcs if isinstance(pcs, int) and pcs > 1 else None
                if not any(_near(price, a, tol) or (per_piece is not None and _near(per_piece, a, tol)) for a in allowed):
                    nearest = min(allowed, key=lambda a: abs(price - a))
                    out.append(f"{where}: price {format_centavos(price)} is more than {tolerance_pct}% from "
                               f"every anchor in {source} (nearest {format_centavos(nearest)})")
        if pcs is not None and pcs not in packs:
            out.append(f"{where}: pack size {pcs} is not an observed or committed pack size ({source})")
        for q in item.quantities:
            if isinstance(q.qty, int) and q.qty not in qtys:
                out.append(f"{where}: quantity {q.qty} is neither a committed default nor an observed pack size")
    return out


def step_years(ledgers_end: date, calendar_years: Iterable[int]) -> list[int]:
    """Years a price step falls on: after the last ledger year, within the bundle's calendar."""
    return [y for y in sorted(calendar_years) if y > ledgers_end.year]


__all__ = [
    "Evidence",
    "Priced",
    "category_figures",
    "category_pack_sizes",
    "check_anchors",
    "gather_evidence",
    "price_item",
    "step_years",
    "tidy",
    "tidy_between",
]

