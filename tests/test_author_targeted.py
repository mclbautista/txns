"""`author` re-asks only for the deficient items of a variants draft (issue #36).

A first answer that loses many texts to accidental ledger-name matches, concentrated in a few catalog
items, is not regenerated as a whole. The one re-ask names only the items (and kinds of variant) that are
still under their counts, asks for new texts only, and the answer is added to the texts already kept,
item by item. Same synthetic blocked words as `test_author_collisions` ("delivery" and "snacks");
scripted LLM, no network.

The scripted model is built from the request it gets, as a real one behaves: asked again for the whole
batch it repeats itself (the same words, so the same matches); asked for a few new texts of a few items it
writes those.
"""

import json
import unittest

from tests.llm_fake import Reply, ScriptedLLM, valid_draft
from tests.test_author_collisions import COLLIDING, CollisionCase
from tests.test_author_drafts import MODEL
from txns.drafting import parts
from txns.drafting.targeted import Targeted

HIT = "delivery"  # a blocked name in this workspace
FEW = 3  # items that carry the matches


def _base(item_id: str) -> str:
    return item_id.replace("_", " ")


def _affected(doc):
    chosen = [v for v in doc["items"] if v["terse"]][:FEW]
    assert len(chosen) == FEW
    return chosen


def _concentrated(round_: int, *, lost: int = 4):
    """The answer of a model that repeats itself: in the first three items, one safe text and `lost` more
    that carry a blocked word (so each item is left with a single usable text). Other items are untouched."""
    def edit(doc):
        for v in _affected(doc):
            base = _base(v["id"])
            v["vendor"] = []
            v["descriptive"] = [f"{base} safe order"] + [f"{base} {HIT} r{round_}k{k}" for k in range(lost)]
    return edit


def _fresh(item: dict, *, hits: int = 2, tag: str = "fresh") -> dict:
    """What a model writes when asked for `need` new texts of an item: that many, plus `hits` that match."""
    base = _base(item["id"])
    need = item["need"]
    return {
        "id": item["id"],
        "descriptive": [f"{base} {tag} {k}" for k in range(need["descriptive"])]
        + [f"{base} {HIT} r1k{k}" for k in range(hits if need["descriptive"] else 0)],
        "terse": [f"{base} {tag} t{k}" for k in range(need["terse"])],
        "vendor": [],
    }


def _model(request, *, fresh=_fresh):
    """Asked for the whole batch again: the same answer as before. Asked for replacements: writes them."""
    if not any("need" in item for item in request.input["items"]):
        doc = valid_draft(request)
        _concentrated(1)(doc)
        return doc
    return {"items": [fresh(item) for item in request.input["items"]]}


def _script(respond=_model, first=_concentrated(0)):
    return ScriptedLLM().script("variants-01", Reply(edit=first), Reply(respond=respond))


class TargetedCase(CollisionCase):
    def asks(self, fake):
        return [q for q in fake.requests if q.part == "variants-01"]

    def first_defaults(self, fake):
        """The first answer's untouched items: the fake's own valid draft for the request it got."""
        return {v["id"]: v for v in valid_draft(self.asks(fake)[0])["items"]}

    def saved_items(self):
        return {v["id"]: v for v in self.saved("variants-01")["draft"]["items"]}

    def assert_fails_closed(self, fake, *, shown=None):
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertEqual(fake.parts().count("variants-01"), 2)  # one re-ask, no third call
        self.assertIn("draft `variants-01` failed its schema again after one re-ask", r.stderr)
        self.assertNotIn("variants-01", self.saved_parts())
        self.assertFalse((self.ws.cwd / "bundles").exists())
        self.assertEqual({q.model for q in fake.requests}, {MODEL})
        self.assert_no_collision_text(r, fake)
        if shown:
            self.assertIn(shown, r.stderr)
        return r


