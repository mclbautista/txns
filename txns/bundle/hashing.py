"""Bundle content hash (FR-D3, ADR 0002).

The hash covers every file in the bundle folder, recursively. `manifest.json`
is hashed as canonical JSON with the fields in MANIFEST_UNHASHED removed, so
flipping `reviewed`, recording the hash itself or the promotion order never
changes it. Every other file is hashed byte for byte.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from txns.canonical import canonical_json

MANIFEST = "manifest.json"
MANIFEST_UNHASHED = ("hash", "reviewed", "promotion")
SHORT_HASH = 12  # hex chars of the hash used in the folder name


def manifest_for_hash(manifest: dict) -> bytes:
    kept = {k: v for k, v in manifest.items() if k not in MANIFEST_UNHASHED}
    return canonical_json(kept).encode("utf-8")


def content_hash(folder: Path) -> str:
    """SHA-256 hex over (relative path, file digest) pairs in sorted path order."""
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    outer = hashlib.sha256()
    for path in files:
        rel = path.relative_to(folder).as_posix()
        if rel == MANIFEST:
            data = manifest_for_hash(json.loads(path.read_text(encoding="utf-8")))
        else:
            data = path.read_bytes()
        outer.update(rel.encode("utf-8"))
        outer.update(b"\0")
        outer.update(hashlib.sha256(data).hexdigest().encode("ascii"))
        outer.update(b"\n")
    return outer.hexdigest()


def folder_name(label: str, full_hash: str) -> str:
    return f"{label}-{full_hash[:SHORT_HASH]}"
