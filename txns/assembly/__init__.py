"""Bundle assembly for `author` (FR-D1, Flow 1 "assemble bundle").

    rules = load_rules(cwd)                       # inputs/bundle-rules.json (committed rule defaults)
    anchors = anchors.load(cwd)                   # inputs/price-anchors.json (committed price anchors)
    built = assemble(drafts, derived, rules=rules, anchors=anchors, calendar=holidays.load_committed(cwd),
                     label=..., model=..., payload_hash=...)
    write(built.files, folder)                    # one JSON file per stem: the bundle, before the gates
    built.left_out                                # drafted items with no anchor (reported, not bundled)

The bundle's files and where each comes from:

    manifest.json    label, pinned model slug, served models per draft part, ledger
                     hashes, interpreter and generator versions, `reviewed: false`
                     (`hash` and `promotion` are added by `store.promote`)
    catalog.json     drafted items (storyline, category, class, archetype, params with
                     a subscription's missing anchor day filled from the committed
                     `anchor_days`) plus the price fields pricing sets (goods, pack_pcs,
                     round_figures)
    rate_cards.json  `txns.assembly.pricing`: anchors only, never the LLM
    text.json        drafted variants; `{pcs}` filled with the item's pack size (a
                     variant needing one the item lacks is dropped), vendor variants
                     only for sellers with a price point, a terse string shared by
                     items with different prices kept by the first item only
    storylines.json  drafted storylines
    rules.json       the committed rule defaults, unchanged
    holidays.json    the committed calendar for the ledgers' first year onwards
    reference.json   the ledger statistics, unchanged
    vocabulary.json  drafted date-tail formats
    anchors.json     per item, where its prices come from (committed anchors in full,
                     so `approve` can re-check them offline) and the items left out

An item that cannot be priced, or whose text falls below the minimums once
assembly dropped variants it cannot use, is left out and listed; nothing
here fails the run except a draft with no item left at all (exit 4).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from txns import holidays, versions
from txns.assembly import anchors as anchor_file
from txns.assembly import pricing
from txns.bundle import text_rules
from txns.canonical import canonical_json, pretty_json, sha256_hex
from txns.errors import BundleInvalid, MissingInput

RULES_PATH = Path("inputs") / "bundle-rules.json"  # committed rule defaults, relative to the working folder
BUNDLE_FORMAT = 1
PCS = "{pcs}"
SUBSCRIPTION = "fixed_day_subscription"
DEFAULT_ANCHOR_DAYS = (1,)


@dataclass(frozen=True)
class LeftOut:
    id: str
    category: str
    reason: str


@dataclass
class Assembly:
    files: dict[str, Any]  # stem -> JSON value
    left_out: list[LeftOut] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # per-item adjustments worth a line in the report
    bases: dict[str, int] = field(default_factory=dict)  # pricing basis -> items


def load_rules(root: Path) -> dict[str, Any]:
    path = root / RULES_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MissingInput(f"committed rule defaults not found: {RULES_PATH.as_posix()}") from None
    except (OSError, ValueError) as exc:
        raise MissingInput(f"{RULES_PATH.as_posix()}: unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise MissingInput(f"{RULES_PATH.as_posix()}: must be a JSON object")
    return data


def bundle_years(calendar: Mapping[str, Any], first_year: int) -> list[int]:
    """Calendar years a bundle carries: the committed ones from the ledgers' first year on."""
    years = sorted(int(y) for y in calendar.get("years", {}))
    return [y for y in years if y >= first_year] or years


def _anchor_day(item_id: str, rules: Mapping[str, Any]) -> int:
    days = (rules.get("archetypes", {}).get(SUBSCRIPTION, {}) or {}).get("anchor_days") or DEFAULT_ANCHOR_DAYS
    return int(days[int(sha256_hex(item_id)[:8], 16) % len(days)])