class ConcentratedCollisionTest(TargetedCase):
    def test_the_whole_batch_re_ask_fails_where_the_scoped_one_promotes(self):
        # Repeated matches in three items, everything else valid; the model repeats itself when asked for the whole batch.
        fake = _script()
        r = self.ok(fake)
        self.assertEqual(len(self.asks(fake)), 2)  # the one re-ask, no more
        self.assertRegex(r.stdout, r"promoted bundles/draft-[0-9a-f]{12}/ \(reviewed: false\)")
        self.assertEqual(len(list((self.ws.cwd / "bundles").iterdir())), 1)
        self.assertNotIn(f"{HIT} r", (r.stdout + r.stderr + fake.sent_text() + self.everything_written()).casefold())

    def test_the_re_ask_names_only_the_deficient_items(self):
        fake = _script()
        self.ok(fake)
        first, again = self.asks(fake)
        wanted = {v["id"] for v in _affected(valid_draft(first))}
        self.assertEqual({i["id"] for i in again.input["items"]}, wanted)
        self.assertEqual(len(first.input["items"]), len(self.first_defaults(fake)))
        self.assertLess(len(again.input["items"]), len(first.input["items"]))
        used = {c["category"] for c in again.input["categories"]}
        self.assertEqual(used, {i["category"] for i in again.input["items"]})

    def test_the_counts_asked_are_safe_and_consistent(self):
        fake = _script()
        self.ok(fake)
        _, again = self.asks(fake)
        for item in again.input["items"]:
            have, need = item["have"], item["need"]
            self.assertEqual(len(have["descriptive"]), 1)  # the one text of the first answer that was safe
            self.assertEqual(need["terse"], 0)  # terse was not short, so none is asked
            self.assertGreaterEqual(need["descriptive"], parts.MIN_DESCRIPTIVE - 1)
            self.assertLessEqual(len(have["descriptive"]) + need["descriptive"], parts.MAX_VARIANTS)
        props = again.schema["properties"]["items"]["items"]["properties"]
        self.assertEqual(props["descriptive"]["minItems"], 0)  # no kind is demanded of an item that needs none
        self.assertEqual(sorted(props["id"]["enum"]), sorted(i["id"] for i in again.input["items"]))
        self.assertNotIn("every item", again.instructions)

    def test_the_re_ask_never_carries_a_blocked_word_or_a_discarded_text(self):
        fake = _script()
        r = self.ok(fake)
        _, again = self.asks(fake)
        sent = json.dumps({**again.content(), "messages": again.messages()}, ensure_ascii=False).casefold()
        # ("delivery" is also a catalog category here, as in the first request; what must not travel is a text)
        for text in (f"{HIT} r", "snacks", *COLLIDING):
            self.assertNotIn(text, sent)
        self.assertEqual(again.problems, ())  # nothing about the first answer is sent back
        self.assertIsNone(again.previous)
        self.assertNotIn(f"{HIT} r", r.stderr.casefold())

    def test_the_other_items_and_the_kept_texts_stay_as_they_were(self):
        fake = _script()
        self.ok(fake)
        saved, defaults = self.saved_items(), self.first_defaults(fake)
        affected = {v["id"] for v in _affected(valid_draft(self.asks(fake)[0]))}
        self.assertEqual(set(saved), set(defaults))
        for item_id, v in saved.items():
            if item_id in affected:
                self.assertEqual(v["descriptive"][0], f"{_base(item_id)} safe order")  # kept, and first
                self.assertGreaterEqual(len(v["descriptive"]), parts.MIN_DESCRIPTIVE)
                self.assertLessEqual(len(v["descriptive"]), parts.MAX_VARIANTS)
                self.assertEqual(v["terse"], defaults[item_id]["terse"])
            else:
                self.assertEqual(v, defaults[item_id], item_id)

    def test_the_new_texts_only_top_up_what_was_short(self):
        fake = _script()
        self.ok(fake)
        for item_id in (v["id"] for v in _affected(valid_draft(self.asks(fake)[0]))):
            self.assertEqual(len(self.saved_items()[item_id]["descriptive"]), parts.MIN_DESCRIPTIVE)

    def test_the_pinned_model_and_cost_accounting_are_unchanged(self):
        fake = _script()
        r = self.ok(fake)
        self.assertEqual({q.model for q in fake.requests}, {MODEL})
        self.assertEqual(self.saved("variants-01")["model"], fake.model)
        self.assertAlmostEqual(self.saved("variants-01")["cost_usd"], 2 * fake.cost)
        self.assertIn("7 LLM calls this run ($0.0700)", r.stdout)  # six parts and the one re-ask

    def test_the_run_reports_the_items_by_id_only(self):
        fake = _script()
        r = self.ok(fake)
        self.assertRegex(r.stderr, rf"draft `variants-01`: .*asking once more for only {FEW} items")
        self.assertRegex(r.stderr, rf"dropped {FEW * 4} texts that match a ledger name")
        self.assertNotIn(HIT + " r", r.stderr)

    def test_the_choice_is_deterministic(self):
        runs = []
        for _ in range(2):
            self.setUp()
            self.ok(_script())
            runs.append(self.saved("variants-01")["draft"])
        self.assertEqual(runs[0], runs[1])

    def test_a_rerun_resumes_the_saved_draft_without_a_call(self):
        self.ok(_script())
        again = ScriptedLLM()
        r = self.ok(again)
        self.assertEqual(again.requests, [])
        self.assertIn("6 parts reused from saved drafts", r.stdout)


