"""Config: TOML + flags + today -> resolved config and config hash (FR-A2, FR-A3, FR-E1).

`load_config` validates the TOML file. `resolve` fills defaults that depend on
the bundle (storyline multipliers), the seed and today (`auto` period), giving a
`ResolvedConfig`. The config hash covers the resolved `generate` keys except
`out`, `seed` and `bundle` (those enter run identity separately, `out` never).
`[author]` keys are validated here but are not part of `generate`'s identity.
`[calibration]` keys (row band, ₱ floor, re-roll cap) are `generate` keys: they
enter run.json and the config hash as the `calibration` table.

Adding a `generate` key: add it to GENERATE_KEYS with a validator in
`_validate_generate`, put it in `ResolvedConfig.as_dict()`. It is then recorded
in run.json and hashed automatically.
"""

from __future__ import annotations

import calendar
import math
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from txns.canonical import canonical_json, sha256_hex
from txns.errors import MissingInput
from txns.prng import MAX_SEED

DEFAULT_CONFIG = "txns.toml"
TIERS = ("independent", "mid", "high")
PRICE_CLASSES = ("subscription", "retail", "big_ticket")

GENERATE_KEYS = (
    "bundle",
    "start",
    "end",
    "target",
    "band_pct",
    "target_rows",
    "tier",
    "tolerance_pct",
    "seed",
    "out",
    "multipliers",
    "calibration",
)
CALIBRATION_KEYS = (
    "min_quarterly_transactions",
    "max_quarterly_transactions",
    "min_transaction_amount",
    "max_reroll_attempts",
)
DEFAULT_MAX_REROLL_ATTEMPTS = 50
AUTHOR_KEYS = ("model", "max_cost_usd", "ledgers_dir")
AUTHOR_DEFAULTS = {"max_cost_usd": 5, "ledgers_dir": "inputs/ledgers"}  # `model` has no default
# Keys that never enter the config hash. `seed` and `bundle` are hashed into
# run identity on their own; `out` is excluded from identity entirely (FR-H7).
UNHASHED_KEYS = ("out", "seed", "bundle")


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @property
    def label(self) -> str:
        """`2026Q3` for an exact calendar quarter, else `20260701-20260930`."""
        s, e = self.start, self.end
        if s.day == 1 and s.month % 3 == 1 and e == quarter_end(s.year, (s.month - 1) // 3):
            return f"{s.year}Q{(s.month - 1) // 3 + 1}"
        return f"{s:%Y%m%d}-{e:%Y%m%d}"

    def days(self) -> int:
        return (self.end - self.start).days + 1


def quarter_end(year: int, q: int) -> date:
    month = 3 * q + 3
    return date(year, month, calendar.monthrange(year, month)[1])


def latest_full_quarter(today: date) -> Period:
    """The last calendar quarter that ended before `today` (T7)."""
    q = (today.month - 1) // 3 - 1
    year = today.year
    if q < 0:
        q, year = 3, year - 1
    return Period(date(year, 3 * q + 1, 1), quarter_end(year, q))


@dataclass(frozen=True)
class Calibration:
    """The `[calibration]` table: row band, transaction floor and the drawer's re-roll cap.

    All unset by default (no row band beyond `target_rows`, no floor). The row
    band applies to the run's period as given, which is a quarter by default.
    """

    min_rows: int | None = None  # min_quarterly_transactions
    max_rows: int | None = None  # max_quarterly_transactions
    min_amount: int | float | None = None  # min_transaction_amount, pesos
    max_rerolls: int = DEFAULT_MAX_REROLL_ATTEMPTS  # max_reroll_attempts

    @property
    def min_amount_centavos(self) -> int | None:
        """The floor in centavos (rounded up, so a row at the floor in pesos always passes)."""
        if self.min_amount is None:
            return None
        return math.ceil(Fraction(str(self.min_amount)) * 100)

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_quarterly_transactions": self.min_rows,
            "max_quarterly_transactions": self.max_rows,
            "min_transaction_amount": self.min_amount,
            "max_reroll_attempts": self.max_rerolls,
        }


@dataclass(frozen=True)
class Config:
    """Validated config file contents plus command-line overrides."""

    bundle: str = "latest"
    start: date | str = "auto"
    end: date | str = "auto"
    target: int = 4_000_000
    band_pct: int | float = 2
    target_rows: int | None = None
    tier: str = "mid"
    tolerance_pct: int | float = 25
    seed: int | None = None
    out: str = "out"
    class_multipliers: dict[str, float] = field(default_factory=dict)
    storyline_multipliers: dict[str, float] = field(default_factory=dict)
    archetype_multipliers: dict[str, float] = field(default_factory=dict)
    calibration: Calibration = field(default_factory=Calibration)
    author: dict[str, Any] = field(default_factory=dict)
    source: str | None = None  # path the config was read from, None if defaults


