"""Test helpers: drive `txns` in-process through `main(argv)` in a temp working folder.

    ws = Workspace(self)                      # temp cwd, removed after the test
    ws.install_bundle()                       # promote tests/fixtures/bundle into ws/bundles
    ws.install_bundle(mutate=fn)              # fn(files) edits the parsed JSON files first
    ws.write_config('tier = "high"\n')        # ws/txns.toml, on top of FIXTURE_CONFIG
    r = ws.run("generate", "--seed", "7")     # r.code, r.stdout, r.stderr
    r.csv_bytes, r.run_json, r.rows           # outputs of the last generate
    ws.install_author_inputs()                # committed author inputs + fixture ledgers in ws/inputs
"""

from __future__ import annotations

import copy
import csv
import io
import json
import re
import shutil
import tempfile
import unittest
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from txns import versions
from txns.bundle import store
from txns.cli import main

FIXTURE_BUNDLE = Path(__file__).parent / "fixtures" / "bundle"
FIXTURE_LEDGERS = Path(__file__).parent / "fixtures" / "ledgers"  # fabricated names only
REPO_ROOT = Path(__file__).parent.parent
AUTHOR_INPUTS = ("spend-only.json", "ph-holidays.json")  # committed inputs `author` reads
DEFAULT_TODAY = date(2026, 10, 5)  # auto period = 2026-07-01 .. 2026-09-30

# The fixture bundle spends about ₱25k a quarter, far below the ₱4M default
# target, which it cannot reach (exit 5). Tests that are not about calibration
# run with a band every test-sized plan already meets (₱1 to about ₱10 billion),
# so calibration keeps the plan as drawn. Every Workspace starts with this as its
# txns.toml, and `write_config` puts these keys first unless the text sets them.
FIXTURE_CONFIG = {"target": "1", "band_pct": "1_000_000_000_000"}


def load_fixture_files(src: Path = FIXTURE_BUNDLE) -> dict[str, Any]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(src.glob("*.json"))}


def add_item(
    files: dict[str, Any],
    item_id: str,
    *,
    storyline: str,
    points: list[tuple[int, str]],
    quantities: list[tuple[int, int]],
    descriptive: list[str],
    terse: list[str] = (),
    archetype: str = "petty_daily",
    price_class: str = "retail",
    category: str = "Office Expenses",
    params: dict | None = None,
    vendor: list[tuple[str, str]] = (),
) -> None:
    """Add a catalog item (and its storyline if new) to parsed bundle files.

    `vendor` holds vendor-prefixed variants as (seller id, "Vendor - item") pairs.
    """
    files["storylines"].setdefault(storyline, {"description": f"test storyline {storyline}"})
    files["catalog"]["items"][item_id] = {
        "storyline": storyline,
        "category": category,
        "class": price_class,
        "archetype": archetype,
        "params": params if params is not None else {"per_week": 1.0},
    }
    files["rate_cards"][item_id] = {
        "points": [{"unit_price": p, "seller": s} for p, s in points],
        "quantities": [{"qty": q, "weight": w} for q, w in quantities],
    }
    files["text"][item_id] = {"descriptive": list(descriptive), "terse": list(terse)}
    if vendor:
        files["text"][item_id]["vendor"] = [{"seller": s, "text": t} for s, t in vendor]


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str
    csv_path: Path | None = None
    run_path: Path | None = None

    @property
    def csv_bytes(self) -> bytes:
        return self.csv_path.read_bytes()

    @property
    def run_bytes(self) -> bytes:
        return self.run_path.read_bytes()

    @property
    def run_json(self) -> dict:
        return json.loads(self.run_path.read_text(encoding="utf-8"))

    @property
    def rows(self) -> list[dict[str, str]]:
        return list(csv.DictReader(io.StringIO(self.csv_bytes.decode("utf-8"), newline="")))


@dataclass
class Workspace:
    test: unittest.TestCase
    cwd: Path = field(init=False)

    def __post_init__(self):
        tmp = tempfile.TemporaryDirectory(prefix="txns-test-")
        self.test.addCleanup(tmp.cleanup)
        self.cwd = Path(tmp.name)
        self._staging = 0
        self.write_config("")

    def install_bundle(
        self,
        *,
        label: str = "fixture",
        mutate: Callable[[dict[str, Any]], None] | None = None,
        reviewed: bool = False,
        interpreter: str | None = None,
        src: Path = FIXTURE_BUNDLE,
    ) -> str:
        """Promote a copy of the fixture bundle into ./bundles; returns its folder name."""
        files = copy.deepcopy(load_fixture_files(src))
        files["manifest"]["reviewed"] = reviewed
        files["manifest"]["interpreter"] = interpreter or versions.interpreter()
        if mutate:
            mutate(files)
        self._staging += 1
        staging = self.cwd / f".staging-{self._staging}"
        staging.mkdir()
        for stem, data in files.items():
            (staging / f"{stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return store.promote(staging, self.cwd / "bundles", label).name

    def write_config(self, text: str, name: str = "txns.toml", *, fixture_defaults: bool = True) -> Path:
        """Write a config file; FIXTURE_CONFIG keys the text does not set go first."""
        if fixture_defaults:
            given = set(re.findall(r"^\s*([A-Za-z_]+)\s*=", text, re.MULTILINE))
            text = "".join(f"{k} = {v}\n" for k, v in FIXTURE_CONFIG.items() if k not in given) + text
        path = self.cwd / name
        path.write_text(text, encoding="utf-8")
        return path

    def run(self, *argv: str, today: date = DEFAULT_TODAY, env: dict[str, str] | None = None) -> Result:
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), today=today, env=env if env is not None else {}, cwd=self.cwd, stdout=out, stderr=err)
        result = Result(code, out.getvalue(), err.getvalue())
        # The CSV this run wrote is named on its "wrote ... .csv (" line.
        m = re.search(r"^wrote (.+\.csv) \(", result.stdout, re.MULTILINE)
        if m:
            result.csv_path = Path(m.group(1))
            result.run_path = result.csv_path.with_name(result.csv_path.name[: -len(".csv")] + ".run.json")
        return result

    def all_csvs(self) -> list[Path]:
        return sorted(self.cwd.rglob("*.csv"))

    def bundle_dir(self, name: str) -> Path:
        return self.cwd / "bundles" / name

    def remove(self, rel: str) -> None:
        shutil.rmtree(self.cwd / rel, ignore_errors=True)

    def install_author_inputs(self, ledgers: Path | None = FIXTURE_LEDGERS) -> Path:
        """Copy the committed author inputs and the fixture ledgers into ./inputs; returns inputs/ledgers."""
        inputs = self.cwd / "inputs"
        inputs.mkdir(exist_ok=True)
        for name in AUTHOR_INPUTS:
            shutil.copyfile(REPO_ROOT / "inputs" / name, inputs / name)
        target = inputs / "ledgers"
        if ledgers is not None:
            shutil.copytree(ledgers, target, dirs_exist_ok=True)
        return target
