"""The draft parts `author` asks the LLM for, their schemas and checks (FR-C3, FR-D2 gate 1).

    parts = plan(payload)                    # storylines, catalog-NN, variants-NN, vocabulary
    request_input(part, payload, drafts)     # what the call carries (payload extracts + earlier drafts)
    problems(part, document, payload, drafts, index)   # [] = the response is a valid draft
    apply(part, document, drafts)            # add a valid draft to `drafts`

Categories are batched (at most MAX_BATCH_CATEGORIES categories and
MAX_BATCH_TEXTS item-text patterns per batch), so a real ledger set takes about
15-25 calls: one for storylines, one catalog and one variants call per batch,
one for vocabulary. Batches follow the payload's category order, so the same
payload always gives the same parts.

Draft shapes (every object is closed: unknown keys are schema errors):

    storylines  {"storylines": [{"name", "description", "month_weights"?,
                                 "burst_days"?, "bursts_per_quarter"?, "quiet_days"?, "parties_per_quarter"?}]}
    catalog     {"items": [{"id", "category", "storyline", "class", "archetype",
                            "goods"?, "decimal"?, "params"?,
                            "sellers": [{"id", "vendor": name | null}]}]}
    variants    {"items": [{"id", "descriptive": [...], "terse": [...],
                            "vendor": [{"seller", "text": "Vendor - item"}]}]}
    vocabulary  {"date_tails": ["({mon} {d}, {yyyy})", ...]}

No draft carries a price or a pack size: no key names one, and no text states a
peso amount or a literal pack size. A text of a `goods: "stock"` item may say
where the pack size goes with the placeholder `{pcs}` ("Ballpens, box of {pcs}");
the bundle fills it from the ledger's observed pack sizes (ticket 15). Vendor
names are only the payload's vendor names (allowlisted brands or the scrubber's
fabricated names), and a draft that carries a ledger name fails its check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from txns.bundle import packs
from txns.bundle import storylines as storyline_rules
from txns.bundle import vocabulary as vocabulary_rules
from txns.bundle.model import PRICE_CLASSES
from txns.bundle.prices import TIERED_GOODS
from txns.drafting.jsonschema import errors as schema_errors
from txns.errors import BundleInvalid
from txns.privacy import LeakDetector, NameIndex, find_in_object
from txns.writer import text_violations

MAX_BATCH_CATEGORIES = 6
MAX_BATCH_TEXTS = 60
MAX_ITEMS_PER_CATEGORY = 12
MIN_DESCRIPTIVE = 3  # plain plus vendor-prefixed (same rule as txns.bundle.text_rules)
MIN_TERSE = 2
MAX_VARIANTS = 12
COLLISION_SPARE = 2  # extra variants asked of an item that lost one to a name coincidence
SEPARATOR = " - "
PCS = "{pcs}"  # pack-size placeholder, filled by the bundle (never by the LLM)
EXAMPLE_TEXTS = 40  # item-text patterns shown to the vocabulary call

# The seven archetypes (FR-E3) and the item params a draft may propose for each.
# Keep these names equal to the engine's registered archetypes (txns.engine.archetypes).
ARCHETYPES: dict[str, str] = {
    "fixed_day_subscription": "charged on a fixed day every month or few months (software, cloud storage, phone plan)",
    "project_burst": "bought in clusters while a project runs, quiet in between (drives for a shoot, location fees)",
    "periodic_top_up": "refilled or topped up about once or a few times a month (prepaid load, water refills)",
    "petty_daily": "small purchases on ordinary days at a steady rate (coffee, parking, courier runs)",
    "deposit_balance": "a deposit followed weeks later by its balance (venue, catering, equipment rental)",
    "one_off_big_ticket": "a rare large purchase on a weekday at a negotiated figure (a camera body, a laptop)",
    "batch_logged": "receipts logged together on a later batch day (reimbursed errands, supply runs)",
}
PARAMS: dict[str, dict[str, tuple[float, float, bool]]] = {  # name -> (min, max, integer)
    "fixed_day_subscription": {"anchor_day": (1, 31, True), "every_months": (1, 12, True)},
    "project_burst": {"per_burst": (0.1, 50, False)},
    "periodic_top_up": {"per_month": (0.1, 31, False)},
    "petty_daily": {"per_week": (0.01, 50, False)},
    "deposit_balance": {"per_quarter": (0.1, 30, False)},
    "one_off_big_ticket": {"per_quarter": (0.1, 30, False)},
    "batch_logged": {"per_week": (0.01, 50, False)},
}
CLASSES = PRICE_CLASSES

# Keys that would carry a price or a pack size; no draft may have one at any depth.
_FORBIDDEN_KEY = re.compile(r"price|amount|cost|peso|centavo|pack|pcs|tier|step|qty|quantit|anchor_price", re.I)
_PRICE_TEXT = re.compile(
    r"₱\s?\d|\bPHP\s?\d|\bPhp\s?\d|(?<![A-Za-z])P\s?\d[\d,]*\.\d\d\b|\b\d[\d,]*(?:\.\d\d)?\s?[Pp]esos?\b"
)
_PACK_TEXT = re.compile(
    r"(?<![\w.,])\d{1,4}[-\s]?(?:pcs|pc|pieces|piece|units|unit|packs|pack|pk|sets|set|boxes|box|bottles"
    r"|rolls|reams|ct|count)(?!\w)|\b(?:pack|box|set|bundle|case|bag)s?\s+of\s+\d",
    re.I,
)
_BRACES = re.compile(r"[{}]")

_ID = {"type": "string", "pattern": r"^[a-z][a-z0-9_]{1,39}$"}
_SELLER_ID = {"type": "string", "pattern": r"^[a-z][a-z0-9-]{1,39}$"}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 100}
# A nullable field is `anyOf` [its schema, _NULL], never a list-valued `type`: Anthropic's
# structured-output validator refuses a type list (with a null enum value) with HTTP 400.
_NULL = {"type": "null"}

SCHEMAS: dict[str, dict[str, Any]] = {
    "storylines": {
        "type": "object",
        "additionalProperties": False,
        "required": ["storylines"],
        "properties": {"storylines": {"type": "array", "minItems": 1, "maxItems": 12, "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "description"],
            "properties": {
                "name": _ID,
                "description": {"type": "string", "minLength": 1, "maxLength": 300},
                "month_weights": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}},
                "burst_days": {"type": "array", "minItems": 2, "maxItems": 2,
                               "items": {"type": "integer", "minimum": 1, "maximum": 92}},
                "bursts_per_quarter": {"type": "number", "minimum": 0, "maximum": 30},
                "quiet_days": {"type": "integer", "minimum": 0, "maximum": 92},
                "parties_per_quarter": {"type": "number", "minimum": 0, "maximum": 30},
            },
        }}},
    },
    "catalog": {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {"items": {"type": "array", "minItems": 1, "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "category", "storyline", "class", "archetype", "sellers"],
            "properties": {
                "id": _ID,
                "category": {"type": "string", "minLength": 1},
                "storyline": _ID,
                "class": {"type": "string", "enum": list(CLASSES)},
                "archetype": {"type": "string", "enum": list(ARCHETYPES)},
                "goods": {"anyOf": [{"type": "string", "enum": list(TIERED_GOODS)}, _NULL]},
                "decimal": {"type": "boolean"},
                "params": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0}},
                "sellers": {"type": "array", "minItems": 1, "maxItems": 4, "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "vendor"],
                    "properties": {"id": _SELLER_ID, "vendor": {"anyOf": [{"type": "string", "minLength": 1}, _NULL]}},
                }},
            },
        }}},
    },
    "variants": {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {"items": {"type": "array", "minItems": 1, "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "descriptive", "terse", "vendor"],
            "properties": {
                "id": _ID,
                "descriptive": {"type": "array", "minItems": 1, "maxItems": MAX_VARIANTS, "items": _TEXT},
                "terse": {"type": "array", "maxItems": MAX_VARIANTS, "items": _TEXT},
                "vendor": {"type": "array", "maxItems": MAX_VARIANTS, "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["seller", "text"],
                    "properties": {"seller": _SELLER_ID, "text": _TEXT},
                }},
            },
        }}},
    },
    "vocabulary": {
        "type": "object",
        "additionalProperties": False,
        "required": ["date_tails"],
        "properties": {"date_tails": {"type": "array", "minItems": 1, "maxItems": 6,
                                      "items": {"type": "string", "minLength": 1, "maxLength": 40}}},
    },
}

_COMMON = (
    "You draft part of a bundle for a generator of realistic spend-ledger rows for a small "
    "post-production studio in the Philippines. The input is JSON: aggregates and item-text "
    "patterns from the studio's own books, with every vendor and person name already replaced. "
    "Answer with one JSON document that matches the given JSON schema exactly, and nothing else. "
    "Never give a price, an amount, a cost or a pack size anywhere: prices come from the books, not from you. "
)
INSTRUCTIONS: dict[str, str] = {
    "storylines": _COMMON + (
        "Propose the storylines: the few recurring reasons this studio spends (for example office pantry, "
        "errands, the software stack, post-production projects, the year-end party). Give each a snake_case "
        "name and a one-sentence description. A seasonal storyline gives its off-season months weight 0 in "
        "month_weights (keys \"1\" to \"12\"); a project storyline may give burst_days [min, max], "
        "bursts_per_quarter and quiet_days; a party storyline gives parties_per_quarter (its project_burst "
        "items then fall on party days, several rows a day). Leave out settings you have no reason to set."
    ),
    "catalog": _COMMON + (
        "Propose catalog items for every category in the input, at least one per category and at most "
        f"{MAX_ITEMS_PER_CATEGORY}: the distinct things the studio buys there, judged from the item texts. "
        "Each item has a snake_case id not in taken_item_ids, its category (copied exactly), a storyline from "
        "the input, a class (subscription, retail or big_ticket), one archetype from the archetypes list, and "
        "its sellers. goods is \"stock\" for consumables bought in packs, \"hardware\" for equipment, else "
        "leave it out; decimal is true only for items sold by measure (litres, metres, hours). "
        "params may propose the item's schedule only: " + "; ".join(
            f"{a}: {', '.join(p) or 'none'}" for a, p in PARAMS.items()) + ". "
        "A seller has a short kebab-case id (for example courier-1) and a vendor name, or null for an "
        "unnamed seller. Vendor names must be copied exactly from the category vendors in the input; never "
        "invent one. Reuse known_sellers' ids for the same vendor, and give every vendor one id only."
    ),
    "variants": _COMMON + (
        "Write the item text variants for every item in the input, in the voice of the item texts shown "
        "for its category: short, plain, ASCII, at most 100 characters. Per item: at least "
        f"{MIN_DESCRIPTIVE} descriptive variants that name the thing bought, and at least {MIN_TERSE} terse "
        "ones (a word or two a hurried bookkeeper would type); big_ticket items get descriptive variants "
        "only and an empty terse list. vendor holds a minority of vendor-prefixed descriptive variants, each "
        "\"Vendor - item\" for one of the item's own sellers that has a vendor name, spelled exactly as given. "
        "Plain descriptive and terse text carries no vendor and no \" - \". Every descriptive text belongs to "
        "one item only. Never state a price or a pack size; for a stock item a variant may mark where the "
        f"pack size goes with {PCS} (\"Ballpens, box of {PCS}\")."
    ),
    "vocabulary": _COMMON + (
        "Propose the date-tail formats a bookkeeper appends when logging a receipt days after the purchase "
        "(\"Delivery fee (Oct 28, 2026)\"). Placeholders: {d} {dd} day, {m} {mm} month number, {mon} {month} "
        "month name, {yy} {yyyy} year. Each format names the day and the month, is printable ASCII without "
        f"\" - \", and renders to at most {vocabulary_rules.MAX_TAIL} characters. Match the examples' habits."
    ),
}


@dataclass(frozen=True)
class Part:
    name: str  # storylines | catalog-NN | variants-NN | vocabulary
    kind: str  # storylines | catalog | variants | vocabulary
    categories: tuple[str, ...] = ()  # the batch, for catalog and variants parts


@dataclass
class Drafts:
    """Valid drafts so far, in part order."""

    storylines: list[dict] = field(default_factory=list)
    items: dict[str, dict] = field(default_factory=dict)  # item id -> catalog entry (with category)
    sellers: dict[str, str | None] = field(default_factory=dict)  # seller id -> vendor name
    variants: dict[str, dict] = field(default_factory=dict)  # item id -> {descriptive, terse, vendor}
    vocabulary: dict | None = None
    served_models: dict[str, str] = field(default_factory=dict)  # part -> model the provider reported
    costs: dict[str, float] = field(default_factory=dict)  # part -> cost in USD


def batches(payload: Mapping[str, Any]) -> list[tuple[str, ...]]:
    out: list[list[str]] = []
    texts = 0
    for cat in payload["categories"]:
        n = len(cat.get("item_texts", ()))
        if not out or len(out[-1]) >= MAX_BATCH_CATEGORIES or (out[-1] and texts + n > MAX_BATCH_TEXTS):
            out.append([])
            texts = 0
        out[-1].append(cat["category"])
        texts += n
    return [tuple(b) for b in out]


def plan(payload: Mapping[str, Any]) -> list[Part]:
    groups = batches(payload)
    return [
        Part("storylines", "storylines"),
        *(Part(f"catalog-{i:02d}", "catalog", g) for i, g in enumerate(groups, 1)),
        *(Part(f"variants-{i:02d}", "variants", g) for i, g in enumerate(groups, 1)),
        Part("vocabulary", "vocabulary"),
    ]


def _categories(payload: Mapping[str, Any], names: tuple[str, ...]) -> list[dict]:
    by_name = {c["category"]: c for c in payload["categories"]}
    return [by_name[n] for n in names]


def payload_vendors(payload: Mapping[str, Any]) -> set[str]:
    """Vendor names a draft may use: the payload's (allowlisted brands or fabricated names)."""
    return {v["name"] for c in payload["categories"] for v in c.get("vendors", ())}


