"""`author` recovers when a valid variants answer accidentally matches a ledger name (issue #29).

The model never sees a ledger name, yet a plain word it uses can be one: a ledger
description with no " - " (just "Delivery") is read as a vendor name, so any text with that
word matches the leak check. Synthetic names only: this file edits the workspace's copy of the
fabricated fixture ledgers so that "Delivery" and "Snacks" become blocked names.

Driven through `main(argv)` with the scripted LLM fake; no network. The fake's variants-01
answer is edited so three items (no vendor-prefixed text, exactly the minimum of three
descriptive variants) each carry one text that matches, at descriptive[2], [1] and [1] as in the
reported failure.
"""

import json
import re
import unittest

from tests.llm_fake import Reply, ScriptedLLM, valid_draft
from tests.test_author_drafts import MODEL, DraftCase
from txns import ledger
from txns.drafting import parts
from txns.privacy import NameIndex, load_allowlist
from txns.privacy.leaks import LeakDetector

COLLIDING = ["same-day delivery run", "snacks for the review", "delivery to the client", "mixed snacks order",
             "bulk delivery order", "party snacks refill", "weekend delivery slot", "snacks box", "delivery bike"]
PATHS = [("descriptive", 2), ("descriptive", 1), ("descriptive", 1)]  # as reported: [2], [1], [1]


def _plain(item_id: str, n: int) -> list[str]:
    return [f"{item_id.replace('_', ' ')} {w}" for w in ("order", "supply", "restock", "refill", "extra")][:n]


def _collide(round_: int, spare: int = 0):
    """An edit of a valid variants draft: the first three items (their vendor-prefixed text removed) get
    exactly 3 + `spare` descriptive variants, one of them (at the reported path) a colliding text."""
    def edit(doc):
        chosen = [v for v in doc["items"] if v["terse"]][:3]
        assert len(chosen) == 3
        for n, v in enumerate(chosen):
            kind, at = PATHS[n]
            v["vendor"] = []
            texts = _plain(v["id"], 3 + spare)
            texts[at] = f"{v['id'].replace('_', ' ')} {COLLIDING[3 * round_ + n]}"
            v[kind] = texts
    return edit


def _asked(edit):
    """A scripted answer to the re-ask for only the short items (issue #36), written as an edit of a valid draft of
    the items listed in the request: the model writes exactly the number of new texts that were asked for (issue
    #40) and no vendor-prefixed text. An edit that gives fewer is padded with copies of its first text (a repeat
    adds nothing); one that gives more is a mistake of the test."""
    def respond(request):
        doc = valid_draft(request)
        edit(doc)
        for v, item in zip(doc["items"], request.input["items"]):
            v["vendor"], v["terse"] = [], v["terse"][: item["need"]["terse"]]
            v["descriptive"] += v["descriptive"][:1] * (item["need"]["descriptive"] - len(v["descriptive"]))
            assert (len(v["descriptive"]), len(v["terse"])) == (item["need"]["descriptive"], item["need"]["terse"]), v["id"]
        return doc
    return respond


def _again(round_: int, *, repeat: bool = False):
    """The re-ask answer of `_collide`'s three items: the `need` new texts, one at the reported path a match.
    With `repeat` the model words them as in the first answer, so nothing new comes of them."""
    def respond(request):
        items = []
        for n, item in enumerate(request.input["items"]):
            base, need = item["id"].replace("_", " "), item["need"]["descriptive"]
            texts = _plain(item["id"], 3) if repeat else [f"{base} {w}" for w in ("refill", "extra", "spare")]
            texts[PATHS[n][1] % need] = f"{base} {COLLIDING[3 * round_ + n]}"
            items.append({"id": item["id"], "descriptive": texts[:need], "terse": [], "vendor": []})
        return {"items": items}
    return respond