class CountsAskedTest(TargetedCase):
    def test_the_spare_grows_with_what_an_item_lost_and_stops_at_the_cap(self):
        for lost, want in ((1, 4), (4, 10), (11, 11)):  # 2 short + spare max(2, 2 * lost), at most 12 - 1 kept
            with self.subTest(lost=lost):
                self.setUp()
                fake = _script(first=_concentrated(0, lost=lost))
                self.author(fake)  # the answer to the re-ask does not matter here
                again = self.asks(fake)[1]
                self.assertEqual({i["need"]["descriptive"] for i in again.input["items"]}, {want})
                for item in again.input["items"]:
                    self.assertLessEqual(len(item["have"]["descriptive"]) + item["need"]["descriptive"], parts.MAX_VARIANTS)

    def test_a_big_ticket_item_is_never_asked_for_terse_texts(self):
        fake = _script()
        self.ok(fake)
        for item in self.asks(fake)[1].input["items"]:
            if item["class"] == "big_ticket":
                self.assertEqual(item["need"]["terse"], 0)

    def test_an_item_the_first_answer_left_out_is_asked_for_in_full(self):
        def without_one(doc):
            _concentrated(0)(doc)
            doc["items"].pop()

        fake = ScriptedLLM().script("variants-01", Reply(edit=without_one), Reply(respond=_model))
        self.ok(fake)
        again = self.asks(fake)[1]
        left_out = [i for i in again.input["items"] if not i["have"]["descriptive"]]
        self.assertEqual(len(left_out), 1)
        self.assertEqual(left_out[0]["have"], {"descriptive": [], "terse": []})
        self.assertGreaterEqual(left_out[0]["need"]["descriptive"], parts.MIN_DESCRIPTIVE)
        self.assertEqual(len(again.input["items"]), FEW + 1)
        self.assertIn(left_out[0]["id"], self.saved_items())


class FillingTest(TargetedCase):
    def test_each_kind_is_topped_up_only_to_its_own_minimum(self):
        def both_short(doc):
            _concentrated(0)(doc)
            for v in _affected(doc):
                base = _base(v["id"])
                v["terse"] = [f"{base} tt", f"{base} {HIT} r0t"]

        def lavish(item):
            base = _base(item["id"])
            return {"id": item["id"], "vendor": [], "descriptive": [f"{base} new {k}" for k in range(8)],
                    "terse": [f"{base} nt {k}" for k in range(4)]}

        fake = ScriptedLLM().script("variants-01", Reply(edit=both_short),
                                    Reply(respond=lambda q: _model(q, fresh=lavish)))
        self.ok(fake)
        for item in self.asks(fake)[1].input["items"]:
            self.assertGreater(item["need"]["descriptive"], 0)
            self.assertGreater(item["need"]["terse"], 0)
            saved = self.saved_items()[item["id"]]
            self.assertEqual(len(saved["descriptive"]), parts.MIN_DESCRIPTIVE)
            self.assertEqual(len(saved["terse"]), parts.MIN_TERSE)


