"""`txns approve [BUNDLE]`: mark a bundle reviewed, after the owner has checked or hand-edited it (Flow 2, FR-D4).

    txns approve                      # the latest promoted bundle (FR-D5)
    txns approve <label>-<hash12>     # a named one

The bundle is found first (none, or an unknown name, exits 2). Then the offline
promotion gates (FR-D2 items 2-8, `txns.gates`) run on the folder as it is now:
anchors, the ledger-name leak check (reads the ledgers in `author.ledgers_dir` and
the brand allowlist; missing ledgers exit 2), duplicate items, item text, internal
consistency and a smoke `generate` with the config (`txns.toml` or `--config`),
which also fails on any row under `calibration.min_transaction_amount` or with
text matching rules.json `denied_item_patterns` (soft in `generate`).
Any failure exits 4 and changes nothing.

The content hash is recomputed (`store.is_unedited`):
- unchanged: `reviewed: true` is set in the manifest in place. The flag is outside
  the hash, so the folder name and hash stay valid (FR-D3).
- hand-edited: the folder is copied to `.txns/approve/bundle/`, marked reviewed and
  promoted as `bundles/<label>-<new hash12>/` (label from its manifest), which becomes
  the latest. The original folder is left exactly as it was (ADR 0002); `generate`
  refuses it by name, since its content no longer matches its hash.
  If a folder with that new name exists already (the same edit approved before, or
  promoted by `author`), it is marked reviewed instead of writing a second copy; if
  that folder has itself been hand-edited, approve exits 4 before running the gates.
  A missing or non-string manifest label on an edited bundle exits 4.

`approve` never reads OPENROUTER_API_KEY and makes no network access (FR-C1).
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from txns import gates, privacy
from txns.bundle import hashing, store
from txns.commands import Runtime
from txns.config import AUTHOR_DEFAULTS, load_config
from txns.errors import BundleInvalid, ExitCode

STAGING_DIR = Path(".txns") / "approve" / "bundle"  # relative to the working folder; gitignored


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("bundle", nargs="?", metavar="BUNDLE", help="bundle folder name <label>-<hash12> (default: latest)")
    p.add_argument("--config", metavar="PATH", help="TOML config for the gates' tolerance and smoke run (default: txns.toml)")


def run(args: argparse.Namespace, rt: Runtime) -> int:
    root = store.bundles_root(rt.cwd)
    folder = store.find(root, args.bundle or "latest", warn=rt.warn)  # FR-D4: no bundle exits 2
    cfg = load_config(rt.cwd, args.config)
    settings = {**AUTHOR_DEFAULTS, **cfg.author}

    manifest = store.read_manifest(folder)
    label = manifest.get("label")
    unedited = store.is_unedited(folder)
    new_name = None
    if not unedited:
        if not isinstance(label, str) or not store.LABEL_RE.match(label):
            raise BundleInvalid(f"bundle {folder.name}: manifest label {json.dumps(label)} is not a valid "
                                "bundle label; nothing changed")
        # The flag set on the copy is outside the hash, so the new folder's name is known now.
        new_name = hashing.folder_name(label, hashing.content_hash(folder))
        taken = root / new_name
        if taken.exists() and not store.is_unedited(taken):
            raise BundleInvalid(f"the edited content would be promoted as {new_name}, but {new_name} already "
                                "exists and has itself been hand-edited; approve or remove it first. Nothing changed")

    if not privacy.load_allowlist(rt.cwd).found:
        rt.warn(f"{privacy.ALLOWLIST_PATH.as_posix()} not found; treating the brand allowlist as empty "
                "(any ledger name in the bundle fails gate 3)")
    index = gates.name_index(rt.cwd, rt.path(settings["ledgers_dir"]))

    rt.out(f"bundle {folder.name}: " + ("content matches its hash" if unedited
                                       else "content differs from its hash (hand-edited)"))
    report = gates.run_offline(folder, config=cfg, today=rt.today, index=index)
    for line in report.lines():
        rt.out(line)
    if not report.ok:
        failed = report.failed()
        raise BundleInvalid(f"promotion gate{'s' if len(failed) > 1 else ''} {', '.join(r.gate for r in failed)} "
                            f"failed; {folder.name} not approved and bundles/ unchanged")

    if unedited:
        _mark(folder, rt, manifest.get("reviewed") is True)
        return ExitCode.OK

    existing = root / new_name
    if existing.exists():  # unedited, checked above
        rt.out(f"the edited content is already promoted as {existing.name}")
        _mark(existing, rt, store.read_manifest(existing).get("reviewed") is True)
        rt.out(f"{store.BUNDLES_DIR}/{folder.name}/ left as it was")
        return ExitCode.OK
    staging = rt.cwd / STAGING_DIR
    shutil.rmtree(staging, ignore_errors=True)
    staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(folder, staging)
        store.mark_reviewed(staging)
        promoted = store.promote(staging, root, label)  # consumes the staging folder
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    rt.out(f"approved {promoted.name}: wrote {store.BUNDLES_DIR}/{promoted.name}/ (reviewed: true), now the latest bundle")
    rt.out(f"{store.BUNDLES_DIR}/{folder.name}/ left as it was (its content no longer matches its hash)")
    return ExitCode.OK


def _mark(folder: Path, rt: Runtime, already: bool) -> None:
    if already:
        rt.out(f"approved {folder.name}: already reviewed, nothing changed")
        return
    store.mark_reviewed(folder)
    rt.out(f"approved {folder.name}: reviewed: true (content and hash unchanged)")
