"""A scripted fake of the LLM connection (`txns.llm.Transport`, test seam 2). No network.

    fake = ScriptedLLM()                        # answers every request with a valid draft
    fake.script("catalog-01", Fail("timeout"))  # queue outcomes for one part; used before the default
    fake.script("variants-01", Reply(edit=fn))  # fn(document) edits the valid draft before it is sent back
    fake.script("vocabulary", Reply(text="not json"), Reply(document={...}, model="x/y", cost=0.5))
    fake.requests                               # every Request received, failed calls included
    fake.parts()                                # the part names requested, in order

`valid_draft(request)` builds a draft that passes its checks from the request's
own input (categories, storylines, items, known sellers), the way a well-behaved
model would. Vendor names are copied from the request input only.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from txns.llm import Request, Response, TransportError

DEFAULT_MODEL = "fake/served-model-1"
DEFAULT_COST = 0.01
DATE_TAILS = ["({mon} {d}, {yyyy})", "for {m}/{d}"]


@dataclass
class Reply:
    document: Any = None  # the JSON document to answer with (default: a valid draft)
    text: str | None = None  # raw response text (wins over document)
    edit: Callable[[Any], Any] | None = None  # edits a copy of the valid draft (may return a replacement)
    model: str | None = None
    cost: float | None = None


@dataclass
class Fail:
    message: str = "connection reset"
    retryable: bool = True


class ScriptedLLM:
    def __init__(self, *, model: str = DEFAULT_MODEL, cost: float = DEFAULT_COST):
        self.model = model
        self.cost = cost
        self.requests: list[Request] = []
        self._script: dict[str, list[Reply | Fail]] = {}

    def script(self, part: str, *outcomes: Reply | Fail) -> "ScriptedLLM":
        self._script.setdefault(part, []).extend(outcomes)
        return self

    def parts(self) -> list[str]:
        return [r.part for r in self.requests]

    def sent_text(self) -> str:
        """Everything sent so far, as one text (for leak assertions)."""
        return "\n".join(json.dumps({"model": r.model, **r.content()}, ensure_ascii=False) for r in self.requests)

    def send(self, request: Request) -> Response:
        self.requests.append(request)
        queue = self._script.get(request.part)
        outcome = queue.pop(0) if queue else Reply()
        if isinstance(outcome, Fail):
            raise TransportError(outcome.message, retryable=outcome.retryable)
        if outcome.text is not None:
            text = outcome.text
        else:
            document = outcome.document
            if document is None:
                document = valid_draft(request)
                if outcome.edit is not None:
                    edited = outcome.edit(document)
                    document = document if edited is None else edited
            text = json.dumps(document, ensure_ascii=False)
        return Response(
            text=text,
            model=outcome.model if outcome.model is not None else self.model,
            cost_usd=outcome.cost if outcome.cost is not None else self.cost,
        )


def slug(text: str, n: int = 30) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:n].strip("_")


def valid_draft(request: Request) -> Any:
    kind = request.part.split("-")[0]
    data = copy.deepcopy(dict(request.input))
    build = {"storylines": _storylines, "catalog": _catalog, "variants": _variants, "vocabulary": _vocabulary}
    return build[kind](data)


def _storylines(data: dict) -> dict:
    return {"storylines": [
        {"name": "office_pantry", "description": "Meals, coffee and pantry supplies for the team."},
        {"name": "errands", "description": "Deliveries, supplies and small repairs."},
        {"name": "software_stack", "description": "Monthly software and cloud subscriptions."},
        {"name": "post_projects", "description": "Gear and travel bought while a project runs.",
         "burst_days": [3, 10], "bursts_per_quarter": 2, "quiet_days": 7},
    ]}


def _catalog(data: dict) -> dict:
    known = {s["vendor"]: s["id"] for s in data["known_sellers"] if s["vendor"]}
    taken = set(data["taken_item_ids"])
    items = []
    for n, cat in enumerate(data["categories"], len(taken) + 1):
        vendor = cat["vendors"][0]["name"] if cat["vendors"] else None
        sellers = []
        if vendor:
            sid = known.setdefault(vendor, f"vendor-{len(known) + 1}")
            sellers.append({"id": sid, "vendor": vendor})
        sellers.append({"id": f"shop-{n}", "vendor": None})
        big = "equipment" in cat["category"].lower()
        for suffix in ("a", "b"):
            item_id = f"{slug(cat['category'])}_{suffix}"
            assert item_id not in taken
            item = {"id": item_id, "category": cat["category"], "storyline": "errands", "sellers": sellers}
            if cat["subscription"]:
                item |= {"class": "subscription", "archetype": "fixed_day_subscription",
                         "storyline": "software_stack", "params": {"anchor_day": 5}}
            elif big and suffix == "b":
                item |= {"class": "big_ticket", "archetype": "one_off_big_ticket",
                         "storyline": "post_projects", "goods": "hardware"}
            else:
                item |= {"class": "retail", "archetype": "petty_daily", "params": {"per_week": 1.5}}
                if "supplies" in cat["category"].lower() or "storage" in cat["category"].lower():
                    item["goods"] = "stock"
            items.append(item)
    return {"items": items}


def _variants(data: dict) -> dict:
    out = []
    for item in data["items"]:
        base = item["id"].replace("_", " ")
        named = [s for s in item["sellers"] if s["vendor"]]
        v = {
            "id": item["id"],
            "descriptive": [f"{base} purchase", f"{base} for the office", f"{base} order"],
            "terse": [] if item["class"] == "big_ticket" else [base, f"{base} misc"],
            "vendor": [{"seller": named[0]["id"], "text": f"{named[0]['vendor']} - {base} purchase"}] if named else [],
        }
        if item.get("goods") == "stock":
            v["descriptive"].append(f"{base}, box of {{pcs}}")
        out.append(v)
    return {"items": out}


def _vocabulary(data: dict) -> dict:
    return {"date_tails": list(DATE_TAILS)}
