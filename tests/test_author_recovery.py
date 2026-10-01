"""`author` recovers each item of a variants draft from both answers (issue #35).

A first answer that loses texts to accidental ledger-name matches and its one re-ask are both
pools of candidate texts. A repeated text in an unrelated item, or an affected item that comes back
with more than 12 entries, no longer fails a draft that the two answers can still complete. Same
synthetic blocked words as `test_author_collisions` ("Delivery" and "Snacks"); scripted LLM, no network.

Since issue #36 a first answer that leaves items short is re-asked for those items only (see
`test_author_targeted`). These tests cover the whole-batch re-ask that remains: a draft that lost texts
to coincidences but whose items are all still complete, and so is invalid for another reason (here a
repeated text in an unrelated item, `_first`). Its re-ask answer is chosen item by item with
`parts.recover_variants`, as before.
"""

import shutil
import unittest

from tests.llm_fake import Reply, ScriptedLLM, valid_draft
from tests.test_author_collisions import COLLIDING, CollisionCase, _collide
from txns.drafting import parts
from txns.privacy import NameIndex
from txns.privacy.names import Allowlist

EXTRA = "surplus entry past the cap"


def _with_terse(doc, n):
    return [v for v in doc["items"] if v["terse"]][n]


def _base(v) -> str:
    return v["id"].replace("_", " ")


def _repeat_in_unrelated(doc):
    """The re-ask answer's fourth item (unaffected) lists one of its variants twice."""
    v = _with_terse(doc, 3)
    v["descriptive"][1] = v["descriptive"][0]


def _repeat_across_unrelated(doc):
    """The re-ask answer gives two unrelated items the same descriptive text."""
    _with_terse(doc, 4)["descriptive"][0] = _with_terse(doc, 3)["descriptive"][0]


def _over_cap(doc):
    """The re-ask answer's first affected item comes back with 13 usable texts after its match is dropped."""
    v = _with_terse(doc, 0)
    base = _base(v)
    v["descriptive"] = [f"{base} entry {k}" for k in range(14)]
    v["descriptive"][2] = f"{base} {COLLIDING[3]}"
    v["descriptive"][13] = f"{base} {EXTRA}"


def _first(doc):
    """The first answer: three items lose one text to a match but keep three (four were given), and a sixth
    item lists one of its variants twice, so the draft is invalid without any item being short."""
    _collide(0, spare=1)(doc)
    v = _with_terse(doc, 5)
    v["descriptive"][1] = v["descriptive"][0]


UNRELATED = (3, 4)  # items the first answer leaves alone; the re-ask answer is edited there
AFFECTED = (0, 1, 2, 5)  # items whose texts are not simply the default ones in the saved draft


def _reask(*edits, spare=2):
    collide = _collide(1, spare=spare)

    def edit(doc):
        collide(doc)
        for e in edits:
            e(doc)
    return Reply(edit=edit)


class RecoveryCase(CollisionCase):
    def script(self, *edits, spare=2):
        return ScriptedLLM().script("variants-01", Reply(edit=_first), _reask(*edits, spare=spare))

    def valid(self, fake):
        """A fresh valid variants draft built from the request the first answer was given."""
        return valid_draft(next(q for q in fake.requests if q.part == "variants-01"))

    def first_answer(self, fake):
        return {v["id"]: v for v in self.valid(fake)["items"]}

    def everywhere(self, r, fake):
        return (r.stdout + r.stderr + fake.sent_text() + self.everything_written()).casefold()

    def assert_promoted_after_one_re_ask(self, r, fake):
        self.assertEqual(fake.parts().count("variants-01"), 2)  # the existing one re-ask, no more
        first, again = [q for q in fake.requests if q.part == "variants-01"]
        self.assertTrue(again.problems)  # no item is short, so this is the whole-batch re-ask
        self.assertEqual(len(again.input["items"]), len(first.input["items"]))
        self.assertRegex(r.stdout, r"promoted bundles/draft-[0-9a-f]{12}/ \(reviewed: false\)")
        self.assertEqual(len(list((self.ws.cwd / "bundles").iterdir())), 1)
        self.assert_no_collision_text(r, fake)

    def assert_fails_closed(self, fake):
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertEqual(fake.parts().count("variants-01"), 2)
        self.assertNotIn("variants-01", self.saved_parts())
        self.assertFalse((self.ws.cwd / "bundles").exists())
        self.assert_no_collision_text(r, fake)
        return r


