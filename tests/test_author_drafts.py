"""`txns author` drafts the catalog, item text and vocabulary (ticket 14: FR-C3, FR-C7, FR-D2 gate 1).

Driven through `main(argv)` with the scripted LLM fake (tests/llm_fake.py): no
network. The fixture ledgers give 8 categories in 2 batches, so a full run is 6
calls: storylines, catalog-01, catalog-02, variants-01, variants-02, vocabulary.
"""

import hashlib
import io
import json
import unittest
from datetime import date
from unittest import mock

from tests.helpers import Workspace
from tests.llm_fake import Fail, Reply, ScriptedLLM
from tests.test_author_ledger import KEY
from tests.test_author_scrub import BLOCKED, DISTINCTIVE_WORDS
from txns.cli import main
from txns.drafting import parts
from txns.engine import archetypes

AUTHOR_DIR = ".txns/author"
PARTS = ["storylines", "catalog-01", "catalog-02", "variants-01", "variants-02", "vocabulary"]
MODEL = "example/pinned-model"


class DraftCase(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_author_inputs()
        self.ws.write_config(f'[author]\nmodel = "{MODEL}"\n')

    def author(self, fake=None):
        return self.ws.run("author", env=KEY, transport=fake)

    def ok(self, fake=None):
        r = self.author(fake)
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        return r

    def payload_hash(self) -> str:
        return hashlib.sha256((self.ws.cwd / AUTHOR_DIR / "payload.json").read_bytes()).hexdigest()

    def drafts_dir(self):
        return self.ws.cwd / AUTHOR_DIR / "drafts" / self.payload_hash()

    def saved(self, part: str) -> dict:
        return json.loads((self.drafts_dir() / f"{part}.json").read_text(encoding="utf-8"))

    def saved_parts(self) -> list[str]:
        return sorted(p.stem for p in self.drafts_dir().glob("*.json"))

    def catalog(self) -> list[dict]:
        return [i for p in PARTS if p.startswith("catalog") for i in self.saved(p)["draft"]["items"]]

    def variants(self) -> dict[str, dict]:
        return {v["id"]: v for p in PARTS if p.startswith("variants") for v in self.saved(p)["draft"]["items"]}


class ConnectionTest(DraftCase):
    def test_one_connection_gets_every_part_in_order(self):
        r = self.ok()
        self.assertEqual(self.ws.llm.parts(), PARTS)
        for req in self.ws.llm.requests:
            self.assertEqual(req.model, MODEL)  # the pinned slug goes with every request
            self.assertTrue(req.instructions and req.schema)
        self.assertIn("6 LLM calls this run ($0.0600), 0 parts reused", r.stdout)
        self.assertIn("served by: fake/served-model-1", r.stdout)
        self.assertRegex(r.stdout, r"promoted bundles/draft-[0-9a-f]{12}/ \(reviewed: false\)")

    def test_reported_model_and_cost_are_kept_per_part(self):  # FR-C8, FR-C6
        fake = ScriptedLLM().script("vocabulary", Reply(model="other/served-model-2", cost=0.25))
        r = self.ok(fake)
        self.assertEqual(self.saved("vocabulary")["model"], "other/served-model-2")
        self.assertEqual(self.saved("vocabulary")["cost_usd"], 0.25)
        self.assertEqual(self.saved("storylines")["model"], "fake/served-model-1")
        self.assertIn("($0.3000)", r.stdout)
        self.assertIn("served by: fake/served-model-1, other/served-model-2", r.stdout)

    def test_failed_call_exits_3_and_keeps_earlier_drafts(self):
        fake = ScriptedLLM().script("catalog-02", *[Fail("connection reset")] * 4)
        r = self.author(fake)
        self.assertEqual(r.code, 3, r.stdout + r.stderr)
        self.assertIn("LLM call for draft `catalog-02` failed after 4 attempts: connection reset", r.stderr)
        self.assertIn("2 valid drafts kept", r.stderr)
        self.assertEqual(fake.parts(), ["storylines", "catalog-01"] + ["catalog-02"] * 4)  # 1 + 3 retries (T34)
        self.assertEqual(self.saved_parts(), ["catalog-01", "storylines"])
        self.assertFalse((self.ws.cwd / "bundles").exists())

    def test_without_an_injected_transport_and_no_model_author_exits_2(self):
        # The real OpenRouter connection needs a pinned slug (tests/test_author_openrouter.py drives it).
        self.ws.write_config("")
        out, err = io.StringIO(), io.StringIO()
        got = main(["author"], today=date(2026, 10, 5), env=KEY, cwd=self.ws.cwd, stdout=out, stderr=err)
        self.assertEqual(got, 2, err.getvalue())
        self.assertIn("`author.model` is not set", err.getvalue())
        self.assertTrue((self.ws.cwd / AUTHOR_DIR / "payload.json").exists())

    def test_a_fully_drafted_payload_needs_no_connection(self):
        self.ok()
        again = ScriptedLLM()
        r = self.ok(again)
        self.assertEqual(again.requests, [])
        self.assertIn("0 LLM calls this run ($0.0000), 6 parts reused from saved drafts", r.stdout)


class DraftShapeTest(DraftCase):
    def setUp(self):
        super().setUp()
        self.r = self.ok()

    def test_drafts_are_saved_in_the_temp_folder_keyed_by_payload_hash(self):  # FR-C7
        self.assertEqual(self.saved_parts(), sorted(PARTS))
        self.assertIn(f"wrote {AUTHOR_DIR}/drafts/{self.payload_hash()}/ (6 parts)", self.r.stdout)
        for part in PARTS:
            saved = self.saved(part)
            self.assertEqual(set(saved), {"part", "model", "cost_usd", "draft"})
            self.assertEqual(saved["part"], part)

    def test_catalog_items_per_storyline_and_category(self):  # FR-C3
        storylines = {s["name"] for s in self.saved("storylines")["draft"]["storylines"]}
        payload = json.loads((self.ws.cwd / AUTHOR_DIR / "payload.json").read_text(encoding="utf-8"))
        categories = [c["category"] for c in payload["categories"]]
        items = self.catalog()
        self.assertEqual(sorted({i["category"] for i in items}), sorted(categories))
        for item in items:
            self.assertIn(item["storyline"], storylines)
            self.assertIn(item["archetype"], parts.ARCHETYPES)
            self.assertTrue(item["sellers"])

    def test_variants_per_item(self):  # FR-C3
        classes = {i["id"]: i["class"] for i in self.catalog()}
        variants = self.variants()
        self.assertEqual(sorted(variants), sorted(classes))
        self.assertIn("big_ticket", classes.values())
        for item_id, v in variants.items():
            self.assertGreaterEqual(len(v["descriptive"]) + len(v["vendor"]), 3)
            if classes[item_id] == "big_ticket":
                self.assertEqual(v["terse"], [])
            else:
                self.assertGreaterEqual(len(v["terse"]), 2)
        self.assertEqual(self.saved("vocabulary")["draft"], {"date_tails": ["({mon} {d}, {yyyy})", "for {m}/{d}"]})

    def test_no_draft_carries_a_price_or_a_pack_size(self):  # FR-C3
        for part in PARTS:
            text = json.dumps(self.saved(part)["draft"])
            for word in ("price", "amount", "pack_pcs", "tiers", "quantities", "₱"):
                self.assertNotIn(word, text, part)


class RequestPrivacyTest(DraftCase):
    def test_no_ledger_name_reaches_any_request(self):  # T37
        self.ok()
        sent = self.ws.llm.sent_text().casefold()
        for name in BLOCKED + DISTINCTIVE_WORDS:
            self.assertNotIn(name.casefold(), sent, name)
        self.assertNotIn(KEY["OPENROUTER_API_KEY"], self.ws.llm.sent_text())

    def test_requests_carry_only_payload_extracts_and_drafts(self):
        self.ok()
        payload_text = (self.ws.cwd / AUTHOR_DIR / "payload.json").read_text(encoding="utf-8")
        payload = json.loads(payload_text)
        payload_strings = set(_strings(payload))
        draft_strings = set()
        for p in PARTS:
            draft_strings |= set(_strings(self.saved(p)["draft"]))
        fixed = set(_strings({k: parts.request_input(parts.Part("x", "catalog", ()), payload, parts.Drafts())[k]
                              for k in ("archetypes", "classes")}))
        for req in self.ws.llm.requests:
            for s in _strings(req.input):
                self.assertTrue(s in payload_strings or s in draft_strings or s in fixed, f"{req.part}: {s!r}")

    def test_a_request_that_would_carry_a_name_is_not_sent(self):
        real = parts.request_input

        def leaky(part, payload, drafts):
            data = real(part, payload, drafts)
            return data | {"note": "ask Swiftlane Couriers"} if part.kind == "variants" else data

        with mock.patch.object(parts, "request_input", leaky):
            r = self.author()
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn("leak check failed: the `variants-01` request", r.stderr)
        self.assertIn("no LLM call made", r.stderr)
        self.assertNotIn("variants-01", self.ws.llm.parts())
        self.assertNotIn("swiftlane", (r.stdout + r.stderr).casefold())

    def test_a_draft_that_carries_a_ledger_name_fails(self):
        leaky = Reply(edit=lambda d: d["items"][0]["descriptive"].append("Coffee run with Swiftlane Couriers"))
        fake = ScriptedLLM().script("variants-01", leaky, leaky)
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn("carries a ledger name that is not on the brand allowlist", r.stderr)
        self.assertNotIn("swiftlane", (r.stdout + r.stderr).casefold())
        self.assertNotIn("variants-01", self.saved_parts())


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _set(path, value):
    """An edit that sets doc[path...] = value (path keys and indexes)."""
    def edit(doc):
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return edit


def _drop(path):
    def edit(doc):
        target = doc
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return edit


def _first_named(doc):
    """(index, item) of the first variants item with a vendor-prefixed variant."""
    return next((i, v) for i, v in enumerate(doc["items"]) if v["vendor"])


def _vendor_majority(doc):
    i, v = _first_named(doc)
    v["vendor"] = [dict(v["vendor"][0], text=f"{v['vendor'][0]['text']} {n}") for n in ("x", "y", "z")]


def _wrong_vendor(doc):
    i, v = _first_named(doc)
    v["vendor"][0]["text"] = "Pinebrook Trading - " + v["vendor"][0]["text"].split(" - ", 1)[1]


def _share_descriptive(doc):
    doc["items"][1]["descriptive"][0] = doc["items"][0]["descriptive"][0]


def _pcs_on_non_stock(doc):
    item = next(v for v in doc["items"] if not v["descriptive"][-1].endswith("{pcs}"))
    item["descriptive"].append("Masking tape, box of {pcs}")


def _terse_on_big_ticket(doc):
    big = next(v for v in doc["items"] if v["id"] == "sound_system_and_equipment_b")  # the fake's big ticket
    big["terse"] = ["speakers", "gear"]


class SchemaTest(DraftCase):
    """A response that fails its draft schema is re-asked once, then stops the run with exit 4
    (FR-C5, FR-D2 gate 1; the re-ask itself is tested in tests/test_author_openrouter.py)."""

    CASES = [
        # (part, outcome, fragment of the error)
        ("storylines", Reply(text="Sure! Here are the storylines."), "not one JSON document"),
        ("storylines", Reply(document={"storylines": []}), "needs at least 1 entries"),
        ("storylines", Reply(edit=_set(["storylines", 0, "name"], "Office Pantry")), "does not match"),
        ("storylines", Reply(edit=_set(["storylines", 3, "burst_days"], [9, 3])), "burst_days"),
        ("storylines", Reply(edit=_set(["storylines", 0, "month_weights"], {"13": 1})), "not a month number"),
        ("catalog-01", Reply(edit=_set(["items", 0, "archetype"], "weekly_splurge")), "is not one of"),
        ("catalog-01", Reply(edit=_set(["items", 0, "unit_price"], 15000)), "never carry a price or a pack size"),
        ("catalog-01", Reply(edit=_set(["items", 0, "pack_pcs"], 12)), "never carry a price or a pack size"),
        ("catalog-01", Reply(edit=_set(["items", 0, "sellers", 0, "anchor_price"], 5)), "never carry a price"),
        ("catalog-01", Reply(edit=_set(["items", 0, "storyline"], "nightlife")), "not a drafted storyline"),
        ("catalog-01", Reply(edit=_drop(["items", 0, "sellers"])), "missing `sellers`"),
        ("catalog-01", Reply(edit=_set(["items", 0, "sellers", 0, "vendor"], "Invented Trading Co")),
         "not an allowlisted or fabricated vendor"),
        ("catalog-01", Reply(edit=_set(["items", 0, "category"], "Snacks")), "not one of this call's categories"),
        ("catalog-01", Reply(edit=lambda d: {"items": d["items"][2:]}), "no item for category"),
        ("catalog-01", Reply(edit=_set(["items", 0, "params"], {"per_week": 2, "anchor_day": 3})), "not a param of"),
        ("catalog-02", Reply(edit=_set(["items", 0, "id"], "delivery_fee_a")), "already taken"),
        ("variants-01", Reply(edit=_set(["items", 0, "terse"], ["one"])), "needs at least 2"),
        ("variants-01", Reply(edit=_set(["items", 0, "descriptive"], ["only one"])), "needs at least 3"),
        ("variants-01", Reply(edit=lambda d: {"items": d["items"][:-1]}), "no variants for"),
        ("variants-01", Reply(edit=_vendor_majority), "minority"),
        ("variants-01", Reply(edit=_wrong_vendor), "must read"),
        ("variants-01", Reply(edit=_share_descriptive), "only a terse text may be shared"),
        ("variants-01", Reply(edit=_set(["items", 0, "descriptive", 0], "Coffee ₱150")), "states a price"),
        ("variants-01", Reply(edit=_set(["items", 0, "descriptive", 0], "Bond paper 12pcs")), "pack size"),
        ("variants-01", Reply(edit=_set(["items", 0, "terse", 0], "paper - A4")), "carries \"-\""),
        ("variants-01", Reply(edit=_set(["items", 0, "descriptive", 0], "x" * 101)), "longer than 100"),
        ("variants-01", Reply(edit=_pcs_on_non_stock), "whose goods is not \"stock\""),
        ("variants-02", Reply(edit=_terse_on_big_ticket), "big-ticket"),
        ("vocabulary", Reply(document={"date_tails": ["{yyyy}"]}), "must name the day and the month"),
        ("vocabulary", Reply(document={"date_tails": ["on {d} - {mon}"]}), "without ' - '"),
    ]

    def test_invalid_twice_exits_4_after_one_re_ask(self):
        for part, outcome, fragment in self.CASES:
            with self.subTest(part=part, fragment=fragment):
                self.ws = Workspace(self)
                self.ws.install_author_inputs()
                self.ws.write_config(f'[author]\nmodel = "{MODEL}"\n')
                fake = ScriptedLLM().script(part, outcome, outcome)
                r = self.author(fake)
                self.assertEqual(r.code, 4, r.stdout + r.stderr)
                self.assertIn(f"draft `{part}` failed its schema again after one re-ask", r.stderr)
                self.assertIn(fragment, r.stderr)
                self.assertEqual(fake.parts().count(part), 2)  # the first ask and one re-ask (T35)
                self.assertTrue(any(fragment in p for p in fake.requests[-1].problems), fake.requests[-1].problems)
                self.assertEqual(fake.parts()[-1], part)  # the run stops there
                self.assertNotIn(part, self.saved_parts())
                self.assertFalse((self.ws.cwd / "bundles").exists())

    def test_a_fenced_json_answer_is_accepted(self):
        fake = ScriptedLLM().script("vocabulary", Reply(text='```json\n{"date_tails": ["({mon} {d})"]}\n```'))
        self.ok(fake)
        self.assertEqual(self.saved("vocabulary")["draft"], {"date_tails": ["({mon} {d})"]})

    def test_pack_size_placeholder_on_stock_items(self):
        self.ok()
        stock = {i["id"] for i in self.catalog() if i.get("goods") == "stock"}
        self.assertTrue(stock)
        with_pcs = {i for i, v in self.variants().items() if any("{pcs}" in t for t in v["descriptive"])}
        self.assertEqual(with_pcs, stock)


class ResumeTest(DraftCase):
    def test_rerun_resumes_from_saved_drafts(self):  # FR-C7
        first = ScriptedLLM().script("variants-02", *[Fail("rate limited")] * 4)
        self.assertEqual(self.author(first).code, 3)
        second = ScriptedLLM()
        r = self.ok(second)
        self.assertEqual(second.parts(), ["variants-02", "vocabulary"])
        self.assertIn("2 LLM calls this run ($0.0200), 4 parts reused from saved drafts", r.stdout)
        self.assertEqual(self.saved_parts(), sorted(PARTS))

    def test_rerun_after_an_invalid_response_asks_only_from_there(self):
        first = ScriptedLLM().script("catalog-02", Reply(text="{}"), Reply(text="{}"))
        self.assertEqual(self.author(first).code, 4)
        second = ScriptedLLM()
        self.ok(second)
        self.assertEqual(second.parts(), PARTS[2:])

    def test_a_changed_payload_starts_new_drafts(self):
        self.ok()
        old = self.drafts_dir()
        path = self.ws.cwd / "inputs" / "ledgers" / "account-transactions-2024.csv"
        text = path.read_bytes().decode("utf-8")
        self.assertEqual(text.count("Gaffer tape"), 1)
        path.write_bytes(text.replace("Gaffer tape", "Duct tape").encode("utf-8"))
        again = ScriptedLLM()
        self.ok(again)
        self.assertNotEqual(self.drafts_dir(), old)
        self.assertEqual(again.parts(), PARTS)
        self.assertTrue(old.exists())  # the old drafts stay under their own payload hash

    def test_a_saved_draft_that_no_longer_validates_is_drafted_again(self):
        self.ok()
        path = self.drafts_dir() / "vocabulary.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["draft"]["date_tails"] = ["{yyyy}"]
        path.write_text(json.dumps(saved), encoding="utf-8")
        again = ScriptedLLM()
        r = self.ok(again)
        self.assertEqual(again.parts(), ["vocabulary"])
        self.assertIn("saved draft `vocabulary` is no longer valid", r.stderr)

    def test_same_ledgers_give_the_same_drafts_folder(self):
        self.ok()
        other = Workspace(self)
        other.install_author_inputs()
        other.write_config(f'[author]\nmodel = "{MODEL}"\n')
        self.assertEqual(other.run("author", env=KEY).code, 0)
        self.assertTrue((other.cwd / AUTHOR_DIR / "drafts" / self.payload_hash()).is_dir())


class ArchetypeNamesTest(unittest.TestCase):
    def test_seven_archetypes_spelled_as_the_engine_registers_them(self):  # FR-E3
        self.assertEqual(len(parts.ARCHETYPES), 7)
        self.assertEqual(set(parts.PARAMS), set(parts.ARCHETYPES))
        self.assertLessEqual(set(archetypes.names()), set(parts.ARCHETYPES))


if __name__ == "__main__":
    unittest.main()
