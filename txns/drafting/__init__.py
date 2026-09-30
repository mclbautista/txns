"""LLM drafting for `author` (FR-C3, FR-C7, FR-D2 gate 1).

    result = draft_all(payload_text, index, temp_dir, connect=..., model=..., warn=...)
    result.drafts            # parts.Drafts: storylines, items, sellers, variants, vocabulary,
                             # served_models and costs per part
    result.calls, result.cost_usd, result.reused, result.folder

The scrubbed payload (payload.json's text) is split into parts (`parts.plan`:
storylines, catalog batches, variants batches, vocabulary). For each part:

1. A valid draft saved under `<temp>/drafts/<payload sha256>/<part>.json` is
   reused and no call is made (a re-run with the same payload resumes).
2. Otherwise the request (fixed instructions, extracts of the payload, earlier
   drafts, the part's JSON schema) is leak-checked (exit 4, no call, if a ledger
   name would be sent) and sent through the one `llm.Transport`.
3. The response must be one JSON document passing its draft schema and checks
   (`parts.problems`); if not, the run stops with exit 4 (FR-D2 gate 1; the one
   re-ask is ticket 16's). A failed call exits 3. Valid drafts are saved before
   the next part, so a stopped run keeps what it drafted.

`connect()` is called only when a call is needed, so a fully drafted payload
needs no connection. Retries, the cost cap and the re-ask belong to the
OpenRouter ticket (16); costs and served models are already recorded per part.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from txns import llm
from txns.canonical import pretty_json, sha256_hex
from txns.drafting import parts
from txns.drafting.parts import Drafts, Part
from txns.errors import BundleInvalid, LLMUnreachable
from txns.privacy import NameIndex, find_in_object

DRAFTS_DIR = "drafts"
MAX_SHOWN = 8
_FENCE = re.compile(r"\A\s*```(?:json)?\s*\n(.*?)\n\s*```\s*\Z", re.DOTALL)


@dataclass
class Result:
    drafts: Drafts
    folder: Path
    calls: int = 0  # calls made in this run
    cost_usd: float = 0.0  # cost of this run's calls
    reused: list[str] = field(default_factory=list)  # parts resumed from saved drafts


def payload_hash(payload_text: str) -> str:
    return sha256_hex(payload_text)


def drafts_folder(temp_dir: Path, payload_text: str) -> Path:
    return temp_dir / DRAFTS_DIR / payload_hash(payload_text)


def parse(text: str) -> Any:
    """The response's JSON document (a single ```json fence around it is tolerated)."""
    m = _FENCE.match(text)
    return json.loads(m.group(1) if m else text)


def draft_all(
    payload_text: str,
    index: NameIndex,
    temp_dir: Path,
    *,
    connect: Callable[[], llm.Transport],
    model: str | None,
    warn: Callable[[str], None],
) -> Result:
    payload = json.loads(payload_text)
    folder = drafts_folder(temp_dir, payload_text)
    result = Result(Drafts(), folder)
    transport: llm.Transport | None = None
    for part in parts.plan(payload):
        path = folder / f"{part.name}.json"
        if _resume(part, path, payload, result.drafts, index, warn):
            result.reused.append(part.name)
            continue
        request = llm.Request(
            part=part.name,
            model=model,
            instructions=parts.INSTRUCTIONS[part.kind],
            input=parts.request_input(part, payload, result.drafts),
            schema=parts.SCHEMAS[part.kind],
        )
        # Send nothing that is not scrubbed: the whole request, parsed back as it would go out.
        leaks = find_in_object(json.loads(json.dumps(request.content(), ensure_ascii=False)), index)
        if leaks:
            raise BundleInvalid(
                f"leak check failed: the `{part.name}` request would carry a ledger name not on the brand "
                f"allowlist ({_shown(leaks)}); no LLM call made"
            )
        if transport is None:
            transport = connect()
        try:
            response = transport.send(request)
        except llm.TransportError as exc:
            raise LLMUnreachable(
                f"LLM call for draft `{part.name}` failed: {exc}; "
                f"{_kept(result)} kept in {_rel(folder, temp_dir)}"
            ) from None
        result.calls += 1
        cost = float(response.cost_usd or 0)
        result.cost_usd += cost
        try:
            document = parse(response.text)
        except (TypeError, ValueError) as exc:
            bad = [f"the response is not one JSON document ({exc})"]
        else:
            bad = parts.problems(part, document, payload, result.drafts, index)
        if bad:
            raise BundleInvalid(
                f"draft `{part.name}` failed its schema ({len(bad)} problem{'s' if len(bad) != 1 else ''}): "
                f"{_shown(bad, '; ')}; {_kept(result)} kept"
            )
        _save(path, {"part": part.name, "model": response.model, "cost_usd": cost, "draft": document})
        parts.apply(part, document, result.drafts)
        result.drafts.served_models[part.name] = response.model
        result.drafts.costs[part.name] = cost
    return result


def _resume(part: Part, path: Path, payload, drafts: Drafts, index: NameIndex, warn) -> bool:
    if not path.exists():
        return False
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        document = saved["draft"]
        bad = parts.problems(part, document, payload, drafts, index)
    except (OSError, ValueError, KeyError, TypeError):
        bad = ["unreadable"]
    if bad:
        warn(f"saved draft `{part.name}` is no longer valid ({bad[0]}); drafting it again")
        path.unlink(missing_ok=True)
        return False
    parts.apply(part, document, drafts)
    drafts.served_models[part.name] = str(saved.get("model", ""))
    drafts.costs[part.name] = float(saved.get("cost_usd", 0) or 0)
    return True


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(pretty_json(data), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _shown(items: list[str], sep: str = ", ") -> str:
    return sep.join(items[:MAX_SHOWN]) + (f"{sep}..." if len(items) > MAX_SHOWN else "")


def _kept(result: Result) -> str:
    n = len(result.drafts.served_models)
    return f"{n} valid draft{'s' if n != 1 else ''}"


def _rel(folder: Path, temp_dir: Path) -> str:
    try:
        return folder.relative_to(temp_dir.parent.parent).as_posix()
    except ValueError:
        return folder.as_posix()
