"""Canonical JSON, hashing and atomic-write helpers shared by config, bundle, run identity and outputs."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    """Stable JSON text: sorted keys, no spaces, UTF-8 kept as-is."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def pretty_json(value: Any) -> str:
    """Stable human-readable JSON for files we write (run.json, manifests)."""
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def atomic_write(path: Path, data: bytes | str, *, tmp: Path | None = None) -> None:
    """Write `data` (str as UTF-8, newlines kept as given) to `path` all at once: a temp
    file, then `os.replace`, so a reader never sees half a file. The temp file defaults to
    a hidden sibling `.<name>.tmp`; pass `tmp` to put it elsewhere on the same filesystem
    (a bundle's manifest keeps its temp file outside the hashed folder). Creates `path`'s
    folder if needed; the temp file is removed if anything fails."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp if tmp is not None else path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
