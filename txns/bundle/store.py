"""Bundle store: find, verify, load and promote bundles under `bundles/`.

- A bundle folder is `bundles/<label>-<hash12>/` (FR-D3).
- "latest" = highest `promotion` number in the manifests (FR-D5), reviewed or not.
- Loading recomputes the content hash; a mismatch (hand edit without
  `approve`) exits 4. No bundle, or an unknown name, exits 2 (T8).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

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


def list_bundles(root: Path) -> list[tuple[int, str, Path]]:
    """(promotion, name, path) for every folder with a manifest, oldest first."""
    if not root.is_dir():
        return []
    found = []
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and (folder / hashing.MANIFEST).is_file():
            promotion = _read_manifest(folder).get("promotion", 0)
            found.append((promotion if isinstance(promotion, int) else 0, folder.name, folder))
    return sorted(found)


def find(root: Path, name: str) -> Path:
    """Folder for `latest` or an explicit `<label>-<hash12>` name."""
    bundles = list_bundles(root)
    if not bundles:
        raise MissingInput(f"no bundle found in {root} (run `txns author` first)")
    if name == "latest":
        return bundles[-1][2]
    for _, folder_name, path in bundles:
        if folder_name == name:
            return path
    raise MissingInput(f"unknown bundle `{name}` in {root}")


def load(folder: Path) -> Bundle:
    """Load a promoted bundle and verify its content hash."""
    manifest = _read_manifest(folder)
    actual = hashing.content_hash(folder)
    recorded = manifest.get("hash")
    label = manifest.get("label")
    if recorded != actual or folder.name != hashing.folder_name(str(label), actual):
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
