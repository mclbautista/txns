"""Distinct prices and quantities per catalog item (FR-I4 (7)): per category, the
mean number of distinct unit prices and of distinct quantities per item, against
reference.json `distinct_per_item`. Ledger-relative, warn only; each metric
reports its worst category."""

from txns.scorecard import reference as ref
from txns.scorecard.registry import PASS, CheckResult, ScoreContext, check, grade, listing, result, worst

MEASURES = ("prices", "quantities")


@check("distinct_per_item", "anomaly")
def distinct_per_item(ctx: ScoreContext) -> list[CheckResult]:
    got = ref.distinct_per_item(
        (ctx.bundle.items[item_id].category, item_id, r.unit_price, r.qty)
        for item_id, rows in ctx.by_item.items()
        for r in rows
    )
    ledger = ctx.reference("distinct_per_item") or {}
    out = []
    for measure in MEASURES:
        name = f"distinct_per_item.{measure}"
        value = {cat: figures[measure] for cat, figures in got.items()}
        refs = {cat: ref.figure(ledger, cat, measure) for cat in value}
        refs = {cat: r for cat, r in refs.items() if r is not None}
        if not value:
            out.append(result(name, PASS, f"distinct {measure} per item: nothing to measure", value, None))
            continue
        if not refs:
            out.append(result(name, PASS, f"distinct {measure} per item by category (no ledger reference)", value, None))
            continue
        graded = {cat: grade(value[cat], refs[cat], ctx.tolerance_pct) for cat in refs}
        status = worst(graded.values())
        notes = [f"{cat} {value[cat]:g} vs ledger {refs[cat]:g} ({graded[cat]})" for cat in refs if graded[cat] != PASS]
        unref = sorted(set(value) - set(refs))
        detail = (
            f"distinct {measure} per item off in {len(notes)} categor{'y' if len(notes) == 1 else 'ies'}: {listing(notes)}"
            if notes
            else f"distinct {measure} per item within ±{ctx.tolerance_pct}% in {len(refs)} categories"
        )
        if unref:
            detail += f"; no ledger reference for {', '.join(unref)}"
        out.append(result(name, status, detail, value, refs))
    return out
