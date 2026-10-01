"""Duplicates (FR-I4 (1), FR-I5, FR-H3). Hard.

The duplicate-group rate is the number of same-day, same-item, same-amount
groups of two or more rows, per row (`reference.duplicate_group_rate`), keyed on
the catalog item id (a row no catalog item uses is keyed on its text). It is
graded against reference.json `duplicate_group_rate` with the run's tolerance;
a rate beyond the fail band (more than 2x the tolerance off, either way) fails
hard. The rate is a share measured on the run's rows, so, like the other
sampled shares, the pass band is at least two standard errors of it
(`relative_noisy`, `share_se`), and never under one group either way: a short
run is not failed for sampling noise, while a ₱4M run (about a thousand rows)
fails with no duplicates at all. With no ledger figure the check passes.

Rows tagged "batch" (batch-logged items, entered together on a batch day) count
as rows but never form a group: several same-price rows of one rate card on one
batch day are the batch, not a double entry, and the gap rules cap them.
Rows tagged "party" (party-day items) likewise: several rows of one item on a
party day are the party, capped by the gap rule (FR-E5).
"""

from collections import Counter

from txns.scorecard import reference as ref
from txns.scorecard.registry import PASS, CheckResult, ScoreContext, check, relative_noisy, result, share_se

GROUPLESS_TAGS = ("batch", "party")


def _pct(v: float) -> str:
    return f"{v:.2%}"


def _key(ctx: ScoreContext, n: int, row) -> tuple:
    if any(t in GROUPLESS_TAGS for t in row.tags):
        return (row.date, ("row", n), row.amount)
    item = row.item_id if ctx.item(row.item_id) is not None else ("text", row.text)
    return (row.date, item, row.amount)


@check("duplicates", "anomaly", hard=True)
def duplicates(ctx: ScoreContext) -> CheckResult:
    keys = [_key(ctx, n, r) for n, r in enumerate(ctx.rows)]
    ledger = ctx.reference("duplicate_group_rate")
    rate = ref.duplicate_group_rate(keys)
    if rate is None:
        return result("duplicates", PASS, "duplicate groups: no rows", None, ledger)
    groups = sum(1 for c in Counter(keys).values() if c > 1)
    what = f"duplicate-group rate ({groups} group{'s' if groups != 1 else ''} in {len(keys)} rows)"
    se = max(share_se(ledger, len(keys)) if ledger else 0.0, 1 / len(keys))
    return relative_noisy(ctx, "duplicates", rate, ledger, se, what=what, fmt=_pct)