@dataclass(frozen=True)
class ResolvedConfig:
    bundle: str  # resolved bundle id (folder name)
    period: Period
    auto_period: bool
    target: int  # pesos
    band_pct: int | float
    target_rows: int | None
    tier: str
    tolerance_pct: int | float
    seed: int
    out: str
    class_multipliers: dict[str, float]
    storyline_multipliers: dict[str, float]
    archetype_multipliers: dict[str, float] = field(default_factory=dict)
    calibration: Calibration = field(default_factory=Calibration)

    @property
    def target_centavos(self) -> int:
        return self.target * 100

    def as_dict(self) -> dict[str, Any]:
        """Every resolved `generate` key, as recorded in run.json."""
        return {
            "bundle": self.bundle,
            "start": self.period.start.isoformat(),
            "end": self.period.end.isoformat(),
            "target": self.target,
            "band_pct": self.band_pct,
            "target_rows": self.target_rows,
            "tier": self.tier,
            "tolerance_pct": self.tolerance_pct,
            "seed": self.seed,
            "out": self.out,
            "multipliers": {
                "class": dict(sorted(self.class_multipliers.items())),
                "storyline": dict(sorted(self.storyline_multipliers.items())),
                "archetype": dict(sorted(self.archetype_multipliers.items())),
            },
            "calibration": self.calibration.as_dict(),
        }

    def config_hash(self) -> str:
        hashed = {k: v for k, v in self.as_dict().items() if k not in UNHASHED_KEYS}
        return sha256_hex(canonical_json(hashed))


def _err(msg: str) -> MissingInput:
    return MissingInput(f"config: {msg}")


