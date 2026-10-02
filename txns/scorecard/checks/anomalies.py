"""Minimum amount and denied item text: no row under the transaction floor and no
row text matching rules.json `denied_item_patterns` ("Out fee", "Reimbursement Fees").

The floor is the run's `[calibration] min_transaction_amount`, else rules.json
`scorecard.min_transaction_amount` (pesos; how an external CSV, which has no
config, gets one), else none: then only the text is checked.
Soft here, so `generate` only warns (the CSV is still written and the exit code
is unchanged); `approve` and `author` fail the smoke gate on it (`txns.gates`).
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Iterable, Sequence

from txns.bundle import text_rules
from txns.engine.rows import Row
from txns.money import format_pesos
from txns.scorecard.registry import FAIL, PASS, CheckResult, ScoreContext, check, listing, result

NAME = "minimum_amount_and_denied_terms"


def check_minimum_amount_and_denied_terms(rows: Sequence[Row], *, floor: int | None,
                                          patterns: Iterable | None = None) -> list[str]:
    """One line per violation: a row under `floor` centavos (None = no floor) or with denied text."""
    pats = text_rules.denied_patterns() if patterns is None else tuple(patterns)
    bad = []
    for n, row in enumerate(rows, start=1):
        if floor is not None and row.amount < floor:
            bad.append(f"row {n}: {format_pesos(int(row.amount))} is under the {format_pesos(floor)} floor")
        if text_rules.is_denied_text(row.text, pats):
            bad.append(f"row {n}: text {row.text!r} matches `{text_rules.DENIED_KEY}`")
    return bad


def floor_of(ctx: ScoreContext) -> int | None:
    """The floor in centavos: the run's config, else the bundle's scorecard rule, else None."""
    if ctx.config is not None and ctx.config.calibration.min_amount_centavos is not None:
        return ctx.config.calibration.min_amount_centavos
    pesos = ctx.rule("min_transaction_amount", None)
    return None if pesos is None else math.ceil(Fraction(str(pesos)) * 100)


@check(NAME, "anomaly")
def minimum_amount_and_denied_terms(ctx: ScoreContext) -> CheckResult:
    floor = floor_of(ctx)
    bad = check_minimum_amount_and_denied_terms(ctx.rows, floor=floor,
                                                patterns=text_rules.denied_patterns(ctx.bundle.rules))
    shown = format_pesos(floor) if floor is not None else None
    if bad:
        return result(NAME, FAIL, f"{len(bad)} anomalies: {listing(bad)}", value=len(bad), reference=shown)
    under = f"no row under {shown}" if floor is not None else "no minimum amount configured"
    return result(NAME, PASS, f"{under}; no denied item text", value=0, reference=shown)