class TargetedUnitTest(unittest.TestCase):
    PART = parts.Part("variants-01", "variants", ("Cat",))
    PAYLOAD = {"categories": [{"category": "Cat", "rows": 3, "textless_rows": 0, "item_texts": [], "vendors": []}]}

    def drafts(self):
        d = parts.Drafts()
        d.items["item_0"] = {"id": "item_0", "category": "Cat", "class": "retail",
                             "sellers": [{"id": "acme", "vendor": "Acme Co"}, {"id": "shop", "vendor": None}]}
        d.sellers = {"acme": "Acme Co", "shop": None}
        return d

    def answer(self, **kw):
        return {"items": [{"id": "item_0", "descriptive": kw.get("d", []), "terse": kw.get("t", []),
                           "vendor": [{"seller": "acme", "text": f"Acme Co - {x}"} for x in kw.get("v", [])]}]}

    def test_a_kept_vendor_text_is_shown_without_its_vendor(self):
        first = self.answer(d=["a one"], t=["t one", "t two"], v=[])
        first["items"][0]["vendor"] = []
        plan = Targeted.plan(self.PART, first, [(0, "descriptive", 1)], self.PAYLOAD, self.drafts())
        self.assertEqual(plan.gaps["item_0"].descriptive, 2 + 2)  # two short, plus the spare of two
        first = self.answer(d=["a one", "a two"], t=["t one", "t two"], v=["a three"])
        first["items"][0]["terse"] = ["t one"]
        plan = Targeted.plan(self.PART, first, [(0, "terse", 0)], self.PAYLOAD, self.drafts())
        self.assertEqual(plan.input()["items"][0]["have"],
                         {"descriptive": ["a one", "a two", "a three"], "terse": ["t one"]})
        self.assertEqual(plan.input()["items"][0]["need"], {"descriptive": 0, "terse": 1 + 2})

    def test_nothing_is_planned_when_every_item_is_complete(self):
        first = self.answer(d=["a one", "a two", "a three"], t=["t one", "t two"])
        self.assertIsNone(Targeted.plan(self.PART, first, [(0, "descriptive", 3)], self.PAYLOAD, self.drafts()))

    def test_no_plan_for_an_answer_that_is_not_well_formed(self):
        first = {**self.answer(d=["a one"], t=["t one"]), "note": "x"}
        self.assertIsNone(Targeted.plan(self.PART, first, [(0, "descriptive", 1)], self.PAYLOAD, self.drafts()))


class WhenTheScopedReAskDoesNotApplyTest(TargetedCase):
    def test_a_first_answer_with_an_unrepaired_fault_gets_the_whole_re_ask_as_before(self):
        def priced_and_matching(doc):
            _concentrated(0)(doc)
            other = [v for v in doc["items"] if v["terse"]][FEW]
            other["descriptive"][0] = f"{_base(other['id'])} for \u20b1150.00"

        fake = ScriptedLLM().script("variants-01", Reply(edit=priced_and_matching), Reply(respond=_model))
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        again = self.asks(fake)[1]
        self.assertTrue(again.problems)
        self.assertEqual(len(again.input["items"]), len(self.asks(fake)[0].input["items"]))

    def test_a_first_answer_that_is_complete_after_the_matches_needs_no_re_ask(self):
        def spare(doc):
            _concentrated(0, lost=1)(doc)
            for v in _affected(doc):
                v["descriptive"] += [f"{_base(v['id'])} more {k}" for k in range(3)]

        fake = ScriptedLLM().script("variants-01", Reply(edit=spare))
        r = self.ok(fake)
        self.assertEqual(len(self.asks(fake)), 1)
        self.assertIn(f"dropped {FEW} texts that match a ledger name", r.stderr)


