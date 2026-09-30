"""Check registry and check-author kit for the scorecard (FR-I).

A check is a function `(ctx: ScoreContext) -> CheckResult | list[CheckResult]`
registered with `@check(name, section, hard=...)` in its own module under
`txns/scorecard/checks/`. Modules there are imported automatically (sorted by
file name); the report, printing and run.json need no edits.

The section and hard flag given to `@check` are stamped on every result the
check returns, so a check only fills name, status, detail, value, reference.
A check that returns several metrics names them `<check>.<metric>`.

Grading (FR-I1):
- ledger-relative metrics: `relative(...)` grades against a `reference.json`
  figure with the run's `tolerance_pct` (pass within ±tol, warn to ±2×tol,
  fail beyond). With no figure in `reference.json` the metric passes, marked
  "no ledger reference". `relative_noisy(...)` does the same for a figure
  measured on a sample (shares, spreads): the pass band is at least two
  standard errors wide, so a small run is not flagged for sampling noise.
- absolute rules (FR-E, FR-F): the check decides; soft ones report pass/warn.
Only a FAIL on a check registered with `hard=True` exits 1 (FR-I5). A FAIL on
a soft check is shown and recorded but never changes the exit code.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from typing import Any, Callable, Iterable, Mapping, Sequence

from txns.bundle.model import Bundle, Item
from txns.config import ResolvedConfig
from txns.engine.rows import Row

PASS, WARN, FAIL = "pass", "warn", "fail"
STATUSES = (PASS, WARN, FAIL)  # in order of severity
# Report order of sections; unknown sections sort last.
SECTIONS = ("format", "timing", "price_quantity", "anomaly")
DEFAULT_TOLERANCE_PCT = 25


@dataclass(frozen=True)
class ScoreContext:
    rows: Sequence[Row]
    bundle: Bundle
    config: ResolvedConfig | None  # None when scoring an external CSV
    tolerance_pct: int | float = DEFAULT_TOLERANCE_PCT

    def reference(self, key: str, default: Any = None) -> Any:
        """A top-level figure from the bundle's reference.json (None if absent)."""
        return self.bundle.reference.get(key, default)

    def rule(self, key: str, default: Any) -> Any:
        """A scorecard threshold from the bundle's rules.json `scorecard` table, else `default`."""
        return self.bundle.rules.get("scorecard", {}).get(key, default)

    def item(self, item_id: str | None) -> Item | None:
        return self.bundle.items.get(item_id) if item_id is not None else None

    @cached_property
    def by_item(self) -> Mapping[str, list[Row]]:
        """Rows of catalog items, grouped by item id (sorted), in row order. Unmapped rows are left out."""
        groups: dict[str, list[Row]] = defaultdict(list)
        for r in self.rows:
            if r.item_id is not None and r.item_id in self.bundle.items:
                groups[r.item_id].append(r)
        return {k: groups[k] for k in sorted(groups)}

    @cached_property
    def amounts(self) -> list:
        """Row amounts in centavos, in row order."""
        return [r.amount for r in self.rows]

    @cached_property
    def total(self):
        return sum(self.amounts)

    @cached_property
    def span(self) -> tuple[date, date] | None:
        """The run's period; for an external CSV, its first to last row date (None with no rows)."""
        if self.config is not None:
            return self.config.period.start, self.config.period.end
        dates = [r.date for r in self.rows]
        return (min(dates), max(dates)) if dates else None


@dataclass(frozen=True)
class CheckResult:
    name: str
    section: str = ""  # stamped from @check
    status: str = PASS  # pass | warn | fail
    hard: bool = False  # stamped from @check; a hard FAIL exits 1 (FR-I5)
    detail: str = ""
    value: Any = None  # measured figure, JSON-serialisable
    reference: Any = None  # ledger / rule figure it was compared with

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "section": self.section,
            "status": self.status,
            "hard": self.hard,
            "detail": self.detail,
            "value": self.value,
            "reference": self.reference,
        }


def result(name: str, status: str, detail: str, value: Any = None, reference: Any = None) -> CheckResult:
    return CheckResult(name=name, status=status, detail=detail, value=value, reference=reference)


# ---- grading helpers ----------------------------------------------------------


def worst(statuses: Iterable[str]) -> str:
    return max(statuses, key=STATUSES.index, default=PASS)


def deviation_pct(value: float, reference: float) -> float | None:
    """|value - reference| as a percentage of reference; None when reference is 0."""
    if reference == 0:
        return None
    return abs(value - reference) / abs(reference) * 100


