"""The item-scoped re-ask of a variants draft that lost texts to ledger-name coincidences (issue #36).

    plan = Targeted.plan(part, first_answer_minus_dropped, dropped, payload, drafts)   # None: no scoped re-ask
    plan.gaps                 # item id -> Gap: the items still under their counts, and how many new texts to ask
    plan.input(), plan.schema()   # the request's data and JSON schema: only those items, only new texts
    plan.shape_problems(answer)   # [] when the answer is exactly what `plan.schema()` and the counts asked for allow
    document, faults = plan.integrate(answer)   # kept texts + new ones, item by item; `problems` still decides

A first answer that lost texts to name coincidences (`parts.without_collisions`) and is left short is not
regenerated as a whole: the model drifts when it is, repeating wording that matched or changing items that
were fine. The first answer's usable texts are chosen per item (`parts.Pick`, as in `parts.recover_variants`)
and kept. The one re-ask names only the items still short, by id, with the wording already kept for them (a
vendor-prefixed text without its vendor) and how many new texts of which kind to write (the shortfall plus a
spare that grows with what the item lost, never past the 12-variant cap). Nothing about the texts that were dropped is sent, not even that there were any: the
re-ask carries no problem list and no earlier answer, and the instructions only ask for fresh wording.

The answer fails closed before anything is integrated (issue #40): it must conform to the exact schema that
was sent (`schema()`), list each asked item once, and hold exactly the number of new texts of each kind that was
asked for that item (an empty list for a kind that was not). An unknown or repeated item, an extra key, a
malformed entry, a text that is not a string, a vendor-prefixed text, an unrequested kind or a count that is off
by one is a problem, whatever the kept texts would add up to. The check is on the answer as the model gave it,
before texts that match a ledger name are dropped from it; its messages carry locations, never a value.

A conforming answer is integrated deterministically: every kept text stays, in its place; the new texts are
added in the answer's order only while the item is under its counts, skipping texts that repeat, that another
item or an earlier draft has, that are past the cap, or whose pack-size wording is invalid (issue #33's repair).
A price, length, character or similar fault in a text is not repaired. The caller validates the result in full
(`parts.problems`) and an item still short exits 4: no third call.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from txns.drafting import jsonschema, parts
from txns.drafting.parts import Drafts, Part

INSTRUCTIONS = parts.COMMON + (
    "Write replacement item text variants. The input lists only the items that still need texts. Each item "
    "has `have`, the descriptive and terse wording already kept for it, and `need`, how many NEW texts to write "
    "for it: exactly that many descriptive ones and that many terse ones, and an empty list where the number is 0. "
    "Do not repeat or lightly reword the texts in `have`; use fresh wording, different nouns and phrasing, in the "
    "voice of the item texts shown for its category: short, plain, ASCII, at most 100 characters. Descriptive "
    "texts name the thing bought; terse ones are a word or two a hurried bookkeeper would type. Plain texts "
    "carry no vendor and no \" - \"; leave `vendor` an empty list. Every text belongs to one item only: do not "
    "reuse a text across items. Never state a price or a pack size; for a stock item a text may mark where the "
    f"pack size goes with {parts.PCS} (\"Ballpens, box of {parts.PCS}\"). Answer with one object for each listed "
    "item and no other item."
)


@dataclass(frozen=True)
class Gap:
    """What one catalog item still lacks, and how many new texts of each kind are asked for it."""

    item_id: str
    descriptive: int  # new plain descriptive texts asked (0: none)
    terse: int  # new terse texts asked (0: none)


def _asked(shortfall: int, lost: int, kept: int) -> int:
    """New texts of one kind to ask: the shortfall plus a spare that grows with what the item lost,
    never taking the item past `MAX_VARIANTS` in that kind."""
    if not shortfall:
        return 0
    spare = max(parts.COLLISION_SPARE, parts.COLLISION_SPARE_PER_LOSS * lost)
    return min(shortfall + spare, parts.MAX_VARIANTS - kept)


def _locations(errors: list[str]) -> list[str]:
    """Schema errors by location only: their messages can quote what the model wrote (a key, a text, an id)."""
    out: list[str] = []
    for error in errors:
        line = f"{error.partition(': ')[0]}: does not fit the schema of the re-ask"
        if line not in out:
            out.append(line)
    return out


class Targeted:
    def __init__(self, part: Part, payload: Mapping[str, Any], drafts: Drafts, batch: dict[str, dict],
                 names: set[str], picks: dict[str, parts.Pick], gaps: dict[str, Gap]):
        self.part, self.payload, self.drafts = part, payload, drafts
        self.batch, self.names, self.picks, self.gaps = batch, names, picks, gaps

    @classmethod
    def plan(cls, part: Part, first: Any, first_dropped: list[tuple[int, str, int]], payload: Mapping[str, Any],
             drafts: Drafts) -> "Targeted | None":
        """The scoped re-ask for `first` (a variants answer minus its dropped texts), or None when it does not
        apply: the answer is not well-formed, an item of it has a fault that is not repaired, or every
        item is already complete (then the ordinary re-ask stands)."""
        batch = {i: item for i, item in drafts.items.items() if item["category"] in part.categories}
        answer = parts.answer_items(part, first, batch)
        if answer is None:
            return None
        names = parts.vendor_names(payload, drafts)
        asked = first["items"]
        lost: dict[str, dict[str, int]] = {}  # item id -> kind group -> texts dropped from the first answer
        for i, kind, _ in first_dropped:
            if i < len(asked) and isinstance(asked[i], dict) and isinstance(asked[i].get("id"), str):
                by_kind = lost.setdefault(asked[i]["id"], {})
                group = "terse" if kind == "terse" else "descriptive"
                by_kind[group] = by_kind.get(group, 0) + 1
        claims = parts.used_texts(drafts)
        picks: dict[str, parts.Pick] = {}
        for item_id in sorted(batch, key=lambda i: i in lost):  # an item that lost texts gives up a shared one
            pool = parts.answer_pool(batch[item_id], answer, names)
            if pool is None:
                return None
            pick = picks[item_id] = parts.Pick(batch[item_id])
            pick.take(pool, claims, only_if_short=False)
            parts.claim(claims, item_id, pick.lists)
        gaps = {}
        for item_id in batch:
            need = picks[item_id].shortfall()
            if any(need.values()):
                by_kind, kept = lost.get(item_id, {}), picks[item_id].lists
                gaps[item_id] = Gap(
                    item_id,
                    _asked(need["descriptive"], by_kind.get("descriptive", 0), len(kept["descriptive"])),
                    _asked(need["terse"], by_kind.get("terse", 0), len(kept["terse"])),
                )
        return cls(part, payload, drafts, batch, names, picks, gaps) if gaps else None

    def input(self) -> dict[str, Any]:
        """The data of the re-ask: the deficient items only, with what is kept and what is needed."""
        items = []
        for item_id, gap in self.gaps.items():
            item, kept = self.batch[item_id], self.picks[item_id].lists
            items.append(
                {k: item[k] for k in ("id", "category", "class", "goods", "sellers") if k in item}
                | {"have": {"descriptive": kept["descriptive"] + [parts.body(x["text"]) for x in kept["vendor"]],
                            "terse": list(kept["terse"])},
                   "need": {"descriptive": gap.descriptive, "terse": gap.terse}}
            )
        wanted = {i["category"] for i in items}
        return {
            "categories": [
                {k: c.get(k) for k in ("category", "rows", "textless_rows", "item_texts")}
                for c in parts.categories_of(self.payload, self.part.categories) if c["category"] in wanted
            ],
            "items": items,
        }

    def schema(self) -> dict[str, Any]:
        """The variants schema narrowed to the re-ask: one object for each listed item and none other, no more
        texts of a kind than any item is asked for (none for a kind no item needs), no vendor-prefixed texts."""
        schema = copy.deepcopy(parts.SCHEMAS["variants"])
        items = schema["properties"]["items"]
        items["minItems"] = items["maxItems"] = len(self.gaps)
        props = items["items"]["properties"]
        props["id"] = {"type": "string", "enum": list(self.gaps)}
        props["descriptive"]["minItems"] = 0
        props["descriptive"]["maxItems"] = max(g.descriptive for g in self.gaps.values())
        props["terse"]["maxItems"] = max(g.terse for g in self.gaps.values())
        props["vendor"]["maxItems"] = 0
        return schema

    def shape_problems(self, answer: Any) -> list[str]:
        """Why `answer` does not fit the re-ask, by location only (never an id, a key or a text): [] when it
        conforms to `schema()`, lists each asked item once and holds exactly the texts asked for each. The counts
        are per item, so they are checked here: the schema's item is shared and bounds each kind by the largest ask."""
        out = _locations(jsonschema.errors(answer, self.schema()))
        if out:
            return out
        seen: set[str] = set()
        for i, item in enumerate(answer["items"]):
            where = f"$.items[{i}]"
            if item["id"] in seen:
                out.append(f"{where}: listed twice")
                continue
            seen.add(item["id"])
            gap = self.gaps[item["id"]]
            for kind, asked in (("descriptive", gap.descriptive), ("terse", gap.terse)):
                if len(item[kind]) != asked:
                    out.append(f"{where}.{kind}: expected exactly {asked} texts, got {len(item[kind])}")
        return out

    def integrate(self, answer: Any) -> tuple[dict[str, Any], list[str]]:
        """(document, faults): every item's kept texts, topped up for the deficient items from the answer.

        `answer` must pass `shape_problems` (every asked item is in it). `faults` lists, by location in the answer, the faults that are not
        repaired (a price, a length, a character): that item gets nothing from the answer.
        The document may still be short; `parts.problems` says where."""
        where = {item["id"]: (at, item) for at, item in enumerate(answer["items"])}
        picks = copy.deepcopy(self.picks)
        claims = parts.used_texts(self.drafts)
        for item_id, pick in picks.items():
            parts.claim(claims, item_id, pick.lists)  # what is kept is claimed before anything new is considered
        faults: list[str] = []
        for item_id in self.gaps:  # catalog order, whatever the order of the answer
            at, item = where[item_id]
            asked = {"descriptive": item["descriptive"], "terse": item["terse"], "vendor": []}
            pool = parts.answer_pool(self.batch[item_id], {item_id: asked}, self.names)
            if pool is None:
                faults.extend(parts.entry_faults(f"$.items[{at}]", self.batch[item_id], asked, self.names))
                continue
            picks[item_id].take(pool, claims, only_if_short=True)
            parts.claim(claims, item_id, picks[item_id].lists)
        return {"items": [{"id": i, **picks[i].lists} for i in self.batch]}, faults