class RepeatedTextInUnrelatedItemTest(RecoveryCase):
    def test_a_repeat_inside_an_unrelated_item_no_longer_fails_the_draft(self):
        fake = self.script(_repeat_in_unrelated)
        r = self.ok(fake)
        self.assert_promoted_after_one_re_ask(r, fake)

    def test_a_text_shared_by_two_unrelated_items_no_longer_fails_the_draft(self):
        fake = self.script(_repeat_across_unrelated)
        r = self.ok(fake)
        self.assert_promoted_after_one_re_ask(r, fake)

    def test_unaffected_items_keep_the_first_answers_version_unchanged(self):
        fake = self.script(_repeat_in_unrelated, _repeat_across_unrelated)
        self.ok(fake)
        first = self.first_answer(fake)
        saved = {v["id"]: v for v in self.saved("variants-01")["draft"]["items"]}
        affected = {_with_terse(self.valid(fake), n)["id"] for n in AFFECTED}
        self.assertEqual(set(saved), set(first))
        for item_id, v in saved.items():
            if item_id not in affected:
                self.assertEqual(v, first[item_id], item_id)

    def test_the_affected_items_get_valid_texts_from_the_answers(self):
        fake = self.script(_repeat_in_unrelated)
        self.ok(fake)
        saved = {v["id"]: v for v in self.saved("variants-01")["draft"]["items"]}
        for n in (0, 1, 2):
            v = saved[_with_terse(self.valid(fake), n)["id"]]
            self.assertGreaterEqual(len(v["descriptive"]) + len(v["vendor"]), 3)
            self.assertEqual(len(v["descriptive"]), len(set(v["descriptive"])))
            self.assertFalse(any(t.endswith(tuple(COLLIDING)) for t in v["descriptive"]))

    def test_nothing_is_shared_between_items_in_the_saved_draft(self):
        self.ok(self.script(_repeat_across_unrelated))
        seen: dict[str, str] = {}
        for v in self.saved("variants-01")["draft"]["items"]:
            for t in v["descriptive"] + [x["text"] for x in v["vendor"]]:
                self.assertNotIn(t, seen, f"{t!r} in {v['id']} and {seen.get(t)}")
                seen[t] = v["id"]


class OverCapTest(RecoveryCase):
    def test_an_affected_item_with_more_than_twelve_entries_no_longer_fails_the_draft(self):
        fake = self.script(_over_cap)
        r = self.ok(fake)
        self.assert_promoted_after_one_re_ask(r, fake)

    def test_the_extra_is_skipped_never_saved_or_sent(self):
        fake = self.script(_over_cap)
        r = self.ok(fake)
        saved = {v["id"]: v for v in self.saved("variants-01")["draft"]["items"]}
        v = saved[_with_terse(self.valid(fake), 0)["id"]]
        self.assertEqual(len(v["descriptive"]), parts.MAX_VARIANTS)
        self.assertEqual(self.everywhere(r, fake).count(EXTRA.casefold()), 0)

    def test_the_choice_is_deterministic(self):
        runs = []
        for _ in range(2):
            self.ok(self.script(_over_cap, _repeat_in_unrelated))
            runs.append(self.saved("variants-01")["draft"])
            shutil.rmtree(self.ws.cwd / "bundles")
            shutil.rmtree(self.drafts_dir())
        self.assertEqual(runs[0], runs[1])


class BothDriftsTest(RecoveryCase):
    def test_collisions_a_repeat_and_an_over_cap_item_recover_together(self):
        fake = self.script(_repeat_in_unrelated, _over_cap)
        r = self.ok(fake)
        self.assert_promoted_after_one_re_ask(r, fake)

    def test_the_recovery_is_reported_by_item_only(self):
        fake = self.script(_repeat_in_unrelated, _over_cap)
        r = self.ok(fake)
        self.assertRegex(r.stderr, r"draft `variants-01`: chose the texts of \d+ items? from both answers")
        self.assertNotIn(EXTRA, r.stderr)


