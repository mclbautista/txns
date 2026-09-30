"""Subscription anchor-day hit rate (FR-I2, FR-E8, T22): share of fixed-day
subscription rows on an anchor day or its weekend/holiday roll-forward
(anchors from the bundle). About 95% or more is the goal: rules.json
`scorecard.anchor_hit_rate_min` (default 0.95), less two standard errors so
a run with few charges is not flagged for sampling noise. Warn only."""

from txns.engine.archetypes import fixed_day_subscription as subs
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, result, share_se

DEFAULT_MIN = 0.95


@check("anchor_hits", "timing")
def anchor_hits(ctx: ScoreContext) -> CheckResult:
    goal = float(ctx.rule("anchor_hit_rate_min", DEFAULT_MIN))
    hits = n = 0
    span = ctx.span
    for item_id, rows in ctx.by_item.items():
        item = ctx.bundle.items[item_id]
        if item.archetype != subs.NAME or span is None:
            continue
        on_time = subs.on_schedule(item, ctx.bundle.calendar, *span)
        n += len(rows)
        hits += sum(1 for r in rows if r.date in on_time)
    if n == 0:
        return result("anchor_hits", PASS, "no fixed-day subscription rows", None, goal)
    rate = hits / n
    floor = goal - 2 * share_se(goal, n)
    status = PASS if rate >= floor else WARN
    detail = f"{hits} of {n} subscription rows on the anchor day or its roll-forward ({rate:.1%}; goal {goal:.0%}"
    detail += f", at least {floor:.1%} at this sample size)" if floor < goal else ")"
    return result("anchor_hits", status, detail, round(rate, 4), goal)
