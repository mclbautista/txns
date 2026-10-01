"""Bundle store: find, verify, load and promote bundles under `bundles/`.

- A bundle folder is `bundles/<label>-<hash12>/` (FR-D3).
- "latest" = highest `promotion` number in the manifests (FR-D5), reviewed or not.
  A folder whose manifest cannot be read is skipped with a warning; naming it
  explicitly exits 4.
- Loading recomputes the content hash; a mismatch (hand edit without
  `approve`) exits 4. No bundle, or an unknown name, exits 2 (T8).
- `approve` (FR-D4): `is_unedited` tells an untouched bundle from a hand-edited
  one; `mark_reviewed` flips the flag in place (the only in-place change, outside
  the hash); a hand-edited bundle is copied and promoted under its new hash.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from txns.bundle import hashing, model
from txns.bundle.model import Bundle
from txns.canonical import pretty_json
from txns.errors import BundleInvalid, MissingInput

BUNDLES_DIR = "bundles"
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def bundles_root(cwd: Path) -> Path:
    return cwd / BUNDLES_DIR


def _read_manifest(folder: Path) -> dict:
    try:
        manifest = json.loads((folder / hashing.MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BundleInvalid(f"bundle {folder.name}: unreadable manifest: {exc}") from None
    if not isinstance(manifest, dict):
        raise BundleInvalid(f"bundle {folder.name}: manifest must be a JSON object")
    return manifest


def _scan(root: Path) -> tuple[list[tuple[int, str, Path]], dict[str, BundleInvalid]]:
    """Readable bundles as (promotion, name, path), oldest first, and the folders whose
    manifest cannot be read (name -> the error)."""
    found: list[tuple[int, str, Path]] = []
    broken: dict[str, BundleInvalid] = {}
    if not root.is_dir():
        return found, broken
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and (folder / hashing.MANIFEST).is_file():
            try:
                promotion = _read_manifest(folder).get("promotion", 0)
            except BundleInvalid as exc:
                broken[folder.name] = exc
                continue
            found.append((promotion if isinstance(promotion, int) else 0, folder.name, folder))
    return sorted(found), broken


def list_bundles(root: Path, *, warn: Callable[[str], None] | None = None) -> list[tuple[int, str, Path]]:
    """(promotion, name, path) for every folder with a readable manifest, oldest first.

    A folder whose manifest is unreadable is left out (with a warning through `warn`), so
    one broken folder never blocks the others."""
    found, broken = _scan(root)
    if warn:
        for name, exc in broken.items():
            warn(f"skipping {BUNDLES_DIR}/{name}/: {exc}")
    return found


def find(root: Path, name: str, *, warn: Callable[[str], None] | None = None) -> Path:
    """Folder for `latest` or an explicit `<label>-<hash12>` name.

    Folders with an unreadable manifest are skipped (warned about) for `latest`; naming
    one explicitly exits 4."""
    bundles, broken = _scan(root)
    if name in broken:
        raise broken[name]
    if warn:
        for other, exc in broken.items():
            warn(f"skipping {BUNDLES_DIR}/{other}/: {exc}")
    if not bundles:
        if broken:
            raise MissingInput(f"no readable bundle found in {root} ({len(broken)} with an unreadable manifest)")
        raise MissingInput(f"no bundle found in {root} (run `txns author` first)")
    if name == "latest":
        return bundles[-1][2]
    for _, folder_name, path in bundles:
        if folder_name == name:
            return path
    raise MissingInput(f"unknown bundle `{name}` in {root}")


def read_manifest(folder: Path) -> dict:
    """The bundle's manifest (unreadable or not an object: exit 4)."""
    return _read_manifest(folder)


def _matches(folder: Path, manifest: dict, actual: str) -> bool:
    return manifest.get("hash") == actual and folder.name == hashing.folder_name(str(manifest.get("label")), actual)


def is_unedited(folder: Path) -> bool:
    """True when the folder's content still matches its recorded hash and its folder name."""
    return _matches(folder, _read_manifest(folder), hashing.content_hash(folder))


def mark_reviewed(folder: Path) -> None:
    """Set `reviewed: true` in a bundle's manifest, in place. The flag is outside the content
    hash, so the folder name and hash stay valid (FR-D3). The temp file sits next to the
    folder, not in it, so a crash never leaves a stray file inside the hashed content."""
    manifest = _read_manifest(folder)
    manifest["reviewed"] = True
    tmp = folder.parent / f".{folder.name}.manifest.tmp"
    tmp.write_text(pretty_json(manifest), encoding="utf-8")
    os.replace(tmp, folder / hashing.MANIFEST)


def load(folder: Path) -> Bundle:
    """Load a promoted bundle and verify its content hash."""
    manifest = _read_manifest(folder)
    actual = hashing.content_hash(folder)
    if not _matches(folder, manifest, actual):
        raise BundleInvalid(
            f"bundle {folder.name}: content does not match its hash "
            f"(edited in place? run `txns approve` to re-hash it)"
        )
    data = model.read_json_files(folder)
    return model.build(folder.name, folder, actual, data)


def load_staged(folder: Path) -> Bundle:
    """Load a bundle folder that is not promoted yet (no hash check): the promotion gates' view of it."""
    data = model.read_json_files(folder)
    return model.build(folder.name, folder, hashing.content_hash(folder), data)


def promote(staging: Path, root: Path, label: str) -> Path:
    """Hash a staged bundle folder and move it to `root/<label>-<hash12>/`.

    Sets the manifest's `label`, `hash` and next `promotion` number. The
    staging folder is consumed. Used by `author` and `approve`, and by tests
    to install fixture bundles.
    """
    if not LABEL_RE.match(label):
        raise BundleInvalid(f"bad bundle label `{label}`")
    manifest_path = staging / hashing.MANIFEST
    manifest = _read_manifest(staging)
    manifest["label"] = label
    manifest.setdefault("reviewed", False)
    manifest_path.write_text(pretty_json(manifest), encoding="utf-8")
    full = hashing.content_hash(staging)
    existing = list_bundles(root)
    manifest["hash"] = full
    manifest["promotion"] = (max(p for p, _, _ in existing) + 1) if existing else 1
    manifest_path.write_text(pretty_json(manifest), encoding="utf-8")
    target = root / hashing.folder_name(label, full)
    if target.exists():
        raise BundleInvalid(f"bundle {target.name} already exists")
    root.mkdir(parents=True, exist_ok=True)
    # Move via a sibling temp name so a half-copied folder is never "latest".
    tmp = Path(tempfile.mkdtemp(prefix=".promote-", dir=root))
    tmp.rmdir()
    shutil.move(str(staging), str(tmp))
    tmp.rename(target)
    return target