class CollisionCase(DraftCase):
    def setUp(self):
        super().setUp()
        self.edit_ledger("Spend Money,Swiftlane Couriers,,120.00", "Spend Money,Delivery,,120.00")
        self.edit_ledger("Spend Money,Bean Harbor Cafe,,340.00", "Spend Money,Snacks,,340.00", count=3)

    def edit_ledger(self, old, new, name="account-transactions-2024.csv", count=1):
        path = self.ws.cwd / "inputs" / "ledgers" / name
        text = path.read_bytes().decode("utf-8")
        self.assertEqual(text.count(old), count)
        path.write_bytes(text.replace(old, new).encode("utf-8"))

    def everything_written(self) -> str:
        """stdout, stderr, every request, and every file `author` left behind."""
        files = [p for p in self.ws.cwd.rglob("*") if p.is_file() and "inputs" not in p.relative_to(self.ws.cwd).parts
                 and p.name != "name-map.json"]
        return "\n".join(p.read_text(encoding="utf-8") for p in files)

    def assert_no_collision_text(self, r, fake):
        everywhere = (r.stdout + r.stderr + fake.sent_text() + self.everything_written()).casefold()
        for text in COLLIDING:
            self.assertNotIn(text, everywhere, text)


class RepeatedCollisionTest(CollisionCase):
    def test_the_workspace_really_blocks_the_synthetic_words(self):
        derived = ledger.derive(self.ws.cwd / "inputs" / "ledgers", self.ws.cwd)
        detector = LeakDetector(NameIndex(derived.ledgers, load_allowlist(self.ws.cwd)))
        for text in COLLIDING:
            self.assertTrue(detector.leaks(text), text)
        self.assertFalse(detector.leaks("printer paper order"))

    def test_the_reported_failure_now_promotes_an_unreviewed_bundle(self):
        # First answer: valid except three matches. The re-ask answer: three new matches at the
        # same paths. Before the fix this exited 4 after the one re-ask and promoted nothing.
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_again(1)))
        r = self.ok(fake)
        self.assertEqual(fake.parts().count("variants-01"), 2)  # one re-ask, no more
        self.assertRegex(r.stdout, r"promoted bundles/draft-[0-9a-f]{12}/ \(reviewed: false\)")
        self.assertEqual(len(list((self.ws.cwd / "bundles").iterdir())), 1)
        self.assert_no_collision_text(r, fake)

    def test_what_survives_is_a_valid_draft(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_again(1)))
        self.ok(fake)
        saved = self.saved("variants-01")["draft"]
        for v in saved["items"]:
            self.assertGreaterEqual(len(v["descriptive"]) + len(v["vendor"]), 3)
        for v in [v for v in saved["items"] if v["terse"]][:3]:  # the items that carried the matches
            # the texts kept from the first answer (one match dropped), then new ones only until the item has its three
            self.assertEqual(len(v["descriptive"]), 3)
            self.assertEqual(len(set(v["descriptive"])), 3)
            self.assertFalse(any(t.endswith(tuple(COLLIDING)) for t in v["descriptive"]))

    def test_the_re_ask_is_targeted_and_never_repeats_a_matching_text(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_again(1)))
        self.ok(fake)
        first, again = [q for q in fake.requests if q.part == "variants-01"]
        self.assertEqual(first.problems, ())
        self.assertEqual(len(again.input["items"]), 3)  # only the three items left under the minimum (issue #36)
        self.assertLess(len(again.input["items"]), len(first.input["items"]))
        self.assertEqual((again.problems, again.previous), ((), None))  # nothing of the first answer is sent back
        for item in again.input["items"]:
            self.assertEqual(item["need"], {"descriptive": 3, "terse": 0})  # one short, plus the spare of two
            self.assertEqual(len(item["have"]["descriptive"]), 2)
        sent = json.dumps(again.content(), ensure_ascii=False)
        for text in COLLIDING:
            self.assertNotIn(text, sent)
        self.assertEqual((again.model, again.part), (first.model, first.part))

    def test_diagnostics_are_by_location_only(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_again(1)))
        r = self.ok(fake)
        self.assertRegex(r.stderr, r"draft `variants-01`: dropped 3 texts that match a ledger name .*"
                                   r"\$\.items\[\d+\]\.descriptive\[[12]\]")
        self.assertNotIn("delivery run", r.stderr)

    def test_a_few_matches_with_spare_variants_are_dropped_without_a_re_ask(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0, spare=1)))
        r = self.ok(fake)
        self.assertEqual(fake.parts().count("variants-01"), 1)
        self.assertIn("dropped 3 texts that match a ledger name", r.stderr)
        self.assert_no_collision_text(r, fake)

    def test_a_rerun_resumes_the_recovered_draft_without_a_call(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0, spare=1)))
        self.ok(fake)
        again = ScriptedLLM()
        r = self.ok(again)
        self.assertEqual(again.requests, [])
        self.assertIn("6 parts reused from saved drafts", r.stdout)

    def test_matches_that_leave_an_item_short_twice_still_exit_4(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_again(1, repeat=True)))
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn("draft `variants-01` failed its schema again after one re-ask", r.stderr)
        self.assertIn("needs at least 3", r.stderr)
        self.assertEqual(fake.parts().count("variants-01"), 2)
        self.assertNotIn("variants-01", self.saved_parts())
        self.assertFalse((self.ws.cwd / "bundles").exists())
        self.assert_no_collision_text(r, fake)

    def test_a_match_outside_the_variant_texts_is_not_recovered(self):
        # Only the texts of a variants answer are dropped; a name anywhere else still fails the draft.
        leaky = Reply(edit=lambda d: d["items"][0].update(id="snacks"))
        fake = ScriptedLLM().script("variants-01", leaky, leaky)
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertEqual(fake.parts().count("variants-01"), 2)

    def test_a_catalog_draft_that_carries_a_ledger_name_still_fails(self):
        def leak(doc):
            doc["items"][0]["sellers"][0]["vendor"] = "Swiftlane Couriers"

        fake = ScriptedLLM().script("catalog-01", Reply(edit=leak), Reply(edit=leak))
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn("carries a ledger name that is not on the brand allowlist", r.stderr)
        self.assertNotIn("swiftlane", (r.stdout + r.stderr + fake.sent_text()).casefold())
        self.assertNotIn("catalog-01", self.saved_parts())

    def test_the_pinned_model_is_still_the_only_one_asked(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_again(1)))
        self.ok(fake)
        self.assertEqual({q.model for q in fake.requests}, {MODEL})