class FailClosedTest(RecoveryCase):
    def test_a_repeat_with_no_name_match_anywhere_is_still_a_twice_invalid_answer(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_repeat_in_unrelated), Reply(edit=_repeat_in_unrelated))
        self.assert_fails_closed(fake)

    def test_a_malformed_answer_shape_is_not_recovered(self):
        def stranger(doc):
            doc["items"].append({**doc["items"][0], "id": "not_in_this_batch"})

        fake = self.script(stranger)
        self.assert_fails_closed(fake)

    def test_other_invalid_kinds_are_not_recovered_into_an_incomplete_item(self):
        # The only usable texts of an affected item are priced: nothing safe is left to choose.
        def priced(doc):
            v = _with_terse(doc, 0)
            v["descriptive"] = [f"{_base(v)} for ₱{k}50.00" for k in range(5)]

        fake = self.script(priced, spare=2)
        r = self.assert_fails_closed(fake)
        self.assertNotIn("₱", self.everything_written())

    def test_every_saved_text_was_in_one_of_the_two_answers(self):
        fake = self.script(_repeat_in_unrelated, _over_cap)
        self.ok(fake)
        pool = set()
        for edit in (_first, _reask(_repeat_in_unrelated, _over_cap).edit):
            doc = self.valid(fake)
            edit(doc)
            pool |= {t for v in doc["items"] for t in v["descriptive"] + v["terse"] + [x["text"] for x in v["vendor"]]}
        for v in self.saved("variants-01")["draft"]["items"]:
            for t in v["descriptive"] + v["terse"] + [x["text"] for x in v["vendor"]]:
                self.assertIn(t, pool)


