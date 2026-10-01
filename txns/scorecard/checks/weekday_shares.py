"""Weekday shares (FR-I2, FR-I4 (6), T23): share of rows per weekday against
reference.json `weekday_shares`, one metric per day, graded with a sampling-noise
floor (`relative_noisy`). Plus `weekday_shares.big_ticket_weekend`: big-ticket
rows land on weekdays only (FR-E6), warn only."""

from txns.scorecard import reference as ref
from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, relative_noisy, result, share_se


@check("weekday_shares", "timing")
def weekday_shares(ctx: ScoreContext) -> list[CheckResult]:
    dates = [r.date for r in ctx.rows]
    shares = ref.weekday_shares(dates) or {}
    reference = ctx.reference("weekday_shares") or {}
    out = []
    for day in ref.WEEKDAYS:
        value, want = shares.get(day), reference.get(day)
        se = share_se(want, len(dates)) if want is not None else None
        out.append(
            relative_noisy(
                ctx, f"weekday_shares.{day}", value, want, se, what=f"{day.capitalize()} share", fmt=lambda v: f"{v:.1%}"
            )
        )
    weekend = [
        r for r in ctx.rows if r.date.weekday() >= 5 and (item := ctx.item(r.item_id)) and item.price_class == "big_ticket"
    ]
    name = "weekday_shares.big_ticket_weekend"
    if weekend:
        days = sorted({str(r.date) for r in weekend})
        out.append(result(name, WARN, f"{len(weekend)} big-ticket row(s) on a weekend: {', '.join(days[:3])}", len(weekend), 0))
    else:
        out.append(result(name, PASS, "no big-ticket row on a weekend", 0, 0))
    return out
