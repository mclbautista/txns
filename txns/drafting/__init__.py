"""LLM drafting for `author` (FR-C3, FR-C7, FR-D2 gate 1).

    result = draft_all(payload_text, index, temp_dir, connect=..., model=..., warn=...,
                       sleep=..., max_cost_usd=...)
    result.drafts            # parts.Drafts: storylines, items, sellers, variants, vocabulary,
                             # served_models and costs per part
    result.calls, result.cost_usd, result.reused, result.folder

The scrubbed payload (payload.json's text) is split into parts (`parts.plan`:
storylines, catalog batches, variants batches, vocabulary). For each part:

1. A valid draft saved under `<temp>/drafts/<payload sha256>/<part>.json` is
   reused and no call is made (a re-run with the same payload resumes).
2. Otherwise the request (fixed instructions, extracts of the payload, earlier
   drafts, the part's JSON schema) is leak-checked (exit 4, no call, if a ledger
   name would be sent) and sent through the one `llm.Transport`. A retryable
   failure (network error, rate limit) is retried 3 times with exponential
   backoff (`llm.send_with_retries`, FR-C4); a call that still fails, or a
   non-retryable one (retired model slug), exits 3.
3. The response must be one JSON document passing its draft schema and checks
   (`parts.problems`). If not, the part is asked once more with the problem list
   (and the rejected answer, when it carries no ledger name); a second invalid
   answer exits 4 (FR-C5, FR-D2 gate 1). Valid drafts are saved before the next
   part, so a stopped run keeps what it drafted.
   The one exception is a variants answer with texts that match a ledger name: the
   model is never shown a name, so such a match is a coincidence (a plain word the
   books also use as a name). Those texts are dropped locally (`parts.without_collisions`)
   and the rest is checked as usual; an item left short is re-asked once, by location
   and item id only, with the answer minus the dropped texts and a request for spare
   variants (two per lost text). An item the re-ask answer still leaves short is filled
   from the first answer's surviving texts (`parts.with_survivors`) and the whole is
   validated. A draft that lost texts this way also drops, from its re-ask answer and from the
   survivors, the texts whose pack-size wording is invalid (`parts.without_pack_wording`); nothing
   else is repaired. The leak check, the allowlist and every other rule are unchanged
   (issues #29, #31, #33).

Every response's reported cost is summed (FR-C6). Before each call (a new part
or a re-ask) the run stops with exit 3 if this run's calls have reached
`max_cost_usd`; the saved drafts stay, and a re-run resumes from them (FR-C7),
with a fresh cap for its own calls.

`connect()` is called only when a call is needed, so a fully drafted payload
needs no connection.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from txns import llm
from txns.canonical import atomic_write, pretty_json, sha256_hex
from txns.drafting import parts
from txns.drafting.parts import Drafts, Part
from txns.errors import BundleInvalid, LLMUnreachable
from txns.privacy import NameIndex, find_in_object

DRAFTS_DIR = "drafts"
MAX_SHOWN = 8
MAX_REASK_PROBLEMS = 40  # problem lines carried by the re-ask
MAX_REASK_PREVIOUS = 200_000  # characters: a longer rejected answer is not sent back
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
    sleep: Callable[[float], None] = time.sleep,
    max_cost_usd: float | None = None,
) -> Result:
    payload = json.loads(payload_text)
    folder = drafts_folder(temp_dir, payload_text)
    result = Result(Drafts(), folder)
    transport: llm.Transport | None = None

    def call(part: Part, request: llm.Request) -> llm.Response:
        nonlocal transport
        _check_leaks(part, request, index)
        if max_cost_usd is not None and result.cost_usd >= max_cost_usd:
            raise LLMUnreachable(
                f"cost cap reached: this run's LLM calls cost ${result.cost_usd:.4f}, "
                f"`author.max_cost_usd` is ${max_cost_usd:g}; stopped before "
                f"{'re-asking' if request.problems else 'drafting'} `{part.name}`, "
                f"{_kept(result)} kept in {_rel(folder, temp_dir)}; run `txns author` again to resume"
            )
        if transport is None:
            transport = connect()

        def on_retry(n: int, wait: float, exc: llm.TransportError) -> None:
            warn(f"LLM call for draft `{part.name}` failed ({exc}); retry {n} of {llm.RETRIES} in {wait:g}s")

        try:
            response = llm.send_with_retries(transport, request, sleep=sleep, on_retry=on_retry)
        except llm.TransportError as exc:
            tries = f" after {exc.attempts} attempts" if exc.attempts > 1 else ""
            raise LLMUnreachable(
                f"LLM call for draft `{part.name}` failed{tries}: {exc}; "
                f"{_kept(result)} kept in {_rel(folder, temp_dir)}"
            ) from None
        result.calls += 1
        result.cost_usd += _cost(response)
        return response

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
        response = call(part, request)
        cost = _cost(response)
        document, bad, dropped, _ = _check(part, response, payload, result.drafts, index)
        _warn_dropped(part, dropped, warn)
        if bad:
            # FR-C5: one re-ask carrying the validation errors, then exit 4.
            warn(f"draft `{part.name}` failed its schema ({_count(bad)}); asking once more")
            first, collided = document, bool(dropped)
            response = call(part, _reask(request, bad, _sendable(response, document, dropped), index))
            cost += _cost(response)
            document, bad, dropped, unfit = _check(part, response, payload, result.drafts, index, earlier_loss=collided)
            _warn_dropped(part, dropped, warn)
            _warn_unfit(part, unfit, warn)
            if bad and first is not None and document is not None:
                # What survived both answers counts: texts of the first answer fill what the second left short.
                if collided or dropped:
                    first, _ = parts.without_pack_wording(part, first, result.drafts)
                merged = parts.with_survivors(part, first, document, result.drafts)
                if merged != document and not parts.problems(part, merged, payload, result.drafts, index):
                    document, bad = merged, []
            if bad:
                raise BundleInvalid(
                    f"draft `{part.name}` failed its schema again after one re-ask ({_count(bad)}): "
                    f"{_shown(bad, '; ')}; {_kept(result)} kept"
                )
        atomic_write(path, pretty_json({"part": part.name, "model": response.model, "cost_usd": cost, "draft": document}))
        parts.apply(part, document, result.drafts)
        result.drafts.served_models[part.name] = response.model
        result.drafts.costs[part.name] = cost
    return result


def _cost(response: llm.Response) -> float:
    return float(response.cost_usd or 0)


def _count(bad: list[str]) -> str:
    return f"{len(bad)} problem{'s' if len(bad) != 1 else ''}"


def _check(part: Part, response: llm.Response, payload, drafts: Drafts, index: NameIndex,
           earlier_loss: bool | None = None) -> tuple[Any, list[str], list[tuple[int, str, int]], list[int]]:
    """(document, problems, dropped, unfit) of a response; problems == [] when it is a valid draft.

    A variants answer loses the texts that match a ledger name (`parts.without_collisions`,
    listed in `dropped`) and is valid when the rest still meets every rule; when it does not,
    the problems start with one line per item that lost texts, by location only (issue #29).

    The answer to the re-ask (`earlier_loss` is a bool; None for the first answer) also loses its texts
    with invalid pack-size wording (`parts.without_pack_wording`, the items in `unfit`) when the draft
    lost texts to name coincidences, in the first answer (`earlier_loss`) or in this one: they are the
    model's wording slips, not a reason to discard an answer that coincidences already thinned
    (issue #33). Everything else is checked as usual."""
    try:
        document = parse(response.text)
    except (TypeError, ValueError) as exc:
        return None, [f"the response is not one JSON document ({exc})"], [], []
    document, dropped = parts.without_collisions(part, document, index)
    unfit: list[int] = []
    if earlier_loss is not None and (earlier_loss or dropped):
        document, unfit = parts.without_pack_wording(part, document, drafts)
    bad = parts.problems(part, document, payload, drafts, index)
    return document, (parts.collision_problems(document, dropped) + bad if bad else bad), dropped, unfit


def _warn_dropped(part: Part, dropped: list[tuple[int, str, int]], warn: Callable[[str], None]) -> None:
    if dropped:
        warn(f"draft `{part.name}`: dropped {len(dropped)} text{'s' if len(dropped) != 1 else ''} that match a "
             f"ledger name (the model is never shown one), at these places in its answer: "
             f"{_shown(parts.collision_where(dropped))}")


def _warn_unfit(part: Part, unfit: list[int], warn: Callable[[str], None]) -> None:
    if unfit:
        warn(f"draft `{part.name}`: dropped {len(unfit)} text{'s' if len(unfit) != 1 else ''} with pack-size or "
             f"quantity wording that is not allowed, at these items of the re-ask answer: "
             f"{_shown([f'$.items[{i}]' for i in dict.fromkeys(unfit)])}")


def _sendable(response: llm.Response, document: Any, dropped: list) -> str:
    """The answer to send back with a re-ask: the one the model gave, or, when texts that match a
    ledger name were dropped from it, the rest of it (the original carries a name)."""
    return json.dumps(document, ensure_ascii=False) if dropped else response.text


def _leaks(value: Any, index: NameIndex) -> list[str]:
    """Ledger names in `value`, checked on the JSON text as it would go out, parsed back."""
    return find_in_object(json.loads(json.dumps(value, ensure_ascii=False)), index)


def _check_leaks(part: Part, request: llm.Request, index: NameIndex) -> None:
    # Send nothing that is not scrubbed: the whole request, parsed back as it would go out.
    leaks = _leaks(request.content(), index)
    if leaks:
        raise BundleInvalid(
            f"leak check failed: the `{part.name}` request would carry a ledger name not on the brand "
            f"allowlist ({_shown(leaks)}); no LLM call made"
        )


REDACTED_PROBLEM = "a text in your answer carries a ledger name that is not on the brand allowlist"


def _reask(request: llm.Request, bad: list[str], previous: str, index: NameIndex) -> llm.Request:
    """The re-ask: the problem list, plus the rejected answer when it carries no ledger name.

    A problem line that would carry a ledger name is replaced by a generic one, so the
    re-ask never sends a name the first answer brought in."""
    shown = [p if not _leaks(p, index) else REDACTED_PROBLEM for p in bad[:MAX_REASK_PROBLEMS]]
    if len(bad) > MAX_REASK_PROBLEMS:
        shown.append(f"... and {len(bad) - MAX_REASK_PROBLEMS} more problems")
    keep = previous if previous and len(previous) <= MAX_REASK_PREVIOUS and not _carries_name(previous, index) else None
    return request.reask(list(dict.fromkeys(shown)), keep)


def _carries_name(text: str, index: NameIndex) -> bool:
    """A ledger name in the raw text or, when it is JSON, in the parsed document (escapes)."""
    if _leaks(text, index):
        return True
    try:
        return bool(_leaks(parse(text), index))
    except (TypeError, ValueError):
        return False


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
