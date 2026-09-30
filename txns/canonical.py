"""Canonical JSON and hashing helpers shared by config, bundle and run identity."""

import hashlib
import json
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