def request_input(part: Part, payload: Mapping[str, Any], drafts: Drafts) -> dict[str, Any]:
    """The data a call carries: extracts of the scrubbed payload plus earlier drafts, nothing else."""
    if part.kind == "storylines":
        return {
            "currency": payload.get("currency"),
            "quarters": payload.get("quarters", {}),
            "categories": [
                {k: c.get(k) for k in ("category", "subscription", "rows", "spend", "textless_rows")}
                | {"item_texts": c.get("item_texts", [])[:10]}
                for c in payload["categories"]
            ],
        }
    if part.kind == "catalog":
        return {
            "categories": _categories(payload, part.categories),
            "storylines": [{"name": s["name"], "description": s["description"]} for s in drafts.storylines],
            "archetypes": ARCHETYPES,
            "classes": list(CLASSES),
            "taken_item_ids": sorted(drafts.items),
            "known_sellers": [{"id": s, "vendor": v} for s, v in sorted(drafts.sellers.items())],
        }
    if part.kind == "variants":
        return {
            "categories": [
                {k: c.get(k) for k in ("category", "rows", "textless_rows", "item_texts")}
                for c in _categories(payload, part.categories)
            ],
            "items": [
                {k: item[k] for k in ("id", "category", "class", "goods", "sellers") if k in item}
                for item in drafts.items.values()
                if item["category"] in part.categories
            ],
        }
    if part.kind == "vocabulary":
        texts = [t["text"] for c in payload["categories"] for t in c.get("item_texts", ())]
        with_numbers = [t for t in texts if re.search(r"\d", t)]  # where dates would show
        return {"example_item_texts": (with_numbers or texts)[:EXAMPLE_TEXTS]}
    raise ValueError(part.kind)