class FailClosedTest(TargetedCase):
    def test_a_model_that_repeats_the_kept_texts_leaves_the_items_short(self):
        def repeats(request):
            doc = valid_draft(request)
            for v in doc["items"]:
                v["descriptive"] = [f"{_base(v['id'])} safe order"] * 3
            return {"items": doc["items"]}

        self.assert_fails_closed(_script(repeats), shown="needs at least 3")

    def test_too_few_safe_texts_in_the_answer_exit_4(self):
        def mostly_hits(item):
            return _fresh(item, hits=20) | {"descriptive": [f"{_base(item['id'])} fresh 0"]
                                            + [f"{_base(item['id'])} {HIT} r1k{k}" for k in range(9)]}

        fake = _script(lambda q: _model(q, fresh=mostly_hits))
        self.assert_fails_closed(fake, shown="needs at least 3")

    def test_an_item_that_was_not_asked_for_exits_4(self):
        fake = ScriptedLLM()
        # every item of the call, not just the asked ones
        first_items = lambda: valid_draft(next(q for q in fake.requests if q.part == "variants-01"))["items"]

        def stranger(request, unknown):
            doc = _model(request)
            asked = {i["id"] for i in doc["items"]}
            extra = {**next(v for v in first_items() if v["id"] not in asked)}
            doc["items"].append({**extra, "id": "not_in_this_call"} if unknown else extra)
            return doc

        for unknown in (True, False):
            with self.subTest(unknown=unknown):
                self.setUp()
                fake = ScriptedLLM().script("variants-01", Reply(edit=_concentrated(0)),
                                            Reply(respond=lambda q, u=unknown: stranger(q, u)))
                self.assert_fails_closed(fake)

    def test_a_malformed_shape_exits_4(self):
        for name, build in {
            "extra key": lambda d: {**d, "note": "x"},
            "a list": lambda d: d["items"],
            "item without a list": lambda d: {"items": [{**d["items"][0], "terse": "x"}, *d["items"][1:]]},
            "item listed twice": lambda d: {"items": [*d["items"], d["items"][0]]},
            "extra item key": lambda d: {"items": [{**d["items"][0], "price": 1}, *d["items"][1:]]},
        }.items():
            with self.subTest(name):
                self.setUp()
                self.assert_fails_closed(_script(lambda q, b=build: b(_model(q))))

    def test_not_json_exits_4(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_concentrated(0)), Reply(text="not json"))
        self.assert_fails_closed(fake, shown="not one JSON document")

    def test_a_price_in_a_new_text_exits_4_and_is_not_saved(self):
        def priced(item):
            out = _fresh(item)
            out["descriptive"][0] = f"{_base(item['id'])} for ₱150.00"
            return out

        r = self.assert_fails_closed(_script(lambda q: _model(q, fresh=priced)), shown="states a price")
        self.assertNotIn("₱", self.everything_written())

    def test_a_text_another_item_has_is_skipped_not_shared(self):
        def shared(item):
            out = _fresh(item)
            out["descriptive"][0] = out["descriptive"][0].replace(_base(item["id"]), "printer paper")
            return out

        # Three items offer the same first text; only one may keep it, and the others still reach three from the rest.
        self.ok(_script(lambda q: _model(q, fresh=shared)))
        seen = {}
        for v in self.saved("variants-01")["draft"]["items"]:
            for t in v["descriptive"] + [x["text"] for x in v["vendor"]]:
                self.assertNotIn(t, seen, f"{t!r} in {v['id']} and {seen.get(t)}")
                seen[t] = v["id"]

    def test_pack_wording_slips_are_dropped_and_the_rest_used(self):
        def slip(item):
            out = _fresh(item, hits=0)
            out["descriptive"].insert(0, f"{_base(item['id'])} cable, {{pcs}} meters")
            return out

        r = self.ok(_script(lambda q: _model(q, fresh=slip)))
        for v in self.saved("variants-01")["draft"]["items"]:
            self.assertFalse(any("meters" in t for t in v["descriptive"]))
        self.assertNotIn("meters", r.stderr)

    def test_an_id_that_is_a_ledger_name_is_never_echoed(self):
        def leaky(request):
            doc = _model(request)
            doc["items"][0]["id"] = "snacks"
            return doc

        r = self.assert_fails_closed(_script(leaky), shown="carries a ledger name that is not on the brand allowlist")
        self.assertNotIn("snacks", (r.stdout + r.stderr).casefold().replace("snacks_", ""))

    def test_a_first_answer_that_is_not_scoped_keeps_the_whole_re_ask(self):
        # No match anywhere: an ordinary invalid answer, asked again whole with the problem list as before.
        def short(doc):
            _affected(doc)[0]["descriptive"] = ["only one"]

        fake = ScriptedLLM().script("variants-01", Reply(edit=short), Reply(edit=short))
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        again = self.asks(fake)[1]
        self.assertTrue(again.problems)
        self.assertEqual(len(again.input["items"]), len(self.asks(fake)[0].input["items"]))


if __name__ == "__main__":
    unittest.main()
