"""Human messiness and entry order (FR-H3, FR-H4, FR-F5), the last engine stage.

Runs after calibration and text, and never changes the run's row count; the
total moves only by the centavos a derived per-unit price rounds off (never
down). In order:

1. Duplicates (FR-H3): same-day, same-item, same-amount groups at the ledger's
   duplicate-group rate (groups / rows). Groups the plan already has count
   toward it; batch rows never form one (the scorecard's `duplicates` check
   counts a batch day's same-price rows as the batch). A group is made from two
   rows of one item with the same price point and qty: the second is re-dated
   onto the first one's day and tagged "duplicate" (the batch entry of one
   receipt twice), so the total and the row count stay as calibrated. It keeps
   its own text or, on `duplicate_same_text` of groups, copies the first row's.
   Only untagged rows of non-subscription items take part (tagged rows belong
   to a structure; subscriptions keep their anchor day). Per-item targets are
   the rate scaled up by rows that cannot take part, rounded stochastically.
2. Derived per-unit prices (FR-F5): on about `per_unit_share` of a stock item's
   rows, among those whose chosen text states its pack size (`bundle.packs`),
   qty becomes pieces and unit_price the pack price over pieces. Tagged "per_unit".
3. Date tails (FR-H3): about `date_tail_share` of batch-logged rows get their
   original date appended in a format from the bundle vocabulary
   (`bundle.vocabulary`), when the text stays within the FR-H2 rules.
4. Entry order (FR-H4): rows are ordered by the day they were entered, in a
   random order within the day. Batch rows of one item are entered together (in
   the order they happened) and a duplicate right after its twin. About
   `backdate_share` of untagged rows are entered 1 to `backdate_max_days` days
   late, which shows as a small back-dated row. If that leaves the file
   perfectly date-sorted, one row is entered a day late anyway (never sorted).

rules.json `messiness` table (all optional, code defaults below):

    duplicate_group_rate  override of reference.json `duplicate_group_rate`
                          (leave it out to follow the ledger)
    duplicate_same_text   share of duplicates that copy their twin's text, 0.7
    duplicate_max_days    how far a row may be re-dated to make a duplicate, 31
    per_unit_share        0.05     date_tail_share   0.05
    backdate_share        0.02     backdate_max_days 3

Every draw is on the row's item streams (`messiness.*`, `ordering`), so another
item or storyline never changes an item's rows or their relative order (T5).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import timedelta
from typing import Any, Mapping

from txns.bundle import packs, vocabulary
from txns.engine.context import EngineContext
from txns.engine.rows import Row
from txns.money import is_round_thousand

DEFAULT_DUPLICATE_GROUP_RATE = 0.02  # the ledgers: 58 groups in about 2,870 spend rows
DEFAULTS: dict[str, float] = {
    "duplicate_same_text": 0.7,
    "duplicate_max_days": 31,
    "per_unit_share": 0.05,
    "date_tail_share": 0.05,
    "backdate_share": 0.02,
    "backdate_max_days": 3,
}
DUPLICATE, PER_UNIT, BATCH = "duplicate", "per_unit", "batch"


def rules(bundle) -> Mapping[str, Any]:
    return bundle.rules.get("messiness", {}) or {}


def setting(bundle, key: str) -> float:
    value = rules(bundle).get(key)
    return float(DEFAULTS[key] if value is None else value)


def duplicate_group_rate(bundle) -> float:
    """Target duplicate groups per row: rules override, else the ledger's, else the default."""
    value = rules(bundle).get("duplicate_group_rate")
    if value is None:
        value = bundle.reference.get("duplicate_group_rate")
    return float(DEFAULT_DUPLICATE_GROUP_RATE if value is None else value)


def _stochastic_round(x: float, stream) -> int:
    whole = math.floor(x)
    return whole + (1 if stream.chance(x - whole) else 0)