def _render(texts: Sequence[str], pcs: int | None) -> tuple[list[str], int]:
    """`{pcs}` filled with the pack size; variants needing one dropped when there is none."""
    out, dropped = [], 0
    for t in texts:
        if PCS in t:
            if pcs is None:
                dropped += 1
                continue
            t = t.replace(PCS, str(pcs))
        out.append(t)
    return out, dropped


def _text_shortfall(item_class: str, text: Mapping[str, Any]) -> str | None:
    n_desc = len(text["descriptive"]) + len(text.get("vendor", []))
    if n_desc < text_rules.MIN_DESCRIPTIVE:
        return f"{n_desc} descriptive variant(s) left, needs {text_rules.MIN_DESCRIPTIVE}"
    if text.get("vendor") and len(text["vendor"]) >= len(text["descriptive"]):
        return "vendor-prefixed variants would no longer be a minority"
    if item_class != "big_ticket" and len(text["terse"]) < text_rules.MIN_TERSE:
        return f"{len(text['terse'])} terse variant(s) left, needs {text_rules.MIN_TERSE}"
    return None


def assemble(
    drafts,
    derived,
    *,
    rules: Mapping[str, Any],
    anchors: Sequence[anchor_file.Anchor],
    calendar: Mapping[str, Any],
    label: str,
    model: str | None,
    payload_hash: str,
) -> Assembly:
    built = Assembly(files={})
    first = min(l.start for l in derived.ledgers)
    last = max(l.end for l in derived.ledgers)
    years = bundle_years(calendar, first.year)
    try:
        holiday_data = holidays.for_years(calendar, years)
    except holidays.CalendarInvalid as exc:  # pragma: no cover - years come from the calendar itself
        raise MissingInput(str(exc)) from None
    storylines = {s["name"]: {k: v for k, v in s.items() if k != "name"} for s in drafts.storylines}
    pricing_rules = rules.get("pricing") or {}
    denied = text_rules.denied_patterns(rules)  # non-sensical item text never enters a bundle
    step_years = pricing.step_years(last, years)

    evidence, missing = pricing.gather_evidence(drafts.items, drafts.variants, derived.rows, derived.reference, anchors)
    catalog, cards, text, records = {}, {}, {}, {}
    for item_id in sorted(drafts.items):
        draft = drafts.items[item_id]
        if item_id in missing:
            built.left_out.append(LeftOut(item_id, draft["category"], missing[item_id]))
            continue
        priced = pricing.price_item(item_id, draft, evidence[item_id], rules=pricing_rules,
                                    storylines=storylines, step_years=step_years)
        if isinstance(priced, str):
            built.left_out.append(LeftOut(item_id, draft["category"], priced))
            continue
        variants = drafts.variants.get(item_id) or {"descriptive": [], "terse": [], "vendor": []}
        pcs = priced.catalog.get("pack_pcs")
        descriptive, d1 = _render(variants["descriptive"], pcs)
        terse, d2 = _render(variants["terse"], pcs)
        sellers = {s["id"] for s in priced.sellers}
        vendor = []
        for v in variants.get("vendor", []):
            rendered, _ = _render([v["text"]], pcs)
            if rendered and v["seller"] in sellers:
                vendor.append({"seller": v["seller"], "text": rendered[0]})
        n_before = len(descriptive) + len(terse) + len(vendor)
        descriptive = list(text_rules.allowed_texts(descriptive, denied))
        terse = list(text_rules.allowed_texts(terse, denied))
        vendor = [v for v in vendor if not text_rules.is_denied_text(v["text"], denied)]
        n_denied = n_before - len(descriptive) - len(terse) - len(vendor)
        entry_text = {"descriptive": descriptive, "terse": terse}
        if vendor:
            entry_text["vendor"] = vendor
        if d1 or d2 or n_denied:
            short = _text_shortfall(draft["class"], entry_text)
            if short:
                why = [f"no pack size for its {PCS} variants"] if d1 or d2 else []
                why += [f"{n_denied} variant(s) match `{text_rules.DENIED_KEY}`"] if n_denied else []
                built.left_out.append(LeftOut(item_id, draft["category"],
                                              f"{' and '.join(why)}, and without them {short}"))
                continue
        if n_denied:
            built.notes.append(f"item `{item_id}`: dropped {n_denied} variant(s) matching `{text_rules.DENIED_KEY}`")
        params = dict(draft.get("params") or {})
        if draft["archetype"] == SUBSCRIPTION and "anchor_day" not in params:
            params["anchor_day"] = _anchor_day(item_id, rules)
        entry = {k: draft[k] for k in ("storyline", "category", "class", "archetype")}
        entry["params"] = params
        entry.update(priced.catalog)
        catalog[item_id] = entry
        cards[item_id] = priced.card
        text[item_id] = entry_text
        records[item_id] = priced.record
        built.notes.extend(f"item `{item_id}`: {n}" for n in priced.notes)

    _share_terse(catalog, cards, text, records, built)
    for rec in records.values():
        built.bases[rec["basis"]] = built.bases.get(rec["basis"], 0) + 1
    if not catalog:
        raise BundleInvalid("bundle assembly: no drafted item could be priced from an anchor; nothing to promote "
                            f"({len(built.left_out)} item(s) left out)")

    built.files = {
        "manifest": {
            "format": BUNDLE_FORMAT,
            "label": label,
            "note": "Drafted by `txns author`; review it, then run `txns approve`.",
            "generator": versions.generator(),
            "interpreter": versions.interpreter(),
            "model": model,
            "served_models": dict(sorted(drafts.served_models.items())),
            "payload_hash": payload_hash,
            "ledger_hashes": dict(sorted(derived.ledger_hashes.items())),
            "reviewed": False,
        },
        "catalog": {"items": catalog},
        "rate_cards": cards,
        "text": text,
        "storylines": storylines,
        "rules": dict(rules),
        "holidays": holiday_data,
        "reference": derived.reference,
        "anchors": {
            "note": "Where each item's prices come from (txns author). basis: committed = inputs/price-anchors.json "
                    "entry (copied); item text = ledger anchor prices of rows whose text names the item; category = "
                    "the category's remaining anchor figures. left_out: drafted items with no anchor.",
            "items": records,
            "left_out": [{"id": x.id, "category": x.category, "reason": x.reason} for x in built.left_out],
        },
    }
    if drafts.vocabulary:
        built.files["vocabulary"] = dict(drafts.vocabulary)
    return built


