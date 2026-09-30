"""`txns author`: ledgers -> reference.json in the author temp folder (Flow 1, FR-B, FR-C1).

So far `author` checks the API key, reads the ledgers strictly, applies the
spend-only rule and writes, in `.txns/author/` under the working folder:

    reference.json   the FR-B4 statistics (goes into the bundle unchanged)
    ledgers.json     {"ledger_hashes": {file name: sha256}} for the bundle manifest

Drafting, the promotion gate and promotion come in later tickets. The API key
is only checked for presence here and is never written anywhere.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from txns import ledger
from txns.canonical import pretty_json
from txns.commands import Runtime
from txns.config import AUTHOR_DEFAULTS, load_config
from txns.errors import ExitCode, MissingInput

API_KEY_ENV = "OPENROUTER_API_KEY"
TEMP_DIR = Path(".txns") / "author"  # relative to the working folder
REFERENCE_FILE = "reference.json"
LEDGERS_FILE = "ledgers.json"


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", metavar="PATH", help="TOML config (default: txns.toml)")
    p.add_argument("--label", metavar="NAME", help="label for the promoted bundle")


def temp_dir(cwd: Path) -> Path:
    return cwd / TEMP_DIR


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run(args: argparse.Namespace, rt: Runtime) -> int:
    # FR-C1, T33: a missing key stops the run before anything else is read or written.
    if not rt.env.get(API_KEY_ENV, "").strip():
        raise MissingInput(f"{API_KEY_ENV} is not set; `txns author` needs an OpenRouter API key")

    cfg = load_config(rt.cwd, args.config)
    settings = {**AUTHOR_DEFAULTS, **cfg.author}
    ledgers_dir = rt.path(settings["ledgers_dir"])

    derived = ledger.derive(ledgers_dir, rt.cwd)
    for w in derived.warnings:
        rt.warn(w)

    out = temp_dir(rt.cwd)
    _write(out / REFERENCE_FILE, pretty_json(derived.reference))
    _write(out / LEDGERS_FILE, pretty_json({"ledger_hashes": derived.ledger_hashes}))

    n = len(derived.ledger_hashes)
    rt.out(f"read {n} ledger{'s' if n != 1 else ''}: {derived.kept} spend rows kept, "
           f"{sum(derived.excluded.values())} left out by the spend-only rule")
    for reason, count in sorted(derived.excluded.items()):
        rt.out(f"  left out {count:>4}  {reason}")
    rt.out(f"categories: {len(derived.categories)}")
    rt.out(f"wrote {TEMP_DIR.as_posix()}/{REFERENCE_FILE}")
    rt.out(f"wrote {TEMP_DIR.as_posix()}/{LEDGERS_FILE}")
    rt.out("drafting and bundle promotion are not built yet; stopping after reference.json")
    return ExitCode.OK