def _good(base: str, word: str) -> str:
    return f"{base} {word}"


def _bad(base: str, round_: int, k: int) -> str:
    return f"{base} delivery r{round_}k{k}"  # "delivery" is a blocked name in this workspace


HIGH_SHORT_TERSE, HIGH_SHORT_DESC = 0, 1  # the two items left under their counts, as in the reported run


def _high_again(request):
    """The re-ask answer to `_high`: new texts for the two short items only, as many of each kind as asked, with
    matches among them. Each answer alone leaves its item short; the two together do not."""
    terse, desc = request.input["items"]
    assert (terse["need"], desc["need"]) == ({"descriptive": 0, "terse": 3}, {"descriptive": 3, "terse": 0})
    base_t, base_d = (i["id"].replace("_", " ") for i in (terse, desc))
    return {"items": [
        {"id": terse["id"], "descriptive": [], "vendor": [],
         "terse": [_good(base_t, "tt1"), _bad(base_t, 1, 0), _bad(base_t, 1, 1)]},
        {"id": desc["id"], "terse": [], "vendor": [],
         "descriptive": [_good(base_d, "gamma"), _bad(base_d, 1, 0), _bad(base_d, 1, 1)]},
    ]}


def _high(doc):
    """An edit of a valid variants draft that loses many texts to coincidences, as reported (issue #31): six items
    carry matches; two of them (`HIGH_SHORT_*`) are left short by the answer alone. The model words the short
    items differently in the re-ask (`_high_again`), so each answer alone is short but the two together are not."""
    chosen = [v for v in doc["items"] if v["terse"]][:6]
    assert len(chosen) == 6
    for n, v in enumerate(chosen):
        base = v["id"].replace("_", " ")
        v["vendor"] = []
        v["terse"] = [_good(base, "tt1"), _good(base, "tt2")]
        v["descriptive"] = [_good(base, w) for w in ("order", "supply", "restock", "refill", "extra")]
        if n == HIGH_SHORT_TERSE:
            v["descriptive"] = v["descriptive"][:4]
            v["terse"] = [_good(base, "tt0"), _bad(base, 0, 0)]
        elif n == HIGH_SHORT_DESC:
            v["descriptive"] = [_good(base, "alpha"), _good(base, "beta"), _bad(base, 0, 0)]
        else:  # spare capacity of its own, but two of its five texts match
            v["descriptive"][1] = _bad(base, 0, 1)
            v["descriptive"][3] = _bad(base, 0, 3)