def _share_terse(catalog: dict, cards: dict, text: dict, records: dict, built: Assembly) -> None:
    """A terse string may be shared only by items with identical price points (FR-D2 gate 5):
    otherwise the first item keeps it and the others drop it (an item left short is left out)."""
    owners: dict[str, list[str]] = defaultdict(list)
    for item_id in sorted(text):
        for t in text[item_id]["terse"]:
            owners[t].append(item_id)
    short: set[str] = set()
    for t, ids in sorted(owners.items()):
        if len(ids) < 2:
            continue
        keep = canonical_json(cards[ids[0]]["points"])
        for item_id in ids[1:]:
            if canonical_json(cards[item_id]["points"]) != keep:
                text[item_id]["terse"].remove(t)
                if _text_shortfall(catalog[item_id]["class"], text[item_id]):
                    short.add(item_id)
    for item_id in sorted(short):
        built.left_out.append(LeftOut(item_id, catalog[item_id]["category"],
                                      "its terse text is another item's (different prices), too few terse variants left"))
        for table in (catalog, cards, text, records):
            del table[item_id]


def write(files: Mapping[str, Any], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=False)
    for stem, value in sorted(files.items()):
        (folder / f"{stem}.json").write_text(pretty_json(value), encoding="utf-8", newline="\n")


__all__ = ["Assembly", "LeftOut", "RULES_PATH", "assemble", "bundle_years", "load_rules", "write"]
