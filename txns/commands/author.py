"""`txns author`: ledgers -> reference.json and the scrubbed payload (Flow 1, FR-B, FR-C1, FR-C2).

So far `author` checks the API key, reads the ledgers strictly, applies the
spend-only rule, builds the scrubbed LLM payload and runs the leak check. It
writes, in `.txns/author/` under the working folder (gitignored):

    reference.json   the FR-B4 statistics (goes into the bundle unchanged)
    ledgers.json     {"ledger_hashes": {file name: sha256}} for the bundle manifest
    payload.json     the scrubbed payload: exactly what the LLM will be sent (T37)
    name-map.json    real -> fake names; LOCAL ONLY, never sent, bundled or committed

A leak (a non-allowlisted ledger name surviving in the payload) exits 4 before
any LLM call and leaves no payload.json. Drafting, the promotion gate and
promotion come in later tickets; they must send only `payload.json`'s content
(or objects checked with `privacy.find_in_object`). The API key is only checked
for presence here and is never written anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from txns import ledger, privacy
from txns.canonical import pretty_json
from txns.commands import Runtime
from txns.config import AUTHOR_DEFAULTS, load_config
from txns.errors import BundleInvalid, ExitCode, MissingInput

API_KEY_ENV = "OPENROUTER_API_KEY"
TEMP_DIR = Path(".txns") / "author"  # relative to the working folder
REFERENCE_FILE = "reference.json"
LEDGERS_FILE = "ledgers.json"
PAYLOAD_FILE = "payload.json"
NAME_MAP_FILE = "name-map.json"  # local only
MAX_LEAK_LOCATIONS = 10


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

    payload = _scrubbed_payload(derived, rt, out)

    n = len(derived.ledger_hashes)
    rt.out(f"read {n} ledger{'s' if n != 1 else ''}: {derived.kept} spend rows kept, "
           f"{sum(derived.excluded.values())} left out by the spend-only rule")
    for reason, count in sorted(derived.excluded.items()):
        rt.out(f"  left out {count:>4}  {reason}")
    rt.out(f"categories: {len(derived.categories)}")
    rt.out(f"wrote {TEMP_DIR.as_posix()}/{REFERENCE_FILE}")
    rt.out(f"wrote {TEMP_DIR.as_posix()}/{LEDGERS_FILE}")
    rt.out(f"wrote {TEMP_DIR.as_posix()}/{PAYLOAD_FILE} ({len(payload['categories'])} categories, "
           "aggregates and item-text patterns only)")
    rt.out(f"wrote {TEMP_DIR.as_posix()}/{NAME_MAP_FILE} (real-to-fake names: stays on this machine)")
    rt.out("drafting and bundle promotion are not built yet; stopping after the payload")
    return ExitCode.OK


def _scrubbed_payload(derived: ledger.Derived, rt: Runtime, out: Path) -> dict:
    """Build the payload, scrub it, leak-check it (FR-C2, T37); exit 4 on a leak."""
    allow = privacy.load_allowlist(rt.cwd)
    if not allow.found:
        rt.warn(f"{privacy.ALLOWLIST_PATH.as_posix()} not found; treating the brand allowlist as empty "
                "(every ledger name is replaced by a fabricated one)")
    index = privacy.NameIndex(derived.ledgers, allow)
    payload = privacy.build_payload(derived, index)

    # Check the exact text that would be sent, parsed back.
    text = pretty_json(payload)
    leaks = privacy.find_in_object(json.loads(text), index)
    if leaks:
        (out / PAYLOAD_FILE).unlink(missing_ok=True)  # never leave a stale payload to be sent
        shown = ", ".join(leaks[:MAX_LEAK_LOCATIONS]) + (" ..." if len(leaks) > MAX_LEAK_LOCATIONS else "")
        raise BundleInvalid(
            f"leak check failed: a ledger name not on the brand allowlist survives scrubbing at "
            f"{len(leaks)} place{'s' if len(leaks) != 1 else ''} in the payload ({shown}); "
            "no LLM call made and no payload written"
        )
    _write(out / NAME_MAP_FILE, pretty_json({
        "note": "Real ledger names and their fabricated stand-ins. Local only: never send, bundle or commit this file.",
        "names": index.private_map(),
    }))
    _write(out / PAYLOAD_FILE, text)
    blocked = len(index.blocked)
    rt.out(f"brand allowlist: {len(allow.entries)} entries; {len(index.names) - blocked} ledger names kept, "
           f"{blocked} replaced by fabricated names; leak check passed")
    return json.loads(text)
