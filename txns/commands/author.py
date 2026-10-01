"""`txns author`: ledgers -> reference.json, the scrubbed payload and LLM drafts (Flow 1, FR-B, FR-C).

So far `author` checks the API key, reads the ledgers strictly, applies the
spend-only rule, builds the scrubbed LLM payload, runs the leak check and drafts
the catalog, item text and vocabulary through the LLM connection. It writes, in
`.txns/author/` under the working folder (gitignored):

    reference.json   the FR-B4 statistics (goes into the bundle unchanged)
    ledgers.json     {"ledger_hashes": {file name: sha256}} for the bundle manifest
    payload.json     the scrubbed payload: exactly what the LLM will be sent (T37)
    name-map.json    real -> fake names; LOCAL ONLY, never sent, bundled or committed
    drafts/<payload sha256>/<part>.json   valid drafts (`txns.drafting`), reused on re-runs

A leak (a non-allowlisted ledger name surviving in the payload) exits 4 before
any LLM call and leaves no payload.json. Requests carry only extracts of the
payload and earlier drafts, and each is leak-checked again before it is sent.
The connection is OpenRouter with the pinned `author.model` (`txns.llm`).
Network errors and rate limits are retried 3 times with backoff, then exit 3; a
retired model slug exits 3; reaching `author.max_cost_usd` (this run's summed
per-response cost) stops before the next call with exit 3, drafts kept; a
response that fails its draft schema is re-asked once, then exits 4. Bundle
assembly, the promotion gates and promotion come in a later ticket. The API key
is read only from OPENROUTER_API_KEY, goes only into the request's
Authorization header and is never written or printed anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from txns import drafting, ledger, llm, privacy
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

    payload_text, index = _scrubbed_payload(derived, rt, out)
    payload = json.loads(payload_text)

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

    result = drafting.draft_all(
        payload_text,
        index,
        out,
        connect=lambda: rt.transport if rt.transport is not None else llm.connect(settings, rt.env),
        model=settings.get("model"),
        warn=rt.warn,
        sleep=rt.sleep,
        max_cost_usd=settings["max_cost_usd"],
    )
    _report_drafts(result, rt)
    rt.out("bundle assembly and promotion are not built yet; stopping after the drafts")
    return ExitCode.OK


def _report_drafts(result: drafting.Result, rt: Runtime) -> None:
    d = result.drafts
    n_text = sum(len(v["descriptive"]) + len(v["vendor"]) + len(v["terse"]) for v in d.variants.values())
    n_tails = len((d.vocabulary or {}).get("date_tails", []))
    rt.out(f"drafts: {len(d.storylines)} storylines, {len(d.items)} catalog items, "
           f"{n_text} text variants, {n_tails} date-tail formats")
    rt.out(f"  {result.calls} LLM call{'s' if result.calls != 1 else ''} this run (${result.cost_usd:.4f}), "
           f"{len(result.reused)} part{'s' if len(result.reused) != 1 else ''} reused from saved drafts")
    served = sorted(set(d.served_models.values()))
    rt.out(f"  served by: {', '.join(served) if served else '(none)'}")
    rt.out(f"wrote {result.folder.relative_to(rt.cwd).as_posix()}/ ({len(d.served_models)} parts)")


def _scrubbed_payload(derived: ledger.Derived, rt: Runtime, out: Path) -> tuple[str, privacy.NameIndex]:
    """Build the payload, scrub it, leak-check it (FR-C2, T37); exit 4 on a leak.

    Returns payload.json's text (drafting sends only extracts of it) and the name index."""
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
    return text, index
