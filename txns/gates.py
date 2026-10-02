"""Promotion gates (FR-D2 items 2-8): the offline checks `author` runs before promoting a
bundle and `approve` re-runs before marking one reviewed (FR-D4).

    report = run_offline(folder, config=cfg, today=today, index=index)
    report.ok            # every gate passed
    report.lines()       # one line per gate, then its problems
    report.failed()      # the gates that failed

`folder` is a bundle folder, promoted or not (`store.load_staged` reads it without
a hash check). `config` is the loaded config (`txns.toml` or `--config`): the
smoke run uses it with the fixed SMOKE_SEED, as `txns generate` would. `index`
is the ledger name index (`name_index`), for the leak gate. Gate 1 (every LLM
response matches its schema) is the drafting step's (`txns.drafting`).

    2  anchors      prices, tiers, pack sizes and quantities within tolerance of the
                    anchors (`txns.assembly.pricing.check_anchors`, config tolerance_pct)
    3  names        no real ledger name anywhere in the bundle, file names included
                    (`txns.privacy.find_in_files`; reports locations only)
    4  items        no duplicate items: no two items share a descriptive text up to
                    case, spacing and punctuation; a vendor-prefixed variant is
                    compared whole, so sellers may share a generic body after `Vendor - `
    4-6 text        no blank or vendor-only text, every variant names the thing bought,
                    none matches rules.json `denied_item_patterns`,
                    each string one item's (shared terse text only with identical
                    price points), CSV text rules (`txns.bundle.text_rules`)
    7  consistency  the bundle loads (every structural rule `generate` relies on),
                    every item has a registered archetype, a rate card and an allowed
                    quantity set, retail items 2 to 4 price points one per seller
                    (deposit_balance excepted), and every point (base card and dated
                    price steps) of an item not approved for round figures has a
                    quantity giving a non-round amount
    8  smoke        `generate` in memory on SMOKE_SEED with the config: no exit 2-6,
                    no hard scorecard failure, and no row under the config's minimum
                    amount or with denied item text (`minimum_amount_and_denied_terms`,
                    soft in `generate`, hard here) (writes nothing)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from txns import engine, ledger, privacy, scorecard
from txns.assembly.anchors import words
from txns.assembly.pricing import check_anchors
from txns.bundle import events, store, text_rules
from txns.bundle.model import Bundle
from txns.config import Config, resolve
from txns.engine import archetypes
from txns.errors import BundleInvalid, TxnsError
from txns.money import is_round_thousand
from txns.scorecard.checks import anomalies

SMOKE_SEED = 1
MAX_SHOWN = 12  # problems shown per gate


@dataclass(frozen=True)
class GateResult:
    gate: str  # "2", "3", "4", "4-6", "7", "8"
    name: str
    problems: tuple[str, ...] = ()
    skipped: str | None = None  # why the gate could not run (counts as a failure)

    @property
    def ok(self) -> bool:
        return not self.problems and self.skipped is None


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def failed(self) -> list[GateResult]:
        return [r for r in self.results if not r.ok]

    def lines(self) -> list[str]:
        out = ["promotion gates:"]
        for r in self.results:
            if r.skipped is not None:
                out.append(f"  FAIL  gate {r.gate} {r.name}: not run ({r.skipped})")
                continue
            out.append(f"  {'PASS' if r.ok else 'FAIL'}  gate {r.gate} {r.name}"
                       + (f": {len(r.problems)} problem{'s' if len(r.problems) != 1 else ''}" if r.problems else ""))
            out.extend(f"        {p}" for p in r.problems[:MAX_SHOWN])
            if len(r.problems) > MAX_SHOWN:
                out.append(f"        ... and {len(r.problems) - MAX_SHOWN} more")
        return out


def name_index(cwd: Path, ledgers_dir: Path) -> privacy.NameIndex:
    """The ledger name index for the leak gate: ledger names minus the brand allowlist (exit 2 without ledgers)."""
    ledgers, _ = ledger.read_ledgers(ledgers_dir)
    return privacy.NameIndex(ledgers, privacy.load_allowlist(cwd))


def duplicate_items(bundle: Bundle) -> list[str]:
    plain: dict[str, str] = {}  # key -> item: descriptive texts
    bodies: dict[str, str] = {}  # key -> item: what follows `Vendor - ` in a vendor variant
    whole: dict[str, str] = {}  # key -> item: the full vendor-prefixed text
    out = []

    def claim(register: dict[str, str], against: tuple[dict[str, str], ...], item_id: str, text: str, key: str) -> None:
        """Register the text under the item, reporting an earlier item with the same key in `against`."""
        for registry in against:
            other = registry.get(key, item_id)
            if other != item_id:
                out.append(f"items `{other}` and `{item_id}` are the same item: both are called {text!r}")
                break
        register.setdefault(key, item_id)

    for item in bundle.items.values():
        # Sellers' items may share a generic body ("Acme - software subscription", "Zeta - software
        # subscription"), so a vendor body is only compared with descriptive texts and a vendor text
        # whole. A variant without the separator is malformed (gate 4-6 reports it) and names nothing here.
        for text in dict.fromkeys(item.descriptive):
            if key := words(text):  # blank or punctuation only: gate 4-6's to report, not a name to compare
                claim(plain, (plain, bodies), item.id, text, key)
        for full in dict.fromkeys(v.text for v in item.vendor if text_rules.SEPARATOR in v.text):
            body = full.partition(text_rules.SEPARATOR)[2]
            if key := words(full):
                claim(whole, (whole,), item.id, full, key)
            if key := words(body):
                claim(bodies, (plain,), item.id, body, key)
    return out


def consistency(bundle: Bundle) -> list[str]:
    registered = set(archetypes.names())
    out = []
    for item in bundle.items.values():
        where = f"item `{item.id}`"
        if item.archetype not in registered:
            out.append(f"{where}: archetype `{item.archetype}` is not one of {', '.join(sorted(registered))}")
        if not item.price_points:
            out.append(f"{where}: no rate card")
        if not item.quantities or sum(q.weight for q in item.quantities) <= 0:
            out.append(f"{where}: no allowed quantity set")
        if item.price_class == "retail" and item.archetype != events.DEPOSIT_BALANCE:
            sellers = [p.seller for p in item.price_points]
            if not 2 <= len(sellers) <= 4:
                out.append(f"{where}: a retail item has 2 to 4 price points (one per seller), got {len(sellers)}")
            if None in sellers or len(set(sellers)) != len(sellers):
                out.append(f"{where}: a retail item has one price point per seller")
        if not item.round_figures_approved:
            cards = [("price point", item.price_points)]
            cards += [(f"price step {step.date.isoformat()} point", step.points) for step in item.steps]
            for what, points in cards:
                for i, p in enumerate(points):
                    if not any(q.weight > 0 and not is_round_thousand(q.qty * p.price_for(q.qty))
                               for q in item.quantities):
                        out.append(f"{where}: {what} {i} gives a whole-₱1,000 amount at every quantity "
                                   "(a plug-row tell on an item not approved for round figures)")
    return out


def smoke(bundle: Bundle, config: Config, today: date) -> list[str]:
    try:
        resolved = resolve(config, today=today, bundle_id=bundle.id, storylines=list(bundle.storylines), seed=SMOKE_SEED)
        rows = engine.generate(SMOKE_SEED, bundle, resolved)
        report = scorecard.score(rows, bundle, resolved)
    except TxnsError as exc:
        return [f"`generate --seed {SMOKE_SEED}` would exit {int(exc.exit_code)}: {exc}"]
    period = f"{resolved.period.start} to {resolved.period.end}"
    out = [f"hard scorecard failure on seed {SMOKE_SEED} ({period}): {r.name}: {r.detail}"
           for r in report.results if r.hard and r.status == scorecard.FAIL]
    # Soft in `generate`, but a bundle whose rows dip under the floor or carry denied text is not promoted.
    out += [f"anomaly on seed {SMOKE_SEED} ({period}): {r.name}: {r.detail}"
            for r in report.results if r.name == anomalies.NAME and r.status == scorecard.FAIL]
    return out


def run_offline(folder: Path, *, config: Config, today: date, index: privacy.NameIndex) -> GateReport:
    report = GateReport()
    bundle: Bundle | None = None
    load_error = None
    try:
        bundle = store.load_staged(folder)
    except BundleInvalid as exc:
        load_error = str(exc)

    def gate(number: str, name: str, fn: Callable[[], list[str]], needs_bundle: bool = True) -> None:
        if needs_bundle and bundle is None:
            report.results.append(GateResult(number, name, skipped="the bundle does not load, see gate 7"))
            return
        report.results.append(GateResult(number, name, tuple(fn())))

    gate("2", "anchor prices and pack sizes", lambda: check_anchors(bundle, config.tolerance_pct))
    gate("3", "no real ledger name", lambda: privacy.find_in_files([folder], index, root=folder), needs_bundle=False)
    gate("4", "no duplicate items", lambda: duplicate_items(bundle))
    gate("4-6", "item text", lambda: text_rules.text_problems(bundle))
    report.results.append(GateResult("7", "internal consistency", tuple(
        [load_error] if load_error else consistency(bundle))))
    gate("8", "smoke generate", lambda: smoke(bundle, config, today))
    return report
