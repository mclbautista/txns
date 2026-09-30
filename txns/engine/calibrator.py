"""Calibrator (FR-G): plan, draw and scale until the total is in band, without plug rows.

The band is `[target, target x (1 + band_pct/100)]`; with `target_rows` the row
count must also land in `[target_rows, target_rows x (1 + band_pct/100)]`.

How (FR-G1 to FR-G4):

1. The plan is shaped by tier and the class and storyline multipliers
   (`levers.plan_factor`), then planned and drawn by the ordinary planner and
   drawer. If it is already in band, it is used as is.
2. Coarse search over a global scale, in the spec's order, stopping when met:
   (1) more (or fewer) occurrences of retail items whose archetype exposes an
   occurrence lever, until gap rules stop them growing; then larger quantities
   from the allowed sets for (2) subscription seats, (3) retail quantities,
   (4) big-ticket scope. Every candidate plan is planned and drawn afresh; unit
   prices never move. With `target_rows`, the occurrence scale is set by the
   row count first and the quantity stages then move the total (either way).
3. Closing: from the nearest plan below (or above) the band, whole small
   ordinary occurrences are added (taken from a larger plan, so they are dated
   by the archetype's own rules, and only where gap rules allow) or dropped, or
   swapped one for one, until the total (and row count) is in band. When a
   lumpy archetype (a whole project burst) leaves a gap the nearest plans
   cannot bridge, the added occurrences come from plans with 2x, then 8x the
   occurrences (`WIDER_POOLS`). No drawn amount is ever changed.

Exits: the total or row count needs more occurrences than gap rules allow
(every lever at its limit) -> 5 (`GapsImpossible`); the band, or `target_rows`
together with the total, cannot be met -> 6 (`TargetUnsatisfiable`).
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Callable

from txns.engine import archetypes, drawer, levers, planner
from txns.engine.context import EngineContext
from txns.engine.levers import Setting
from txns.engine.rows import Row
from txns.errors import GapsImpossible, TargetUnsatisfiable
from txns.money import format_pesos

MAX_DOUBLINGS = 24  # occurrence scale grows up to 2**24 before occurrences count as saturated
PLATEAU = 3  # doublings without a new row that mean gap rules stop growth
BISECT_STEPS = 40
QTY_MAX = 1e6  # a quantity factor that puts every draw on the largest allowed quantity
QTY_MIN = 1e-6  # ... and on the smallest
MAX_CLOSING_STEPS = 100_000
WIDER_POOLS = (2, 8)  # occurrence multiples of the pools tried when the nearest plans cannot close


@dataclass(frozen=True)
class Goal:
    lo: int  # centavos
    hi: int
    rows_lo: int | None = None
    rows_hi: int | None = None

    @classmethod
    def of(cls, config) -> "Goal":
        widen = (100 + Fraction(str(config.band_pct))) / 100  # exact for decimal band_pct
        lo = config.target_centavos
        goal = cls(lo=lo, hi=math.floor(lo * widen))
        if config.target_rows is not None:
            goal = replace(goal, rows_lo=config.target_rows, rows_hi=math.floor(config.target_rows * widen))
        return goal

    def total_ok(self, total: int) -> bool:
        return self.lo <= total <= self.hi

    def count_ok(self, n: int) -> bool:
        return self.rows_lo is None or self.rows_lo <= n <= self.rows_hi

    def ok(self, rows: list[Row]) -> bool:
        return self.count_ok(len(rows)) and self.total_ok(_total(rows))

    def band(self) -> str:
        return f"{format_pesos(self.lo)} to {format_pesos(self.hi)}"

    def row_band(self) -> str:
        return f"{self.rows_lo} to {self.rows_hi} rows"


def _total(rows: list[Row]) -> int:
    return sum(r.amount for r in rows)


class _Plans:
    """Plans and draws the whole occurrence plan for a calibration setting (cached)."""

    def __init__(self, ctx: EngineContext):
        self.ctx = ctx
        self.scaler = levers.Scaler(ctx.bundle, ctx.config, archetypes.levers)
        self._cache: dict[Setting, list[Row]] = {}

    def rows(self, setting: Setting) -> list[Row]:
        if setting not in self._cache:
            scaled = replace(self.ctx, bundle=self.scaler.bundle_for(setting))
            self._cache[setting] = drawer.draw(scaled, planner.plan(scaled))
        return self._cache[setting]

    def total(self, setting: Setting) -> int:
        return _total(self.rows(setting))

    def count(self, setting: Setting) -> int:
        return len(self.rows(setting))


@dataclass
class _Bracket:
    """The nearest settings found below and above the goal (either may be missing)."""

    under: Setting | None
    over: Setting | None


def _bisect(
    measure: Callable[[Setting], int],
    lo: int,
    hi: int,
    make: Callable[[float], Setting],
    x_under: float,
    x_over: float,
    *,
    geometric: bool = False,
) -> Setting | _Bracket:
    """Search x between a setting measuring under `lo` and one measuring over `hi`."""
    under, over = make(x_under), make(x_over)
    for s in (under, over):
        if lo <= measure(s) <= hi:
            return s
    for _ in range(BISECT_STEPS):
        x = math.sqrt(x_under * x_over) if geometric else (x_under + x_over) / 2
        s = make(x)
        m = measure(s)
        if lo <= m <= hi:
            return s
        if m < lo:
            x_under, under = x, s
        else:
            x_over, over = x, s
    return _Bracket(under, over)


def _grow_occurrences(plans: _Plans, measure, lo: int, hi: int, start: Setting) -> tuple[Setting | _Bracket, Setting]:
    """Double the occurrence scale until `measure` reaches `lo` or gap rules stop growth.

    Returns (result, last setting): result is a setting in range or a bracket;
    when growth stalls the result is a bracket with no `over`.
    """
    s, prev, stall = start.occurrences, start, 0
    if not plans.scaler.has_occurrence_levers():
        return _Bracket(start, None), start
    for _ in range(MAX_DOUBLINGS):
        nxt = replace(prev, occurrences=s * 2)
        if measure(nxt) >= lo:
            made = _bisect(measure, lo, hi, lambda x: replace(prev, occurrences=x), s, s * 2)
            return made, nxt
        stall = stall + 1 if plans.count(nxt) == plans.count(prev) else 0
        s, prev = s * 2, nxt
        if stall >= PLATEAU:
            break
    return _Bracket(prev, None), prev


def _shrink_occurrences(plans: _Plans, measure, lo: int, hi: int, start: Setting) -> Setting | _Bracket:
    if not plans.scaler.has_occurrence_levers():
        return _Bracket(None, start)
    zero = replace(start, occurrences=0.0)
    if measure(zero) > hi:
        return _Bracket(None, zero)
    return _bisect(measure, lo, hi, lambda x: replace(start, occurrences=x), 0.0, start.occurrences)


def _scale_quantities(plans: _Plans, goal: Goal, start: Setting) -> Setting | _Bracket:
    """Move the total with the quantity stages: up in order seats, quantities, scope; down in reverse."""
    total = plans.total
    stages = plans.scaler.stages_present()
    setting = start
    if total(setting) < goal.lo:
        for stage in stages:
            top = replace(setting, **{stage: QTY_MAX})
            if total(top) >= goal.lo:
                base = getattr(setting, stage)
                return _bisect(
                    total, goal.lo, goal.hi, lambda x: replace(setting, **{stage: x}), base, QTY_MAX, geometric=True
                )
            setting = top
        return _Bracket(setting, None)
    for stage in reversed(stages):
        bottom = replace(setting, **{stage: QTY_MIN})
        if total(bottom) <= goal.hi:
            base = getattr(setting, stage)
            return _bisect(
                total, goal.lo, goal.hi, lambda x: replace(setting, **{stage: x}), QTY_MIN, base, geometric=True
            )
        setting = bottom
    return _Bracket(None, setting)


def _gaps_impossible_total(plans: _Plans, goal: Goal, setting: Setting) -> GapsImpossible:
    return GapsImpossible(
        "gap rules cannot hold at the requested scale: with every item that can grow at its "
        f"gap-rule limit and the largest allowed quantities, the plan reaches only "
        f"{format_pesos(plans.total(setting))}, below the target {format_pesos(goal.lo)}. "
        "Lower `target` or `tier`, or use a bundle with more items."
    )


def _search_total(plans: _Plans, goal: Goal) -> Setting | _Bracket:
    base = Setting()
    total = plans.total
    if total(base) > goal.hi:
        return _shrink_occurrences(plans, total, goal.lo, goal.hi, base)
    found, last = _grow_occurrences(plans, total, goal.lo, goal.hi, base)
    if isinstance(found, Setting) or found.over is not None:
        return found
    found = _scale_quantities(plans, goal, last)
    if isinstance(found, _Bracket) and found.over is None:
        raise _gaps_impossible_total(plans, goal, found.under)
    return found


def _search_rows(plans: _Plans, goal: Goal) -> tuple[Setting, float]:
    """Occurrence scale that puts the row count in its band; returns (setting, pool scale)."""
    count = plans.count
    base = Setting()
    lo, hi = goal.rows_lo, goal.rows_hi
    if count(base) > hi:
        found = _shrink_occurrences(plans, count, lo, hi, base)
    elif count(base) < lo:
        found, last = _grow_occurrences(plans, count, lo, hi, base)
        if isinstance(found, _Bracket) and found.over is None:
            raise GapsImpossible(
                f"gap rules cannot hold for target_rows = {goal.rows_lo}: with every item that can "
                f"grow at its gap-rule limit the plan has at most {count(last)} rows. "
                "Lower `target_rows`, or use a bundle with more items."
            )
    else:
        found = base
    if isinstance(found, _Bracket):
        ends = [s for s in (found.under, found.over) if s is not None]
        found = min(ends, key=lambda s: (abs(count(s) - (lo if count(s) < lo else hi)), count(s) >= lo))
    pool = found.occurrences * 2 if found.occurrences > 0 else 1.0
    return found, pool


def _key(row: Row) -> tuple:
    return (row.item_id, row.date)


def _pool(start: list[Row], source: list[Row]) -> list[Row]:
    have = {_key(r) for r in start}
    return [r for r in source if _key(r) not in have]


class _Closer:
    """Adds, drops or swaps whole small ordinary occurrences until the goal is met (FR-G3)."""

    def __init__(self, ctx: EngineContext, goal: Goal, start: list[Row], pool: list[Row], closable: set[str]):
        self.bundle = ctx.bundle
        self.goal = goal
        stream = ctx.stream("calibration", "closing")
        amounts = sorted(r.amount for r in list(start) + list(pool) if r.item_id in closable)
        median = amounts[len(amounts) // 2] if amounts else 0
        cap = max(goal.hi - goal.lo, median)  # "small": within the band, or no larger than a typical row
        self.rows = list(start)
        self.alive = [True] * len(self.rows)
        self.total = _total(self.rows)
        self.count = len(self.rows)
        self.days: dict[str, Counter] = defaultdict(Counter)
        for r in self.rows:
            self.days[r.item_id][r.date] += 1
        self.drops = [i for i, r in enumerate(self.rows) if r.item_id in closable and r.amount <= cap]
        stream.shuffle(self.drops)
        self.adds = [r for r in pool if r.item_id in closable and r.amount <= cap]
        stream.shuffle(self.adds)
        self.used = [False] * len(self.adds)

    # -- state ---------------------------------------------------------------

    def done(self) -> bool:
        return self.goal.count_ok(self.count) and self.goal.total_ok(self.total)

    def result(self) -> list[Row]:
        kept = [r for r, a in zip(self.rows, self.alive) if a]
        return sorted(kept, key=lambda r: (r.item_id, r.date))

    def _gap_ok(self, row: Row, ignore: Row | None = None) -> bool:
        max_per_day, min_gap = self.bundle.gap_rules(self.bundle.items[row.item_id])
        days = self.days[row.item_id]

        def n(day) -> int:
            k = days.get(day, 0)
            if ignore is not None and ignore.item_id == row.item_id and ignore.date == day:
                k -= 1
            return k

        if n(row.date) >= max_per_day:
            return False
        if min_gap > 1:
            for day in days:
                if day != row.date and abs((day - row.date).days) < min_gap and n(day) > 0:
                    return False
        return True

    def _drop(self, i: int) -> None:
        r = self.rows[i]
        self.alive[i] = False
        self.days[r.item_id][r.date] -= 1
        self.total -= r.amount
        self.count -= 1

    def _add(self, j: int) -> None:
        r = self.adds[j]
        self.used[j] = True
        self.rows.append(r)
        self.alive.append(True)
        self.days[r.item_id][r.date] += 1
        self.total += r.amount
        self.count += 1

    # -- moves -----------------------------------------------------------------

    def add_one(self, max_amount: int) -> bool:
        for j, r in enumerate(self.adds):
            if not self.used[j] and r.amount <= max_amount and self._gap_ok(r):
                self._add(j)
                return True
        return False

    def add_smallest(self) -> bool:
        order = sorted((r.amount, j) for j, r in enumerate(self.adds) if not self.used[j])
        for _, j in order:
            if self._gap_ok(self.adds[j]):
                self._add(j)
                return True
        return False

    def drop_one(self, max_amount: int) -> bool:
        for i in self.drops:
            if self.alive[i] and self.rows[i].amount <= max_amount:
                self._drop(i)
                return True
        return False

    def drop_smallest(self) -> bool:
        live = [(self.rows[i].amount, i) for i in self.drops if self.alive[i]]
        if not live:
            return False
        self._drop(min(live)[1])
        return True

    def swap(self, a: int, b: int) -> bool:
        """Drop one row and add one candidate, moving the total by a delta in [a, b] if possible,
        else as far toward it as possible without overshooting."""
        order = sorted((r.amount, j) for j, r in enumerate(self.adds) if not self.used[j])
        amounts = [x for x, _ in order]
        need_more = a > 0
        best: tuple[int, int, int] | None = None  # (progress, drop index, add index)
        for i in self.drops:
            if not self.alive[i]:
                continue
            r = self.rows[i]
            lo_i = bisect_left(amounts, r.amount + a)
            hi_i = bisect_right(amounts, r.amount + b)
            for k in range(lo_i, hi_i):
                if self._gap_ok(self.adds[order[k][1]], ignore=r):
                    self._drop(i)
                    self._add(order[k][1])
                    return True
            steps = range(lo_i - 1, -1, -1) if need_more else range(hi_i, len(order))
            for k in steps:
                delta = amounts[k] - r.amount
                if (delta <= 0) if need_more else (delta >= 0):
                    break
                if self._gap_ok(self.adds[order[k][1]], ignore=r):
                    progress = abs(delta)
                    if best is None or progress > best[0]:
                        best = (progress, i, order[k][1])
                    break
        if best is None:
            return False
        self._drop(best[1])
        self._add(best[2])
        return True

    def run(self) -> list[Row] | None:
        g = self.goal
        for _ in range(MAX_CLOSING_STEPS):
            if self.done():
                return self.result()
            a, b = g.lo - self.total, g.hi - self.total
            if g.rows_lo is not None and self.count < g.rows_lo:
                moved = self.add_one(b) or self.add_smallest()
            elif g.rows_hi is not None and self.count > g.rows_hi:
                moved = self.drop_one(-a) or self.drop_smallest()
            elif a > 0:
                room = g.rows_hi is None or self.count < g.rows_hi
                moved = (room and self.add_one(b)) or self.swap(a, b)
            else:
                room = g.rows_lo is None or self.count > g.rows_lo
                moved = (room and self.drop_one(-a)) or self.swap(a, b)
            if not moved:
                return None
        return None


def _close(
    ctx: EngineContext, plans: _Plans, goal: Goal, attempts: list[tuple[list[Row], list[Row]]]
) -> list[Row] | None:
    """Try each (start rows, pool source rows) in turn; the first closed plan wins."""
    closable = {
        i.id for i in ctx.bundle.items.values() if levers.is_closable(i, plans.scaler.item_levers(i.id))
    }
    for start, source in attempts:
        if goal.ok(start):
            return start
        closed = _Closer(ctx, goal, start, _pool(start, source), closable).run()
        if closed is not None:
            return closed
    return None


def calibrate(ctx: EngineContext) -> list[Row]:
    """Plan, draw and calibrate: the rows whose total (and count) meet the config's goal."""
    goal = Goal.of(ctx.config)
    plans = _Plans(ctx)
    base = plans.rows(Setting())
    if goal.ok(base):
        return base

    if goal.rows_lo is None:
        found = _search_total(plans, goal)
        if isinstance(found, Setting):
            return plans.rows(found)
        under = plans.rows(found.under) if found.under is not None else None
        over = plans.rows(found.over) if found.over is not None else None
        attempts = []
        if under is not None:
            attempts.append((under, over or []))
        if over is not None:
            attempts.append((over, under or []))
        closed = _close(ctx, plans, goal, attempts)
        if closed is None and under is not None and over is not None:
            # A lumpy archetype (a whole project burst) can leave a gap between the two
            # nearest plans that their own small rows cannot bridge: add small ordinary
            # occurrences from plans with more occurrences instead.
            for factor in WIDER_POOLS:
                wider = replace(found.under, occurrences=max(found.under.occurrences * factor, 1.0))
                closed = _close(ctx, plans, goal, [(under, plans.rows(wider))])
                if closed is not None:
                    break
        if closed is not None:
            return closed
        if under is None:
            raise TargetUnsatisfiable(
                f"the band {goal.band()} cannot be met: the smallest plan the bundle allows totals "
                f"{format_pesos(_total(over))}, above the band. Raise `target` or lower `tier` or multipliers."
            )
        raise TargetUnsatisfiable(
            f"the band {goal.band()} cannot be met without plug rows: the nearest plans total "
            f"{format_pesos(_total(under))} and {format_pesos(_total(over)) if over else 'nothing higher'}, "
            "and adding or dropping whole small occurrences cannot close the gap. Widen `band_pct` or change `target`."
        )

    rows_setting, pool_scale = _search_rows(plans, goal)
    found = rows_setting
    if not goal.total_ok(plans.total(rows_setting)):
        found = _scale_quantities(plans, goal, rows_setting)
    starts = [found] if isinstance(found, Setting) else [s for s in (found.under, found.over) if s is not None]
    attempts = [(plans.rows(s), plans.rows(replace(s, occurrences=pool_scale))) for s in starts]
    closed = _close(ctx, plans, goal, attempts)
    if closed is not None:
        return closed
    near = plans.rows(starts[0])
    raise TargetUnsatisfiable(
        f"target_rows = {goal.rows_lo} ({goal.row_band()}) and a total of {goal.band()} cannot both be met "
        f"without plug rows: at about that row count the plan totals {format_pesos(_total(near))}. "
        "Change `target_rows` or `target`, or widen `band_pct`."
    )
