"""Scorecard: pure function (rows, bundle, config) -> report (FR-I).

Adding a check: create `txns/scorecard/checks/<name>.py` with a function
decorated by `@check(name, section, hard=...)` from `txns.scorecard.registry`.
It is picked up automatically; the report, printing and run.json need no edits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from txns.bundle.model import Bundle
from txns.config import ResolvedConfig
from txns.engine.rows import Row
from txns.scorecard import checks as _checks
from txns.scorecard.registry import FAIL, PASS, WARN, CheckResult, ScoreContext, check, registered

__all__ = ["score", "Report", "CheckResult", "ScoreContext", "check", "PASS", "WARN", "FAIL"]

_checks.load_all()


@dataclass(frozen=True)
class Report:
    results: tuple[CheckResult, ...]

    @property
    def hard_failure(self) -> bool:
        return any(r.hard and r.status == FAIL for r in self.results)

    def as_dict(self) -> dict[str, Any]:
        return {
            "hard_failure": self.hard_failure,
            "checks": [r.as_dict() for r in self.results],
        }

    def lines(self) -> list[str]:
        out = ["scorecard:"]
        for r in self.results:
            flag = " (hard)" if r.hard else ""
            out.append(f"  {r.status.upper():4}  {r.section}/{r.name}{flag}: {r.detail}")
        verdict = "HARD FAILURE" if self.hard_failure else "ok"
        out.append(f"  result: {verdict}")
        return out


def score(rows: Sequence[Row], bundle: Bundle, config: ResolvedConfig | None = None) -> Report:
    ctx = ScoreContext(rows=tuple(rows), bundle=bundle, config=config)
    results: list[CheckResult] = []
    for c in registered():
        got = c.fn(ctx)
        results.extend(got if isinstance(got, list) else [got])
    return Report(tuple(results))
