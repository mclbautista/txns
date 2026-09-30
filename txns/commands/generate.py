"""`txns generate`: config + bundle + seed -> CSV + run.json + scorecard (Flow 3).

Standard library only, no network, no env reads (FR-H8, T6).
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from txns import engine, scorecard, versions
from txns.bundle import store
from txns.canonical import sha256_hex
from txns.commands import Runtime
from txns.config import load_config, parse_seed, resolve
from txns.errors import ExitCode
from txns.money import format_centavos, format_pesos
from txns.writer import write_outputs

RUN_ID_SHORT = 6


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", metavar="PATH", help="TOML config (default: txns.toml)")
    p.add_argument("--seed", metavar="N", help="seed, overrides the config")
    p.add_argument("--bundle", metavar="LABEL-HASH", help="bundle to use, overrides the config (default: latest)")


def run_id(seed: int, bundle_hash: str, config_hash: str) -> str:
    """Run identity = hash(seed + bundle hash + config hash) (FR-H7)."""
    return sha256_hex(f"{seed}|{bundle_hash}|{config_hash}")


def draw_seed() -> int:
    """A fresh seed from OS entropy when neither flag nor config gives one (FR-E2)."""
    return int.from_bytes(os.urandom(4), "big")


def run(args: argparse.Namespace, rt: Runtime) -> int:
    cfg = load_config(rt.cwd, args.config)
    seed = parse_seed(args.seed, key="--seed") if args.seed is not None else cfg.seed
    bundle_name = args.bundle or cfg.bundle

    bundle = store.load(store.find(store.bundles_root(rt.cwd), bundle_name))

    warnings: list[str] = []
    if not bundle.reviewed:
        warnings.append(f"bundle {bundle.id} is unreviewed; check it and run `txns approve`")
    if bundle.interpreter != versions.interpreter():
        warnings.append(
            f"interpreter {versions.interpreter()} differs from bundle's {bundle.interpreter}; "
            "output may not be byte-identical to runs made with the bundle's interpreter"
        )
    unknown = sorted(set(cfg.storyline_multipliers) - set(bundle.storylines))
    if unknown:
        warnings.append(f"multipliers.storyline names storylines not in the bundle: {', '.join(unknown)}")
    for w in warnings:
        rt.warn(w)

    if seed is None:
        seed = draw_seed()
        rt.out(f"seed: {seed} (drawn; pass --seed {seed} to reproduce)")

    resolved = resolve(cfg, today=rt.today, bundle_id=bundle.id, storylines=list(bundle.storylines), seed=seed)
    config_hash = resolved.config_hash()
    rid = run_id(seed, bundle.hash, config_hash)

    rows = engine.generate(seed, bundle, resolved)
    report = scorecard.score(rows, bundle, resolved)

    period = resolved.period
    stem = f"txns-{period.label}-{rid[:RUN_ID_SHORT]}"
    total = sum(r.amount for r in rows)
    run_doc: dict[str, Any] = {
        "run_id": rid,
        "seed": seed,
        "bundle": {
            "id": bundle.id,
            "hash": bundle.hash,
            "reviewed": bundle.reviewed,
            "interpreter": bundle.interpreter,
        },
        "config_hash": config_hash,
        "config": resolved.as_dict(),
        "period": {
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
            "label": period.label,
            "auto": resolved.auto_period,
        },
        "rows": len(rows),
        "total": format_centavos(total),
        "total_centavos": total,
        "interpreter": versions.interpreter(),
        "generator": versions.generator(),
        "csv": f"{stem}.csv",
        "warnings": warnings,
        "scorecard": report.as_dict(),
    }
    csv_path, run_path = write_outputs(rt.path(resolved.out), stem, rows, run_doc)

    rt.out(f"period {period.start} to {period.end}; bundle {bundle.id}; seed {seed}")
    rt.out(f"wrote {csv_path} ({len(rows)} rows, total {format_pesos(total)})")
    rt.out(f"wrote {run_path}")
    for line in report.lines():
        rt.out(line)
    return ExitCode.SCORECARD_FAILED if report.hard_failure else ExitCode.OK