class HighCollisionTest(CollisionCase):
    """Issue #31: a first answer and its one re-ask each lose many texts and two items end up short."""

    def script(self):
        return ScriptedLLM().script("variants-01", Reply(edit=_high), Reply(respond=_high_again))

    def test_the_scenario_matches_the_report(self):
        derived = ledger.derive(self.ws.cwd / "inputs" / "ledgers", self.ws.cwd)
        detector = LeakDetector(NameIndex(derived.ledgers, load_allowlist(self.ws.cwd)))
        self.assertTrue(detector.leaks(_bad("printer paper", 0, 0)))
        self.assertFalse(detector.leaks(_good("printer paper", "alpha")))

    def test_high_collision_recovery_promotes_an_unreviewed_bundle_with_one_re_ask(self):
        fake = self.script()
        r = self.ok(fake)
        self.assertEqual(fake.parts().count("variants-01"), 2)  # the existing one re-ask, no more
        self.assertRegex(r.stdout, r"promoted bundles/draft-[0-9a-f]{12}/ \(reviewed: false\)")
        self.assertEqual(len(list((self.ws.cwd / "bundles").iterdir())), 1)
        self.assertNotIn("delivery r", (r.stdout + r.stderr + fake.sent_text() + self.everything_written()).casefold())

    def test_the_two_short_items_are_restored_from_texts_that_survived(self):
        self.ok(self.script())
        saved = {v["id"]: v for v in self.saved("variants-01")["draft"]["items"]}
        chosen = list(saved.values())[:6]  # `_high` edits the first six items that have terse texts
        self.assertTrue(all(v["terse"] for v in chosen))
        terse, desc = chosen[HIGH_SHORT_TERSE], chosen[HIGH_SHORT_DESC]
        self.assertGreaterEqual(len(terse["terse"]), 2)
        self.assertGreaterEqual(len(desc["descriptive"]) + len(desc["vendor"]), 3)
        for v in chosen:
            self.assertFalse(any("delivery r" in t for t in v["descriptive"] + v["terse"]))

    def test_only_the_two_short_items_are_asked_and_only_for_the_kind_they_lack(self):
        fake = self.script()
        self.ok(fake)
        first, again = [q for q in fake.requests if q.part == "variants-01"]
        self.assertGreater(len(first.input["items"]), 2)
        needs = [i["need"] for i in again.input["items"]]
        self.assertEqual(needs, [{"descriptive": 0, "terse": 3}, {"descriptive": 3, "terse": 0}])
        self.assertNotIn("delivery r", json.dumps(again.content()))

    def test_a_surplus_never_asks_past_the_most_variants_an_item_may_have(self):
        fake = self.script()
        self.ok(fake)
        _, again = [q for q in fake.requests if q.part == "variants-01"]
        for item in again.input["items"]:
            for kind in ("descriptive", "terse"):
                self.assertLessEqual(len(item["have"][kind]) + item["need"][kind], parts.MAX_VARIANTS)

    def test_what_is_still_short_after_both_answers_still_exits_4(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_again(1, repeat=True)))
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertFalse((self.ws.cwd / "bundles").exists())


def _pieces(base: str) -> str:
    return f"{base} cable, {{pcs}} meters"  # a quantity in meters where the pack wording is pieces (issue #33)


def _mixed(round_: int, *, bad=(), spare=0):
    """`_collide`, plus pack-wording texts. Items in `bad` also carry one text that states a quantity in meters
    (and so fails the pack-wording rule), in place of one of their plain texts; `bad` items are given the first
    three texts only (one colliding at the reported path), the rest get 3 + `spare`."""
    collide = _collide(round_, spare)

    def edit(doc):
        collide(doc)
        for n, v in enumerate([v for v in doc["items"] if v["terse"]][:3]):
            if n in bad:
                v["descriptive"] = v["descriptive"][:3]
                v["descriptive"][0 if PATHS[n][1] == 2 else 1] = _pieces(v["id"].replace("_", " "))
            # the model words things differently in the re-ask, so its plain texts differ from the first answer's
            v["descriptive"] = [t if t.endswith(COLLIDING[3 * round_ + n]) or "{pcs}" in t else f"{t} r{round_}"
                                for t in v["descriptive"]]
    return edit