class RecoverVariantsUnitTest(unittest.TestCase):
    """`parts.recover_variants` on its own: vendor minority, shared texts, big-ticket items, counts."""

    PART = parts.Part("variants-01", "variants", ("Cat",))
    PAYLOAD = {"categories": [{"category": "Cat", "vendors": [{"name": "Acme Co"}]}]}

    def drafts(self, *classes):
        d = parts.Drafts()
        for n, cls in enumerate(classes):
            item_id = f"item_{n}"
            d.items[item_id] = {"id": item_id, "category": "Cat", "class": cls,
                                "sellers": [{"id": "acme", "vendor": "Acme Co"}, {"id": "shop", "vendor": None}]}
        d.sellers = {"acme": "Acme Co", "shop": None}
        return d

    def answer(self, **items):
        return {"items": [{"id": k, "descriptive": v.get("d", []), "terse": v.get("t", []),
                           "vendor": [{"seller": "acme", "text": f"Acme Co - {x}"} for x in v.get("v", [])]}
                          for k, v in items.items()]}

    def recover(self, first, later, drafts, lost=()):
        dropped = [(i, "descriptive", 0) for i in lost]
        return parts.recover_variants(self.PART, first, dropped, later, self.PAYLOAD, drafts)

    def test_vendor_texts_stay_a_minority_of_the_descriptive_ones(self):
        d = self.drafts("retail")
        first = self.answer(item_0=dict(d=["a one", "a two"], t=["t one", "t two"], v=["a three", "a four", "a five"]))
        document, _ = self.recover(first, first, d)
        item = document["items"][0]
        self.assertEqual(len(item["vendor"]), 1)
        self.assertLess(len(item["vendor"]), len(item["descriptive"]))
        self.assertEqual(parts.problems(self.PART, document, self.PAYLOAD, d, NameIndex([], Allowlist({}, False))), [])

    def test_a_text_an_earlier_draft_already_uses_is_skipped(self):
        d = self.drafts("retail")
        d.variants["other_item"] = {"descriptive": ["taken text"], "terse": [], "vendor": []}
        first = self.answer(item_0=dict(d=["taken text", "a two", "a three", "a four"], t=["t one", "t two"]))
        document, _ = self.recover(first, first, d)
        self.assertEqual(document["items"][0]["descriptive"], ["a two", "a three", "a four"])

    def test_a_big_ticket_item_keeps_no_terse_texts(self):
        d = self.drafts("big_ticket")
        first = self.answer(item_0=dict(d=["a one", "a two", "a three"], t=["t one"]))
        document, _ = self.recover(first, first, d)
        self.assertEqual(document["items"][0]["terse"], [])

    def test_the_other_answer_only_tops_up_what_is_short(self):
        d = self.drafts("retail")
        first = self.answer(item_0=dict(d=["a one", "a two"], t=["t one", "t two"]))
        later = self.answer(item_0=dict(d=["b one", "b two", "b three"], t=["u one", "u two"]))
        document, changed = self.recover(first, later, d)
        self.assertEqual(document["items"][0]["descriptive"], ["a one", "a two", "b one"])
        self.assertEqual(document["items"][0]["terse"], ["t one", "t two"])
        self.assertEqual(changed, ["item_0"])

    def test_the_reworded_item_prefers_the_re_ask_answer(self):
        d = self.drafts("retail")
        first = self.answer(item_0=dict(d=["a one", "a two"], t=["t one", "t two"]))
        later = self.answer(item_0=dict(d=["b one", "b two", "b three"], t=["u one", "u two"]))
        document, _ = self.recover(first, later, d, lost=[0])
        self.assertEqual(document["items"][0]["descriptive"], ["b one", "b two", "b three"])

    def test_pack_wording_is_skipped_but_a_price_gives_the_answer_up(self):
        d = self.drafts("retail")
        slip = self.answer(item_0=dict(d=["a one", "a two", "a three", "a four, 5 pcs"], t=["t one", "t two"]))
        document, _ = self.recover(slip, slip, d)
        self.assertEqual(document["items"][0]["descriptive"], ["a one", "a two", "a three"])
        priced = self.answer(item_0=dict(d=["a one", "a two", "a three", "a four \u20b150.00"], t=["t one", "t two"]))
        self.assertIsNone(self.recover(priced, priced, d))

    def test_a_pack_slip_that_also_has_another_fault_gives_the_answer_up(self):
        d = self.drafts("retail")
        both = self.answer(item_0=dict(d=["a one", "a two", "a three", "a four 5 pcs \u20b150.00"], t=["t one", "t two"]))
        self.assertIsNone(self.recover(both, both, d))

    def test_a_malformed_answer_is_never_used_alone_even_when_the_other_could_finish(self):
        d = self.drafts("retail")
        good = self.answer(item_0=dict(d=["a one", "a two", "a three"], t=["t one", "t two"]))
        stranger = self.answer(item_0=dict(d=["b one", "b two", "b three"], t=["u one", "u two"]), nope=dict())
        self.assertIsNone(self.recover(good, stranger, d))
        self.assertIsNone(self.recover(stranger, good, d))

    def test_a_faulty_copy_that_is_not_chosen_does_not_stop_the_recovery(self):
        d = self.drafts("retail", "retail")
        first = self.answer(item_0=dict(d=["a one", "a two", "a three"], t=["t one", "t two"]),
                            item_1=dict(d=["c one", "c two"], t=["s one", "s two"]))
        later = self.answer(item_0=dict(d=["b one", "b two", "b \u20b150.00"], t=["u one", "u two"]),
                            item_1=dict(d=["e one", "e two", "e three"], t=["w one", "w two"]))
        document, changed = self.recover(first, later, d)
        self.assertEqual(document["items"][0]["descriptive"], ["a one", "a two", "a three"])  # re-ask copy never read
        self.assertEqual(document["items"][1]["descriptive"], ["c one", "c two", "e one"])
        self.assertEqual(changed, ["item_1"])

    def test_no_complete_choice_gives_none(self):
        d = self.drafts("retail")
        first = self.answer(item_0=dict(d=["a one", "a two"], t=["t one", "t two"]))
        self.assertIsNone(self.recover(first, first, d))

    def test_an_answer_with_an_unknown_item_or_extra_keys_is_not_a_pool(self):
        d = self.drafts("retail")
        good = self.answer(item_0=dict(d=["a one", "a two", "a three"], t=["t one", "t two"]))
        stranger = self.answer(item_0=dict(d=["b one", "b two", "b three"], t=["u one", "u two"]), nope=dict())
        keyed = {**good, "note": "x"}
        short = self.answer(item_0=dict(d=["a one"], t=["t one"]))
        for bad in (stranger, keyed):
            self.assertIsNone(self.recover(short, bad, d))
        self.assertIsNotNone(self.recover(short, good, d))


if __name__ == "__main__":
    unittest.main()
