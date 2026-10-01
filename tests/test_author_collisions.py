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

import unittest

from tests.llm_fake import Reply, ScriptedLLM
from tests.test_author_drafts import MODEL, DraftCase
from txns import ledger
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
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(edit=_collide(1, spare=2)))
        r = self.ok(fake)
        self.assertEqual(fake.parts().count("variants-01"), 2)  # one re-ask, no more
        self.assertRegex(r.stdout, r"promoted bundles/draft-[0-9a-f]{12}/ \(reviewed: false\)")
        self.assertEqual(len(list((self.ws.cwd / "bundles").iterdir())), 1)
        self.assert_no_collision_text(r, fake)

    def test_what_survives_is_a_valid_draft(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(edit=_collide(1, spare=2)))
        self.ok(fake)
        saved = self.saved("variants-01")["draft"]
        for v in saved["items"]:
            self.assertGreaterEqual(len(v["descriptive"]) + len(v["vendor"]), 3)
        three = [v for v in saved["items"] if any(t.endswith(" extra") for t in v["descriptive"])]
        self.assertEqual(len(three), 3)
        for v in three:
            self.assertEqual(len(v["descriptive"]), 4)  # five asked for, the colliding one dropped

    def test_the_re_ask_is_targeted_and_never_repeats_a_matching_text(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(edit=_collide(1, spare=2)))
        self.ok(fake)
        first, again = [q for q in fake.requests if q.part == "variants-01"]
        self.assertEqual(first.problems, ())
        self.assertIsNotNone(again.previous)  # the answer minus the matching texts, safe to send back
        told = [p for p in again.problems if "coincides with a business name from the books" in p]
        self.assertEqual(len(told), 3)  # one line per affected item, by location only
        for p in told:
            self.assertRegex(p, r"^\$\.items\[\d+\] \(`\w+`\): 1 variant text removed")
        self.assertEqual(len(again.problems), 6)  # and the 3 items left under the minimum
        for text in COLLIDING:
            self.assertNotIn(text, again.previous)
            self.assertNotIn(text, " ".join(again.problems))
        self.assertEqual((again.input, again.schema, again.model), (first.input, first.schema, first.model))

    def test_diagnostics_are_by_location_only(self):
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(edit=_collide(1, spare=2)))
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
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(edit=_collide(1)))
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
        fake = ScriptedLLM().script("variants-01", Reply(edit=_collide(0)), Reply(edit=_collide(1, spare=2)))
        self.ok(fake)
        self.assertEqual({q.model for q in fake.requests}, {MODEL})


if __name__ == "__main__":
    unittest.main()