class MixedFailureTest(CollisionCase):
    """Issue #33: the re-ask answer loses texts to name coincidences and also carries repairable pack wording."""

    def script(self):
        # First answer: three coincidences (each item left with 2 of its 3 descriptive texts). Re-ask answer: three more,
        # and two of the items also carry a quantity in meters, leaving each with a single usable text.
        return ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_asked(_mixed(1, bad=(0, 1), spare=0))))

    def test_the_reported_failure_now_promotes_an_unreviewed_bundle_with_one_re_ask(self):
        fake = self.script()
        r = self.ok(fake)
        self.assertEqual(fake.parts().count("variants-01"), 2)  # the existing one re-ask, no more
        self.assertRegex(r.stdout, r"promoted bundles/draft-[0-9a-f]{12}/ \(reviewed: false\)")
        self.assertEqual(len(list((self.ws.cwd / "bundles").iterdir())), 1)
        self.assert_no_collision_text(r, fake)
        self.assertNotIn("meters", (r.stdout + r.stderr + fake.sent_text() + self.everything_written()).casefold())

    def test_the_short_items_are_restored_from_first_answer_texts_and_stay_valid(self):
        self.ok(self.script())
        for v in self.saved("variants-01")["draft"]["items"]:
            self.assertGreaterEqual(len(v["descriptive"]) + len(v["vendor"]), 3)
            self.assertFalse(any("{pcs}" in t and "meters" in t for t in v["descriptive"] + v["terse"]))

    def test_the_second_ask_carries_no_discarded_text(self):
        fake = self.script()
        self.ok(fake)
        _, again = [q for q in fake.requests if q.part == "variants-01"]
        self.assertEqual((again.problems, again.previous), ((), None))
        for text in COLLIDING:
            self.assertNotIn(text, json.dumps(again.content()))

    def test_the_dropped_wording_is_reported_by_location_only(self):
        r = self.ok(self.script())
        self.assertRegex(r.stderr, r"draft `variants-01`: dropped 2 texts with pack-size or quantity wording")
        self.assertNotIn("meters", r.stderr)

    def test_pack_wording_alone_twice_still_exits_4(self):
        # No coincidence anywhere: this is the ordinary twice-invalid answer.
        def meters(doc):
            v = [v for v in doc["items"] if v["terse"]][0]
            v["descriptive"][0] = _pieces(v["id"].replace("_", " "))

        fake = ScriptedLLM().script("variants-01", Reply(edit=meters), Reply(edit=meters))
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertEqual(fake.parts().count("variants-01"), 2)
        self.assertFalse((self.ws.cwd / "bundles").exists())

    def test_an_item_still_short_after_dropping_the_wording_exits_4(self):
        def twice(doc):
            _collide(0)(doc)
            v = [v for v in doc["items"] if v["terse"]][0]
            v["descriptive"][0] = f"{v['id'].replace('_', ' ')} {COLLIDING[8]}"

        # the first answer's first item loses two texts, so it has one survivor; with one usable text in the re-ask
        # that is two in all, still under the three it needs
        fake = ScriptedLLM().script("variants-01", Reply(edit=twice), Reply(respond=_asked(_mixed(1, bad=(0, 1, 2), spare=0))))
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn("needs at least 3", r.stderr)
        self.assertFalse((self.ws.cwd / "bundles").exists())

    def test_another_kind_of_invalid_text_is_not_repaired(self):
        def priced(doc):
            _mixed(1, bad=(0, 1), spare=0)(doc)
            v = [v for v in doc["items"] if v["terse"]][2]
            v["descriptive"][0] = f"{v['id'].replace('_', ' ')} for \u20b1150.00"

        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(respond=_asked(priced)))
        r = self.author(fake)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn("states a price", r.stderr)
        self.assertFalse((self.ws.cwd / "bundles").exists())


if __name__ == "__main__":
    unittest.main()
