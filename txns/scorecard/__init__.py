"""Scorecard: pure function (rows, bundle, config) -> report (FR-I).

    score(rows, bundle, config)            rows from the engine (generate)
    score_csv(csv_text, bundle, ...)       any CSV in the output format, e.g. the
                                           negative sample (the optional test seam)

Adding a check: create `txns/scorecard/checks/<name>.py` with a function
decorated by `@check(name, section, hard=...)` from `txns.scorecard.registry`,
which also holds the grading helpers (`relative`, `grade`, `worst`). It is
picked up automatically; the report, printing and run.json need no edits.
Ledger figures come from the bundle's reference.json (schema in
`txns.scorecard.reference`), thresholds from rules.json `scorecard`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Sequence

from txns.bundle.model import Bundle
from txns.config import ResolvedConfig
from txns.engine.rows import Row
from txns.scorecard import checks as _checks
from txns.scorecard.registry import (
    DEFAULT_TOLERANCE_PCT,
    FAIL,
    PASS,
    WARN,
    CheckResult,
    ScoreContext,
    check,
    registered,
)

__all__ = ["score", "score_csv", "Report", "CheckResult", "ScoreContext", "check", "PASS", "WARN", "FAIL"]

_checks.load_all()


@dataclass(frozen=True)
class Report:
    results: tuple[CheckResult, ...]
    tolerance_pct: int | float = DEFAULT_TOLERANCE_PCT

    @property
    def hard_failure(self) -> bool:
        return any(r.hard and r.status == FAIL for r in self.results)

    def get(self, name: str) -> CheckResult:
        for r in self.results:
            if r.name == name:
                return r
        raise KeyError(name)

    def counts(self) -> dict[str, int]:
        c = Counter(r.status for r in self.results)
        return {s: c.get(s, 0) for s in (PASS, WARN, FAIL)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "hard_failure": self.hard_failure,
            "tolerance_pct": self.tolerance_pct,
            "counts": self.counts(),
            "checks": [r.as_dict() for r in self.results],
        }

    def lines(self) -> list[str]:
        out = [f"scorecard (tolerance ±{self.tolerance_pct}%):"]
        for r in self.results:
            flag = " (hard)" if r.hard else ""
            out.append(f"  {r.status.upper():4}  {r.section}/{r.name}{flag}: {r.detail}")
        c = self.counts()
        hard = sum(1 for r in self.results if r.hard and r.status == FAIL)
        verdict = f"HARD FAILURE ({hard} hard check{'s' if hard != 1 else ''} failed)" if hard else "ok"
        out.append(f"  result: {verdict}; {c[PASS]} pass, {c[WARN]} warn, {c[FAIL]} fail")
        return out


def score(
    rows: Sequence[Row],
    bundle: Bundle,
    config: ResolvedConfig | None = None,
    *,
    tolerance_pct: int | float | None = None,
) -> Report:
    """Score rows against the bundle. Tolerance: argument, else config, else 25."""
    if tolerance_pct is None:
        tolerance_pct = config.tolerance_pct if config is not None else DEFAULT_TOLERANCE_PCT
    ctx = ScoreContext(rows=tuple(rows), bundle=bundle, config=config, tolerance_pct=tolerance_pct)
    results: list[CheckResult] = []
    for c in registered():
        got = c.fn(ctx)
        for r in got if isinstance(got, list) else [got]:
            results.append(replace(r, section=c.section, hard=c.hard))
    return Report(tuple(results), tolerance_pct)


def score_csv(data: str | bytes, bundle: Bundle, *, tolerance_pct: int | float | None = None) -> Report:
    """Score any CSV in the output format against a bundle (item ids mapped from text)."""
    from txns.scorecard.external import read_csv

    return score(read_csv(data, bundle), bundle, None, tolerance_pct=tolerance_pct)
