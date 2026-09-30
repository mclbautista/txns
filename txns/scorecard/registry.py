"""Check registry for the scorecard (FR-I).

A check is a function `(ctx: ScoreContext) -> CheckResult | list[CheckResult]`
registered with `@check(...)` in its own module under `txns/scorecard/checks/`.
Modules there are imported automatically (sorted by file name).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from txns.bundle.model import Bundle
from txns.config import ResolvedConfig
from txns.engine.rows import Row

PASS, WARN, FAIL = "pass", "warn", "fail"
# Report order of sections; unknown sections sort last.
SECTIONS = ("format", "timing", "price_quantity", "anomaly")


@dataclass(frozen=True)
class ScoreContext:
    rows: Sequence[Row]
    bundle: Bundle
    config: ResolvedConfig | None  # None when scoring an external CSV


@dataclass(frozen=True)
class CheckResult:
    name: str
    section: str
    status: str  # pass | warn | fail
    hard: bool = False  # a hard check's fail exits 1 (FR-I5)
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
