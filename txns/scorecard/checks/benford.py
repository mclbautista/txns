"""Benford first digit of row amounts (FR-I4 (3), T32). Report only: never fails.

Mean absolute deviation (MAD) of first-digit shares from Benford's law, judged
with Nigrini's first-digit bands (0.006 close, 0.012 acceptable, 0.015
marginal). Beyond marginal it warns; with fewer than `benford_min_rows`
(rules.json `scorecard`, default 100) rows it only reports.
"""

import math

from txns.scorecard.registry import PASS, WARN, CheckResult, ScoreContext, check, result

EXPECTED = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
BANDS = ((0.006, "close"), (0.012, "acceptable"), (0.015, "marginal"))
DEFAULT_MIN_ROWS = 100


def first_digit(amount) -> int | None:
    digits = str(abs(amount)).lstrip("0.")
    return int(digits[0]) if digits and digits[0].isdigit() and digits[0] != "0" else None


@check("benford", "anomaly")
def benford(ctx: ScoreContext) -> CheckResult:
    digits = [d for d in (first_digit(a) for a in ctx.amounts if a > 0) if d]
    n = len(digits)
    if not n:
        return result("benford", PASS, "Benford first digit: nothing to measure (report only)", None, None)
    shares = {d: digits.count(d) / n for d in EXPECTED}
    mad = round(sum(abs(shares[d] - EXPECTED[d]) for d in EXPECTED) / 9, 6)
    band = next((label for limit, label in BANDS if mad <= limit), "nonconforming")
    min_rows = int(ctx.rule("benford_min_rows", DEFAULT_MIN_ROWS))
    if n < min_rows:
        status, note = PASS, f"only {n} rows, too few to judge"
    else:
        status, note = (PASS if band != "nonconforming" else WARN), band
    detail = f"first-digit MAD {mad:.4f} over {n} rows: {note} (report only)"
    return result("benford", status, detail, {"mad": mad, "rows": n}, 0.015)