def _forbidden_keys(v: Any, path: str = "$") -> list[str]:
    out = []
    if isinstance(v, dict):
        for k, x in v.items():
            if _FORBIDDEN_KEY.search(str(k)):
                out.append(f"{path}.{k}: drafts never carry a price or a pack size")
            out.extend(_forbidden_keys(x, f"{path}.{k}"))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            out.extend(_forbidden_keys(x, f"{path}[{i}]"))
    return out


def without_collisions(part: Part, document: Any, index: NameIndex) -> tuple[Any, list[tuple[int, str, int]]]:
    """A variants answer minus the texts that match a ledger name, and which they were.

        cleaned, dropped = without_collisions(part, document, index)   # dropped: [(item, list, position), ...]

    The model is never shown a ledger name, so a match in its answer is a coincidence: a plain
    word the books also use as a name (a ledger description with no " - " is read as a vendor
    name). Dropping the one text keeps the rest of an otherwise valid answer; nothing about
    the check is relaxed, because `problems` still leak-checks what remains and still applies
    every count, uniqueness and text rule (an item left short is a problem like any other).
    Only the texts of variants answers are dropped: a name anywhere else fails the draft.
    Positions are those of the answer as the model gave it.
    """
    items = document.get("items") if part.kind == "variants" and isinstance(document, dict) else None
    if not isinstance(items, list):
        return document, []
    detector = LeakDetector(index)
    dropped: list[tuple[int, str, int]] = []
    cleaned = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        item = dict(item)
        for kind in ("descriptive", "terse", "vendor"):
            if not isinstance(item.get(kind), list):
                continue
            kept = []
            for j, entry in enumerate(item[kind]):
                text = entry.get("text") if isinstance(entry, dict) else entry
                if isinstance(text, str) and detector.leaks(text):
                    dropped.append((i, kind, j))
                else:
                    kept.append(entry)
            item[kind] = kept
        cleaned.append(item)
    return ({**document, "items": cleaned}, dropped) if dropped else (document, [])