def _number(key: str, value: Any, *, minimum: float, strict: bool) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _err(f"`{key}` must be a number, got {value!r}")
    if value < minimum or (strict and value == minimum):
        op = ">" if strict else ">="
        raise _err(f"`{key}` must be {op} {minimum}, got {value!r}")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _int(key: str, value: Any, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _err(f"`{key}` must be an integer, got {value!r}")
    if value < minimum:
        raise _err(f"`{key}` must be >= {minimum}, got {value!r}")
    return value


def _date_or_auto(key: str, value: Any) -> date | str:
    # A TOML datetime parses to `datetime`, a `date` subclass that cannot be compared
    # with a date: reject it here rather than crash later.
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        if value == "auto":
            return "auto"
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise _err(f"`{key}` must be `auto` or a YYYY-MM-DD date, got {value!r}")


def parse_seed(value: Any, *, key: str = "seed") -> int | None:
    if value is None or value == "":
        return None
    # isascii + isdecimal: plain 0-9 only (isdigit accepts "²", which int() refuses).
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SEED:
        raise _err(f"`{key}` must be blank or an integer from 0 to {MAX_SEED}, got {value!r}")
    return value


def _multipliers(key: str, table: Any, allowed: tuple[str, ...] | None) -> dict[str, float]:
    if not isinstance(table, dict):
        raise _err(f"`{key}` must be a table")
    out: dict[str, float] = {}
    for name, value in table.items():
        if allowed is not None and name not in allowed:
            raise _err(f"unknown key `{key}.{name}` (expected one of {', '.join(allowed)})")
        v = _number(f"{key}.{name}", value, minimum=0, strict=True)
        out[name] = float(v)
    return out


def _validate_generate(data: dict[str, Any]) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if "bundle" in data:
        b = data["bundle"]
        if not isinstance(b, str) or not b.strip():
            raise _err("`bundle` must be `latest` or a bundle name")
        kw["bundle"] = b.strip()
    for key in ("start", "end"):
        if key in data:
            kw[key] = _date_or_auto(key, data[key])
    if "target" in data:
        kw["target"] = _int("target", data["target"], minimum=1)
    if "band_pct" in data:
        kw["band_pct"] = _number("band_pct", data["band_pct"], minimum=0, strict=False)
    if "target_rows" in data:
        kw["target_rows"] = _int("target_rows", data["target_rows"], minimum=1)
    if "tier" in data:
        if data["tier"] not in TIERS:
            raise _err(f"`tier` must be one of {', '.join(TIERS)}, got {data['tier']!r}")
        kw["tier"] = data["tier"]
    if "tolerance_pct" in data:
        kw["tolerance_pct"] = _number("tolerance_pct", data["tolerance_pct"], minimum=0, strict=True)
    if "seed" in data:
        kw["seed"] = parse_seed(data["seed"])
    if "out" in data:
        if not isinstance(data["out"], str) or not data["out"].strip():
            raise _err("`out` must be a folder path")
        kw["out"] = data["out"]
    if "multipliers" in data:
        m = data["multipliers"]
        if not isinstance(m, dict):
            raise _err("`multipliers` must be a table")
        for sub in m:
            if sub not in ("class", "storyline", "archetype"):
                raise _err(f"unknown table `multipliers.{sub}` (expected class, storyline, archetype)")
        kw["class_multipliers"] = _multipliers("multipliers.class", m.get("class", {}), PRICE_CLASSES)
        kw["storyline_multipliers"] = _multipliers("multipliers.storyline", m.get("storyline", {}), None)
        kw["archetype_multipliers"] = _multipliers("multipliers.archetype", m.get("archetype", {}), None)
    if "calibration" in data:
        kw["calibration"] = _validate_calibration(data["calibration"])
        if kw["calibration"].min_rows is not None and "target_rows" in kw:
            raise _err("set either `target_rows` or `calibration.min_quarterly_transactions`/"
                       "`max_quarterly_transactions`, not both")
    return kw


def _validate_calibration(table: Any) -> Calibration:
    if not isinstance(table, dict):
        raise _err("`calibration` must be a table")
    for key in table:
        if key not in CALIBRATION_KEYS:
            raise _err(f"unknown key `calibration.{key}` (expected one of {', '.join(CALIBRATION_KEYS)})")
    lo_key, hi_key = "min_quarterly_transactions", "max_quarterly_transactions"
    lo = _int(f"calibration.{lo_key}", table[lo_key], minimum=1) if lo_key in table else None
    hi = _int(f"calibration.{hi_key}", table[hi_key], minimum=1) if hi_key in table else None
    if (lo is None) != (hi is None):
        raise _err(f"`calibration.{lo_key}` and `calibration.{hi_key}` go together; set both or neither")
    if lo is not None and hi is not None and lo > hi:
        raise _err(f"`calibration.{lo_key}` {lo} is above `calibration.{hi_key}` {hi}")
    kw: dict[str, Any] = {"min_rows": lo, "max_rows": hi}
    if "min_transaction_amount" in table:
        kw["min_amount"] = _number("calibration.min_transaction_amount", table["min_transaction_amount"],
                                   minimum=0, strict=True)
    if "max_reroll_attempts" in table:
        kw["max_rerolls"] = _int("calibration.max_reroll_attempts", table["max_reroll_attempts"], minimum=1)
    return Calibration(**kw)


def _validate_author(table: Any) -> dict[str, Any]:
    if not isinstance(table, dict):
        raise _err("`author` must be a table")
    for key in table:
        if key not in AUTHOR_KEYS:
            raise _err(f"unknown key `author.{key}` (expected one of {', '.join(AUTHOR_KEYS)})")
    out = dict(table)
    if "model" in out and (not isinstance(out["model"], str) or not out["model"].strip()):
        raise _err("`author.model` must be an OpenRouter model slug")
    if "max_cost_usd" in out:
        out["max_cost_usd"] = _number("author.max_cost_usd", out["max_cost_usd"], minimum=0, strict=True)
    if "ledgers_dir" in out and not isinstance(out["ledgers_dir"], str):
        raise _err("`author.ledgers_dir` must be a folder path")
    return out


def load_config(cwd: Path, path: str | None) -> Config:
    """Read and validate the TOML config.

    An explicit `--config` that does not exist exits 2. A missing default
    `txns.toml` means "all defaults".
    """
    explicit = path is not None
    p = Path(path) if explicit else Path(DEFAULT_CONFIG)
    if not p.is_absolute():
        p = cwd / p
    if not p.exists():
        if explicit:
            raise MissingInput(f"config file not found: {path}")
        return Config()
    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise _err(f"{p.name} is not valid TOML: {exc}") from None
    except OSError as exc:
        raise MissingInput(f"config file unreadable: {p}: {exc}") from None
    for key in data:
        if key not in GENERATE_KEYS and key != "author":
            raise _err(f"unknown key `{key}`")
    kw = _validate_generate(data)
    if "author" in data:
        kw["author"] = _validate_author(data["author"])
    return Config(source=str(path if explicit else DEFAULT_CONFIG), **kw)


def resolve(
    cfg: Config,
    *,
    today: date,
    bundle_id: str,
    storylines: list[str],
    seed: int,
) -> ResolvedConfig:
    """Fill period, storyline multiplier defaults and seed."""
    auto_q = latest_full_quarter(today)
    start = auto_q.start if cfg.start == "auto" else cfg.start
    end = auto_q.end if cfg.end == "auto" else cfg.end
    assert isinstance(start, date) and isinstance(end, date)
    if start > end:
        raise _err(f"`start` {start} is after `end` {end}")
    classes = {c: 1.0 for c in PRICE_CLASSES}
    classes.update(cfg.class_multipliers)
    stories = {s: 1.0 for s in storylines}
    stories.update(cfg.storyline_multipliers)
    return ResolvedConfig(
        bundle=bundle_id,
        period=Period(start, end),
        auto_period=cfg.start == "auto" and cfg.end == "auto",
        target=cfg.target,
        band_pct=cfg.band_pct,
        target_rows=cfg.target_rows,
        tier=cfg.tier,
        tolerance_pct=cfg.tolerance_pct,
        seed=seed,
        out=cfg.out,
        class_multipliers=classes,
        storyline_multipliers=stories,
        archetype_multipliers=dict(cfg.archetype_multipliers),
        calibration=cfg.calibration,
    )
