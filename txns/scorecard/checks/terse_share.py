"""Terse-text share (FR-I4 (8), FR-H1): per category, the share of rows whose text
is one of its item's terse variants, against reference.json `terse_share`.
Ledger-relative, warn only (a beyond-2x deviation is reported as WARN).
Rows whose text no catalog variant uses are left out; a date tail is ignored."""

from txns.scorecard import reference as ref
from txns.scorecard.itemmap import TERSE, TextMatcher
from txns.scorecard.registry import FAIL, PASS, WARN, CheckResult, ScoreContext, check, grade, listing, result, worst


@check("terse_share", "anomaly")
def terse_share(ctx: ScoreContext) -> CheckResult:
    matcher = TextMatcher(ctx.bundle)
    classed = []
    for r in ctx.rows:
        item, match = ctx.item(r.item_id), matcher.match(r.text)
        if item is not None and match is not None:
            classed.append((item.category, match.kind == TERSE))
    value = ref.terse_share(classed)
    ledger = ctx.reference("terse_share") or {}
    refs = {cat: ledger[cat] for cat in value if ledger.get(cat) is not None}
    if not value:
        return result("terse_share", PASS, "terse text share: nothing to measure", value, None)
    if not refs:
        return result("terse_share", PASS, "terse text share by category (no ledger reference)", value, None)
    graded = {cat: grade(value[cat], refs[cat], ctx.tolerance_pct) for cat in refs}
    status = worst(graded.values())
    status = WARN if status == FAIL else status
    notes = [f"{cat} {value[cat]:.0%} vs ledger {refs[cat]:.0%}" for cat in refs if graded[cat] != PASS]
    detail = (
        f"terse text share off in {len(notes)} categor{'y' if len(notes) == 1 else 'ies'}: {listing(notes)}"
        if notes
        else f"terse text share within ±{ctx.tolerance_pct}% in {len(refs)} categories"
    )
    unref = sorted(set(value) - set(refs))
    if unref:
        detail += f"; no ledger reference for {', '.join(unref)}"
    return result("terse_share", status, detail, value, refs)