def collision_where(dropped: list[tuple[int, str, int]]) -> list[str]:
    """JSON paths of the dropped texts in the answer as given (location only)."""
    return [f"$.items[{i}].{kind}[{j}]" for i, kind, j in dropped]


def collision_problems(document: Any, dropped: list[tuple[int, str, int]]) -> list[str]:
    """Re-ask lines for the items that lost texts, by item location and id only (never the text)."""
    counts: dict[int, int] = {}
    for i, _, _ in dropped:
        counts[i] = counts.get(i, 0) + 1
    out = []
    for i, n in counts.items():
        name = document["items"][i].get("id")
        label = f" (`{name}`)" if isinstance(name, str) and re.fullmatch(_ID["pattern"], name) else ""
        out.append(
            f"$.items[{i}]{label}: {n} variant text{'s' if n != 1 else ''} removed because the wording "
            "coincides with a business name from the books; word this item differently, and give it "
            f"{COLLISION_SPARE} more descriptive variants than it needs (and {COLLISION_SPARE} more terse ones "
            "if it has terse variants) so one more coincidence can be dropped"
        )
    return out


def problems(part: Part, document: Any, payload: Mapping[str, Any], drafts: Drafts, index: NameIndex) -> list[str]:
    """Why `document` is not a valid draft for `part` ([] when it is)."""
    # A draft carries no ledger name outside the allowlist (FR-C2, FR-D2 gate 3). Checked
    # first and reported alone, by location only, so no message quotes the name.
    leaks = find_in_object(document, index)
    if leaks:
        return [f"{where}: carries a ledger name that is not on the brand allowlist" for where in leaks]
    bad = _forbidden_keys(document) + schema_errors(document, SCHEMAS[part.kind])
    if bad:
        return bad
    return {
        "storylines": _storyline_problems,
        "catalog": _catalog_problems,
        "variants": _variant_problems,
        "vocabulary": _vocabulary_problems,
    }[part.kind](part, document, payload, drafts)


