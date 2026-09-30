"""Monthly row-count and spend spread (FR-I2): coefficient of variation of
per-day monthly rows and spend within each calendar quarter, against
reference.json `monthly_spread`. A CV from 3 months is itself noisy (standard
error about CV / sqrt(2 x (months - 1))), so the band has that noise floor.
Warn only."""

import math

from txns.scorecard import reference as ref
from txns.scorecard.registry import PASS, CheckResult, ScoreContext, check, relative_noisy, result


@check("monthly_spread", "timing")
def monthly_spread(ctx: ScoreContext) -> list[CheckResult]:
    span = ctx.span
    figures, dof = ref.monthly_spread(((r.date, r.amount) for r in ctx.rows), *span) if span else (None, 0)
    reference = ctx.reference("monthly_spread") or {}
    out = []
    for key, what in (("rows", "monthly row-count spread (CV)"), ("spend", "monthly spend spread (CV)")):
        name = f"monthly_spread.{key}"
        if figures is None:
            out.append(result(name, PASS, f"{what}: needs 2+ full months in one quarter", None, reference.get(key)))
            continue
        want = reference.get(key)
        se = want / math.sqrt(2 * dof) if want is not None and dof else None
        out.append(relative_noisy(ctx, name, figures[key], want, se, what=what, fmt=lambda v: f"{v:.2f}"))
    return out