def grade(value: float, reference: float, tolerance_pct: float) -> str:
    """FR-I1: pass within ±tol of the reference, warn to ±2×tol, fail beyond.

    A zero reference passes only an exact zero (a relative band around 0 is empty).
    """
    dev = deviation_pct(value, reference)
    if dev is None:
        return PASS if value == 0 else FAIL
    if dev <= tolerance_pct:
        return PASS
    return WARN if dev <= 2 * tolerance_pct else FAIL


def relative(
    ctx: ScoreContext,
    name: str,
    value: float | None,
    reference: float | None,
    *,
    what: str,
    fmt: Callable[[Any], str] = str,
) -> CheckResult:
    """A ledger-relative metric graded against `reference` with the run's tolerance."""
    if value is None:
        return result(name, PASS, f"{what}: nothing to measure", None, reference)
    if reference is None:
        return result(name, PASS, f"{what} {fmt(value)} (no ledger reference)", value, None)
    status = grade(value, reference, ctx.tolerance_pct)
    dev = deviation_pct(value, reference)
    off = f"{dev:.0f}%" if dev is not None else "n/a"
    detail = f"{what} {fmt(value)} vs ledger {fmt(reference)} (off {off}, tolerance ±{ctx.tolerance_pct}%)"
    return result(name, status, detail, value, reference)


def relative_noisy(
    ctx: ScoreContext,
    name: str,
    value: float | None,
    reference: float | None,
    se: float | None,
    *,
    what: str,
    fmt: Callable[[Any], str] = str,
    one_sided: bool = False,
) -> CheckResult:
    """`relative` for a sampled figure: the pass band is at least two standard errors.

    A share or spread measured on a few hundred rows moves by more than the
    run's tolerance from sampling alone, so the band is widened to 2 x `se`
    (the figure's standard error at this sample size) when that is wider; the
    warn band stays double the pass band. `one_sided` passes anything at or
    below the reference (for figures that should be near zero, like the
    holiday share).
    """
    if value is None or reference is None:
        return relative(ctx, name, value, reference, what=what, fmt=fmt)
    tol = ctx.tolerance_pct
    widened = bool(se) and reference != 0 and 200 * se / abs(reference) > tol
    if widened:
        tol = 200 * se / abs(reference)
    if one_sided and value <= reference:
        return result(name, PASS, f"{what} {fmt(value)}, at or below ledger {fmt(reference)}", value, reference)
    status = grade(value, reference, tol)
    dev = deviation_pct(value, reference)
    off = f"{dev:.0f}%" if dev is not None else "n/a"
    band = f"{'at most +' if one_sided else '±'}{tol:.0f}%{' incl. sampling noise' if widened else ''}"
    detail = f"{what} {fmt(value)} vs ledger {fmt(reference)} (off {off}, tolerance {band})"
    return result(name, status, detail, value, reference)


def share_se(share: float, n: int) -> float:
    """Standard error of a share measured on n rows."""
    return math.sqrt(max(share * (1 - share), 0.0) / n) if n else 0.0


def listing(items: Sequence[str], limit: int = 3) -> str:
    """'a; b; c (+4 more)' for details."""
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return "; ".join(items[:limit]) + more


# ---- registry -----------------------------------------------------------------

CheckFn = Callable[[ScoreContext], "CheckResult | list[CheckResult]"]


@dataclass(frozen=True)
class RegisteredCheck:
    name: str
    section: str
    hard: bool
    fn: CheckFn


_CHECKS: dict[str, RegisteredCheck] = {}


def check(name: str, section: str, *, hard: bool = False) -> Callable[[CheckFn], CheckFn]:
    """Register a check. `hard=True` marks it as one of the FR-I5 hard set."""

    def deco(fn: CheckFn) -> CheckFn:
        if name in _CHECKS:
            raise RuntimeError(f"scorecard check `{name}` registered twice")
        _CHECKS[name] = RegisteredCheck(name, section, hard, fn)
        return fn

    return deco


def registered() -> list[RegisteredCheck]:
    def key(c: RegisteredCheck):
        order = SECTIONS.index(c.section) if c.section in SECTIONS else len(SECTIONS)
        return (order, c.section, c.name)

    return sorted(_CHECKS.values(), key=key)


def all_checks() -> Mapping[str, RegisteredCheck]:
    return dict(_CHECKS)
