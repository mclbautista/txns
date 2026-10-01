"""Deposit-then-balance offsets (FR-E4, T24): every balance follows a deposit of
its item by the item's offset range (`offset_days`, `txns.bundle.events`), and
every deposit has its balance.

Per `deposit_balance` item, rows are deposits or balances by their tags (the
engine's, or `external.infer_tags` from the price: rate-card point 0 is the
deposit figure, the last point the balance figure). Rows a CSV cannot tell
apart (a one-point card) are read in date order: a row that closes an open
deposit within the offset range is its balance, else it opens a deposit.
Balances are then matched in date order to the earliest unmatched deposit
`offset_days` [min, max] before them (earliest-first finds a full matching
whenever one exists, since each balance's window is an interval). An unmatched
balance, or a deposit left without its balance, is reported. Absolute rule,
warn only; value = {item: unpaired rows}. Rows tagged "duplicate" are left out
(a batch-entry double of a row is not another event).
"""

from txns.bundle import events
from txns.scorecard.checks.gap_rules import EXEMPT_TAGS
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, listing, result

DEPOSIT, BALANCE = "deposit", "balance"


def _roles(rows, lo: int, hi: int) -> tuple[list, list]:
    deposits = sorted(r.date for r in rows if DEPOSIT in r.tags)
    balances = sorted(r.date for r in rows if BALANCE in r.tags)
    open_: list = []
    for day in sorted(r.date for r in rows if DEPOSIT not in r.tags and BALANCE not in r.tags):
        d = next((d for d in open_ if lo <= (day - d).days <= hi), None)
        if d is None:
            open_.append(day)
            deposits.append(day)
        else:
            open_.remove(d)
            balances.append(day)
    return sorted(deposits), sorted(balances)


@check("deposit_offsets", "timing")
def deposit_offsets(ctx: ScoreContext) -> CheckResult:
    unpaired: dict[str, int] = {}
    bad: list[str] = []
    pairs = 0
    for item in ctx.bundle.items.values():
        if item.archetype != events.DEPOSIT_BALANCE:
            continue
        lo, hi = events.offset_days(ctx.bundle, item)
        rows = [r for r in ctx.by_item.get(item.id, []) if not any(t in EXEMPT_TAGS for t in r.tags)]
        deposits, balances = _roles(rows, lo, hi)
        used = [False] * len(deposits)
        missing = 0
        for b in balances:
            j = next((j for j, d in enumerate(deposits) if not used[j] and lo <= (b - d).days <= hi), None)
            if j is None:
                missing += 1
                bad.append(f"{item.id} balance {b}: no deposit {lo}-{hi} days before")
            else:
                used[j] = True
                pairs += 1
        for d, u in zip(deposits, used):
            if not u:
                missing += 1
                bad.append(f"{item.id} deposit {d}: no balance {lo}-{hi} days after")
        if missing:
            unpaired[item.id] = missing
    if bad:
        return result("deposit_offsets", WARN, f"{len(bad)} unpaired deposit/balance row(s): {listing(bad)}", unpaired, {})
    if not pairs:
        return result("deposit_offsets", PASS, "no deposit/balance rows", {}, {})
    return result("deposit_offsets", PASS, f"{pairs} deposit/balance pair(s), every balance at its offset", {}, {})