def _by_item(rows: list[Row]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        if r.item_id is not None:
            out[r.item_id].append(i)
    return {k: out[k] for k in sorted(out)}


def _groups(rows: list[Row], idx: list[int]) -> Counter:
    """(date, amount) -> rows of one item."""
    return Counter((rows[i].date, rows[i].amount) for i in idx)


def apply(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    rows = list(rows)
    rows = duplicates(ctx, rows)
    rows = per_unit(ctx, rows)
    rows = date_tails(ctx, rows)
    return entry_order(ctx, rows)


# ---- 1. duplicates ----------------------------------------------------------------


def _can_duplicate(item, row: Row) -> bool:
    return not row.tags and item.price_class != "subscription"


def duplicates(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    rate = duplicate_group_rate(ctx.bundle)
    if rate <= 0 or not rows:
        return rows
    by_item = _by_item(rows)
    natural = {
        k: sum(1 for c in _groups(rows, [i for i in idx if BATCH not in rows[i].tags]).values() if c > 1)
        for k, idx in by_item.items()
    }
    eligible = {
        k: [i for i in idx if _can_duplicate(ctx.bundle.items[k], rows[i])] for k, idx in by_item.items()
    }
    n_eligible = sum(len(v) for v in eligible.values())
    if not n_eligible:
        return rows
    elsewhere = sum(natural[k] for k, v in eligible.items() if not v)
    item_rate = max(0.0, rate * len(rows) - elsewhere) / n_eligible
    same_text = setting(ctx.bundle, "duplicate_same_text")
    max_days = int(setting(ctx.bundle, "duplicate_max_days"))
    for item_id, idx in eligible.items():
        if not idx:
            continue
        stream = ctx.item_stream(item_id, "messiness.duplicates")
        want = _stochastic_round(item_rate * len(idx), stream) - natural[item_id]
        if want <= 0:
            continue
        groups = _groups(rows, by_item[item_id])
        free = [i for i in idx if groups[(rows[i].date, rows[i].amount)] == 1]
        busy_days = Counter(rows[i].date for i in by_item[item_id])
        order = list(free)
        stream.shuffle(order)
        used: set[int] = set()
        made = 0
        for a in order:
            if made >= want:
                break
            if a in used:
                continue
            first = rows[a]
            partners = [
                b
                for b in free
                if b != a
                and b not in used
                and rows[b].date != first.date
                and abs((rows[b].date - first.date).days) <= max_days
                and (rows[b].price_point, rows[b].qty, rows[b].unit_price)
                == (first.price_point, first.qty, first.unit_price)
            ]
            if not partners or busy_days[first.date] > 1:
                continue
            b = min(partners, key=lambda j: (abs((rows[j].date - first.date).days), rows[j].date, j))
            text = first.text if stream.chance(same_text) else rows[b].text
            busy_days[rows[b].date] -= 1
            busy_days[first.date] += 1
            rows[b] = replace(rows[b], date=first.date, text=text, tags=rows[b].tags + (DUPLICATE,))
            used.update((a, b))
            made += 1
    return rows


# ---- 2. derived per-unit prices -------------------------------------------------------


def per_unit(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    share = setting(ctx.bundle, "per_unit_share")
    if share <= 0:
        return rows
    for item_id, idx in _by_item(rows).items():
        item = ctx.bundle.items[item_id]
        pcs = packs.pack_pcs(item)
        if pcs is None:
            continue
        stream = ctx.item_stream(item_id, "messiness.per_unit")
        groups = _groups(rows, idx)
        eligible = [
            i
            for i in idx
            if DUPLICATE not in rows[i].tags
            and groups[(rows[i].date, rows[i].amount)] == 1
            and isinstance(rows[i].qty, int)
            and packs.states_pack(rows[i].text, pcs)
        ]
        k = min(len(eligible), _stochastic_round(share * len(idx), stream))
        stream.shuffle(eligible)
        drift = 0  # centavos the derived rows add over their pack amounts; kept >= 0
        for i in sorted(eligible[:k]):
            row = rows[i]
            qty = row.qty * pcs
            low, high = packs.derived_prices(row.unit_price, pcs)
            price = low if low > 0 and drift + low * qty - row.amount >= 0 else high
            derived = replace(row, qty=qty, unit_price=price, tags=row.tags + (PER_UNIT,))
            if is_round_thousand(derived.amount) or not packs.is_per_unit(item, derived):
                continue
            drift += derived.amount - row.amount
            rows[i] = derived
    return rows


# ---- 3. original-date tails ---------------------------------------------------------------


def date_tails(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    from txns.writer import text_violations  # the writer imports the engine's Row

    share = setting(ctx.bundle, "date_tail_share")
    if share <= 0:
        return rows
    formats = vocabulary.date_tails(ctx.bundle)
    for item_id, idx in _by_item(rows).items():
        batch = [i for i in idx if rows[i].original_date is not None]
        if not batch:
            continue
        stream = ctx.item_stream(item_id, "messiness.tails")
        k = _stochastic_round(share * len(batch), stream)
        stream.shuffle(batch)
        for i in sorted(batch[:k]):
            row = rows[i]
            text = f"{row.text} {vocabulary.render(stream.choice(formats), row.original_date)}"
            if not text_violations(text):
                rows[i] = replace(row, text=text)
    return rows


# ---- 4. entry order --------------------------------------------------------------------------


def entry_order(ctx: EngineContext, rows: list[Row]) -> list[Row]:
    share = setting(ctx.bundle, "backdate_share")
    max_days = max(1, int(setting(ctx.bundle, "backdate_max_days")))
    keys: list[tuple | None] = [None] * len(rows)
    for item_id, idx in _by_item(rows).items():
        stream = ctx.item_stream(item_id, "ordering")
        batch_keys: dict = {}
        twins: dict[tuple, int] = {}
        for i in idx:
            row = rows[i]
            if DUPLICATE in row.tags:
                continue
            if BATCH in row.tags:
                u = batch_keys.setdefault(row.date, stream.next_u64())
                entered = row.date
            else:
                u = stream.next_u64()
                late = not row.tags and stream.chance(share)
                entered = row.date + timedelta(days=stream.randint(1, max_days)) if late else row.date
            keys[i] = (entered, u, item_id, row.original_date or row.date, 0, i)
            twins.setdefault((row.date, row.amount), i)
        for i in idx:
            row = rows[i]
            if DUPLICATE in row.tags:
                twin = twins.get((row.date, row.amount))
                base = keys[twin] if twin is not None else (row.date, stream.next_u64(), item_id, row.date, 0, i)
                keys[i] = base[:4] + (1, i)
    for i, row in enumerate(rows):  # rows without a catalog item (not produced by the engine)
        if keys[i] is None:
            keys[i] = (row.date, 0, "", row.date, 0, i)
    order = sorted(range(len(rows)), key=lambda i: keys[i])
    out = [rows[i] for i in order]
    return _never_sorted(out, [keys[i] for i in order])


def _never_sorted(rows: list[Row], keys: list[tuple]) -> list[Row]:
    """FR-H4: if no row is back-dated, enter one ordinary row a day late anyway."""
    if any(b.date < a.date for a, b in zip(rows, rows[1:])):
        return rows
    last = max((r.date for r in rows), default=None)
    candidates = [i for i, r in enumerate(rows) if not r.tags and r.date < last]
    if not candidates:
        return rows
    pick = min(candidates, key=lambda i: keys[i][1:])
    row = rows.pop(pick)
    later = next(i for i, r in enumerate(rows) if r.date > row.date)
    rows.insert(later + 1, row)
    return rows