def _storyline_problems(part, doc, payload, drafts) -> list[str]:
    out, seen = [], set()
    for i, s in enumerate(doc["storylines"]):
        if s["name"] in seen:
            out.append(f"$.storylines[{i}]: storyline `{s['name']}` listed twice")
        seen.add(s["name"])
        try:
            storyline_rules.check_settings(f"storyline `{s['name']}`", s)
        except BundleInvalid as exc:
            out.append(f"$.storylines[{i}]: {str(exc).removeprefix('bundle invalid: ')}")
    return out


def _catalog_problems(part, doc, payload, drafts) -> list[str]:
    out = []
    names = {s["name"] for s in drafts.storylines}
    allowed_vendors = payload_vendors(payload)
    sellers = dict(drafts.sellers)
    seen: set[str] = set()
    per_category = {c: 0 for c in part.categories}
    for i, item in enumerate(doc["items"]):
        where = f"$.items[{i}]"
        if item["id"] in drafts.items or item["id"] in seen:
            out.append(f"{where}: item id `{item['id']}` is already taken")
        seen.add(item["id"])
        if item["category"] not in per_category:
            out.append(f"{where}: category {item['category']!r} is not one of this call's categories")
        else:
            per_category[item["category"]] += 1
        if item["storyline"] not in names:
            out.append(f"{where}: storyline `{item['storyline']}` is not a drafted storyline")
        allowed = PARAMS[item["archetype"]]
        for key, value in item.get("params", {}).items():
            if key not in allowed:
                out.append(f"{where}.params: `{key}` is not a param of {item['archetype']} "
                           f"(allowed: {', '.join(allowed) or 'none'})")
                continue
            lo, hi, integer = allowed[key]
            if (integer and not isinstance(value, int)) or not lo <= value <= hi:
                kind = "a whole number" if integer else "a number"
                out.append(f"{where}.params.{key}: must be {kind} from {lo} to {hi}")
        if item.get("decimal") and item["class"] != "retail":
            out.append(f"{where}: only retail items may be decimal")
        own = set()
        for j, s in enumerate(item["sellers"]):
            w = f"{where}.sellers[{j}]"
            if s["id"] in own:
                out.append(f"{w}: seller `{s['id']}` listed twice")
            own.add(s["id"])
            if s["vendor"] is not None and s["vendor"] not in allowed_vendors:
                out.append(f"{w}: vendor {s['vendor']!r} is not an allowlisted or fabricated vendor from the input")
            if s["id"] in sellers and sellers[s["id"]] != s["vendor"]:
                out.append(f"{w}: seller `{s['id']}` already stands for {sellers[s['id']]!r}")
            sellers.setdefault(s["id"], s["vendor"])
    for cat, n in per_category.items():
        if n == 0:
            out.append(f"$.items: no item for category {cat!r}")
        elif n > MAX_ITEMS_PER_CATEGORY:
            out.append(f"$.items: {n} items for category {cat!r}, at most {MAX_ITEMS_PER_CATEGORY}")
    return out


def _text_problems(where: str, text: str, item: Mapping[str, Any], names: set[str]) -> list[str]:
    out = []
    if text.strip().casefold() in names:
        return [f"{where}: {text!r} is a vendor or seller only; name the thing bought"]
    if _PRICE_TEXT.search(text):
        out.append(f"{where}: {text!r} states a price")
    rendered = text
    if PCS in text:
        rendered = text.replace(PCS, "9999")
        if item.get("goods") != packs.PACK_GOODS:
            out.append(f"{where}: {text!r} uses {PCS} on an item whose goods is not \"stock\"")
        elif text.count(PCS) > 1 or not packs.states_pack(rendered, 9999):
            out.append(f"{where}: {text!r} must state the pack once as pieces, \"pack of {PCS}\" or \"box of {PCS}\"")
    if _BRACES.search(text.replace(PCS, "")):
        out.append(f"{where}: {text!r} has a brace outside the {PCS} placeholder")
    if _PACK_TEXT.search(text.replace(PCS, "")):
        out.append(f"{where}: {text!r} states a pack size or quantity; use {PCS} for a stock item's pack size")
    out.extend(f"{where}: {text!r}: {p}" for p in text_violations(rendered))
    return out


def _variant_problems(part, doc, payload, drafts) -> list[str]:
    out = []
    batch = {i: item for i, item in drafts.items.items() if item["category"] in part.categories}
    names = {v.casefold() for v in payload_vendors(payload)} | {s.casefold() for s in drafts.sellers}
    ids = [x["id"] for x in doc["items"]]
    missing = [i for i in batch if i not in ids]
    if missing:
        out.append(f"$.items: no variants for {', '.join(f'`{i}`' for i in missing)}")
    uses: dict[str, set[tuple[str, bool]]] = {}  # text -> {(item id, is terse)}, earlier drafts included
    for item_id, v in drafts.variants.items():
        for text in v["descriptive"] + [x["text"] for x in v["vendor"]]:
            uses.setdefault(text, set()).add((item_id, False))
        for text in v["terse"]:
            uses.setdefault(text, set()).add((item_id, True))
    seen_ids = set()
    for i, v in enumerate(doc["items"]):
        where = f"$.items[{i}]"
        item = batch.get(v["id"])
        if item is None:
            out.append(f"{where}: `{v['id']}` is not a catalog item of this call")
            continue
        if v["id"] in seen_ids:
            out.append(f"{where}: `{v['id']}` listed twice")
            continue
        seen_ids.add(v["id"])
        n_desc = len(v["descriptive"]) + len(v["vendor"])
        if n_desc < MIN_DESCRIPTIVE:
            out.append(f"{where}: {n_desc} descriptive variant(s), needs at least {MIN_DESCRIPTIVE}")
        if v["vendor"] and len(v["vendor"]) >= len(v["descriptive"]):
            out.append(f"{where}: vendor-prefixed variants must be a minority of the descriptive ones")
        if item["class"] == "big_ticket":
            if v["terse"]:
                out.append(f"{where}: big-ticket items get descriptive variants only, no terse ones")
        elif len(v["terse"]) < MIN_TERSE:
            out.append(f"{where}: {len(v['terse'])} terse variant(s), needs at least {MIN_TERSE}")

        own_sellers = {s["id"]: s["vendor"] for s in item["sellers"]}
        texts: list[tuple[str, bool]] = []
        for j, x in enumerate(v["vendor"]):
            w = f"{where}.vendor[{j}]"
            head, sep, rest = x["text"].partition(SEPARATOR)
            vendor = own_sellers.get(x["seller"])
            if x["seller"] not in own_sellers:
                out.append(f"{w}: seller `{x['seller']}` does not sell `{v['id']}`")
            elif vendor is None:
                out.append(f"{w}: seller `{x['seller']}` has no vendor name, so it takes no vendor prefix")
            elif not sep or head != vendor or not rest.strip():
                out.append(f"{w}: {x['text']!r} must read {vendor + SEPARATOR + '<item>'!r}")
            else:
                out.extend(_text_problems(w, rest.strip(), item, names))
                out.extend(f"{w}: {x['text']!r}: {p}" for p in text_violations(x["text"].replace(PCS, "9999")))
            texts.append((x["text"], False))
        for kind in ("descriptive", "terse"):
            for j, text in enumerate(v[kind]):
                w = f"{where}.{kind}[{j}]"
                head, sep, _ = text.partition(SEPARATOR)
                if kind == "terse" and sep:
                    out.append(f"{w}: terse text {text!r} carries \"{SEPARATOR.strip()}\"")
                elif sep and head.strip().casefold() in names:
                    out.append(f"{w}: {text!r} carries a vendor; vendor-prefixed text goes in `vendor`")
                out.extend(_text_problems(w, text, item, names))
                texts.append((text, kind == "terse"))
        seen = set()
        for text, terse in texts:
            if text in seen:
                out.append(f"{where}: variant {text!r} listed twice")
            seen.add(text)
            uses.setdefault(text, set()).add((v["id"], terse))
    # A string belongs to one item; only terse strings may be shared (gate 5 then
    # allows the share only between items with identical price points).
    for text, users in sorted(uses.items()):
        items = sorted({u for u, _ in users})
        if len(items) > 1 and not all(terse for _, terse in users) and any(u in seen_ids for u in items):
            out.append(f"$.items: {text!r} is used by {', '.join(f'`{u}`' for u in items)}; "
                       "only a terse text may be shared between items")
    return out


def _vocabulary_problems(part, doc, payload, drafts) -> list[str]:
    return [f"$.date_tails: {p}" for p in vocabulary_rules.problems(doc)]


def apply(part: Part, doc: Mapping[str, Any], drafts: Drafts) -> None:
    if part.kind == "storylines":
        drafts.storylines = list(doc["storylines"])
    elif part.kind == "catalog":
        for item in doc["items"]:
            drafts.items[item["id"]] = dict(item)
            for s in item["sellers"]:
                drafts.sellers.setdefault(s["id"], s["vendor"])
    elif part.kind == "variants":
        for v in doc["items"]:
            drafts.variants[v["id"]] = {"descriptive": list(v["descriptive"]), "terse": list(v["terse"]),
                                        "vendor": [dict(x) for x in v["vendor"]]}
    elif part.kind == "vocabulary":
        drafts.vocabulary = dict(doc)
