"""`txns author` assembles, validates and promotes a bundle (ticket 15: FR-D1 to FR-D3, FR-D5, T27, T38, T39).

Driven through `main(argv)` with the scripted LLM fake and the fabricated fixture
ledgers (no network). Without scripting, the fake drafts two items per fixture
category (`<category slug>_a`, `_b`) whose text names no ledger row, so every
item is priced from its category's anchor figures, split cheapest first:

- Office Expense - Meals anchors ₱340 (3 rows, text-less) and ₱562.50 (2 rows,
  "Coffee for client review"): `_a` ₱340, `_b` ₱562.50.
- Office Supplies has no repeated price, so its observed amounts (₱480, ₱1,250)
  are its figures; it is stock with pack size 5 (the "5 reams" row).
- Sound System and Equipment: one figure, ₱45,000. The retail `_a` item is left
  out (a whole ₱1,000 on an item not approved for round figures); the big-ticket
  `_b` item keeps it.
- Software Subscriptions: subscription items with anchor day 5 (from the draft).

The gates are also run directly on hand-edited copies of a promoted bundle
(`gates.run_offline`, the seam `txns approve` re-uses).
"""

import json
import shutil
import unittest
from datetime import date
from pathlib import Path

from tests.helpers import REPO_ROOT, Workspace
from tests.llm_fake import Reply
from tests.test_author_ledger import KEY
from txns import gates, holidays
from txns.bundle import hashing, packs, store
from txns.config import load_config
from txns.money import is_round_thousand, is_tidy_cents

MODEL = "example/pinned-model"
BUNDLE_FILES = ["anchors", "catalog", "holidays", "manifest", "rate_cards", "reference", "rules",
                "storylines", "text", "vocabulary"]
STAGED = ".txns/author/bundle"


def _item(doc: dict, item_id: str) -> dict:
    return next(i for i in doc["items"] if i["id"] == item_id)


def edit_item(item_id: str, fn):
    """A Reply(edit=...) that edits one item of a catalog or variants draft."""
    return Reply(edit=lambda doc: (fn(_item(doc, item_id)), None)[1])


class BundleCase(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_author_inputs()
        self.ws.write_config(f'[author]\nmodel = "{MODEL}"\n')

    def author(self, *args, fake=None):
        return self.ws.run("author", *args, env=KEY, transport=fake)

    def ok(self, *args, fake=None):
        r = self.author(*args, fake=fake)
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        return r

    def bundles(self) -> list[str]:
        root = self.ws.cwd / "bundles"
        return sorted(p.name for p in root.iterdir()) if root.is_dir() else []

    def only_bundle(self) -> Path:
        (name,) = self.bundles()
        return self.ws.bundle_dir(name)

    def read(self, folder: Path, stem: str):
        return json.loads((folder / f"{stem}.json").read_text(encoding="utf-8"))

    def failed(self, r, gate: str):
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn(f"promotion gate", r.stderr)
        self.assertRegex(r.stdout, rf"FAIL  gate {gate} ")
        self.assertIn("bundles/ untouched", r.stderr)
        self.assertTrue((self.ws.cwd / STAGED / "manifest.json").is_file())  # kept for review


class PromotionTest(BundleCase):
    def test_promotes_a_complete_unreviewed_bundle(self):  # T38, FR-D1, FR-D3, FR-C8
        r = self.ok("--label", "v3")
        folder = self.only_bundle()
        manifest = self.read(folder, "manifest")
        self.assertEqual(folder.name, f"v3-{manifest['hash'][:12]}")
        self.assertEqual(hashing.content_hash(folder), manifest["hash"])  # the hash matches the content
        self.assertIn(f"promoted bundles/{folder.name}/ (reviewed: false)", r.stdout)
        self.assertEqual(sorted(p.stem for p in folder.iterdir()), BUNDLE_FILES)
        self.assertEqual(manifest["label"], "v3")
        self.assertIs(manifest["reviewed"], False)
        self.assertEqual(manifest["promotion"], 1)
        self.assertEqual(manifest["model"], MODEL)
        self.assertEqual(manifest["served_models"], {p: "fake/served-model-1" for p in (
            "storylines", "catalog-01", "catalog-02", "variants-01", "variants-02", "vocabulary")})
        ledgers = json.loads((self.ws.cwd / ".txns/author/ledgers.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["ledger_hashes"], ledgers["ledger_hashes"])
        self.assertTrue(manifest["interpreter"].startswith("CPython 3.12"))
        self.assertEqual(manifest["generator"], "txns 0.1.0")
        self.assertFalse((self.ws.cwd / STAGED).exists())  # the staged folder became the bundle

        # Its parts: reference.json and the committed rules unchanged, the holiday calendar from 2024 on.
        temp_ref = json.loads((self.ws.cwd / ".txns/author/reference.json").read_text(encoding="utf-8"))
        self.assertEqual(self.read(folder, "reference"), temp_ref)
        committed_rules = json.loads((REPO_ROOT / "inputs/bundle-rules.json").read_text(encoding="utf-8"))
        self.assertEqual(self.read(folder, "rules"), committed_rules)
        calendar = holidays.load_committed(REPO_ROOT)
        self.assertEqual(self.read(folder, "holidays"), holidays.for_years(calendar, [2024, 2025, 2026, 2027]))
        self.assertEqual(self.read(folder, "vocabulary"), {"date_tails": ["({mon} {d}, {yyyy})", "for {m}/{d}"]})
        self.assertEqual(sorted(self.read(folder, "storylines")),
                         ["errands", "office_pantry", "post_projects", "software_stack"])

    def test_promoted_bundle_generates(self):  # gate 8 holds for the real run too
        self.ok()
        r = self.ws.run("generate", "--seed", "7")
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        self.assertIn("is unreviewed", r.stderr)
        self.assertTrue(r.rows)

    def test_latest_follows_promotion_order(self):  # FR-D5
        self.ok("--label", "first")
        r = self.ok("--label", "second")  # drafts reused, same content, new label
        self.assertIn("0 LLM calls this run", r.stdout)
        names = self.bundles()
        self.assertEqual(len(names), 2)
        latest = store.find(self.ws.cwd / "bundles", "latest")
        self.assertTrue(latest.name.startswith("second-"))
        self.assertEqual(self.read(latest, "manifest")["promotion"], 2)

    def test_same_label_and_content_is_not_promoted_twice(self):
        self.ok("--label", "v1")
        before = self.bundles()
        r = self.ok("--label", "v1")
        self.assertIn("already promoted with this exact content", r.stdout)
        self.assertEqual(self.bundles(), before)

    def test_same_name_taken_by_a_hand_edited_folder_exits_4(self):  # like approve (review 8)
        self.ok("--label", "v1")
        folder = self.only_bundle()
        storylines = self.read(folder, "storylines")
        next(iter(storylines.values()))["description"] = "Edited by hand"
        (folder / "storylines.json").write_text(json.dumps(storylines, indent=2) + "\n", encoding="utf-8")
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        r = self.author("--label", "v1")
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertNotIn("already promoted with this exact content", r.stdout)
        self.assertIn(f"{folder.name} already exists and has been hand-edited", r.stderr)
        self.assertEqual(self.bundles(), [folder.name])
        self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, before)
        self.assertTrue((self.ws.cwd / STAGED / "manifest.json").is_file())  # kept for review

    def test_bad_label_exits_2_before_any_call(self):
        r = self.author("--label", "../escape")
        self.assertEqual(r.code, 2)
        self.assertIn("bad --label", r.stderr)
        self.assertEqual(self.ws.llm.requests, [])

    def test_missing_committed_inputs_exit_2_before_any_call(self):
        for name in ("price-anchors.json", "bundle-rules.json"):
            with self.subTest(name):
                ws = Workspace(self)
                ws.install_author_inputs()
                ws.write_config(f'[author]\nmodel = "{MODEL}"\n')
                (ws.cwd / "inputs" / name).unlink()
                r = ws.run("author", env=KEY)
                self.assertEqual(r.code, 2, r.stdout + r.stderr)
                self.assertIn(name, r.stderr)
                self.assertEqual(ws.llm.requests, [])


class PricingTest(BundleCase):
    def priced(self, fake=None) -> tuple[Path, dict, dict, dict, dict, str]:
        r = self.ok(fake=fake)
        folder = self.only_bundle()
        return (folder, self.read(folder, "catalog")["items"], self.read(folder, "rate_cards"),
                self.read(folder, "text"), self.read(folder, "anchors"), r.stdout)

    def test_prices_come_from_ledger_anchors_only(self):  # FR-D2 gate 2, FR-C3
        _, catalog, cards, _, anchors, _ = self.priced()
        base = lambda i: [p["unit_price"] for p in cards[i]["points"]]
        self.assertEqual(base("office_expense_meals_a")[0], 34000)
        self.assertEqual(base("office_expense_meals_b")[0], 56250)
        self.assertEqual(base("delivery_fee_a")[0], 9500)
        self.assertEqual(base("delivery_fee_b")[0], 19200)
        self.assertEqual(base("software_subscriptions_a"), [34900])  # one point for a subscription
        self.assertEqual(base("software_subscriptions_b"), [264200])  # the most seen figure (lower on a tie)
        self.assertEqual(base("sound_system_and_equipment_b"), [4500000])  # big ticket: round figure allowed
        self.assertEqual(anchors["items"]["office_expense_meals_a"], {"basis": "category", "figures": [34000]})
        for item_id, card in cards.items():
            prices = [p for pt in card["points"] for p in [pt["unit_price"]] + [t["unit_price"] for t in pt.get("tiers", [])]]
            prices += [p["unit_price"] for s in card.get("steps", []) for p in s["points"]]
            self.assertTrue(all(is_tidy_cents(p) for p in prices), item_id)
            if catalog[item_id]["class"] == "retail":
                sellers = [p["seller"] for p in card["points"]]
                self.assertTrue(2 <= len(sellers) <= 4 and len(set(sellers)) == len(sellers), item_id)
                self.assertFalse(any(is_round_thousand(p) for p in prices), item_id)

    def test_item_without_a_usable_anchor_is_left_out_and_reported(self):
        _, catalog, _, _, anchors, out = self.priced()
        self.assertNotIn("sound_system_and_equipment_a", catalog)
        self.assertIn("left out `sound_system_and_equipment_a` (Sound System and Equipment)", out)
        self.assertEqual([x["id"] for x in anchors["left_out"]], ["sound_system_and_equipment_a"])

    def test_item_text_picks_its_ledger_rows(self):
        coffee = ["Coffee for the client review", "Brewed coffee for the team", "Coffee run for the edit"]
        fake = self.ws.llm.script("variants-01", edit_item("office_expense_meals_a",
                                                            lambda v: v.update(descriptive=coffee)))
        _, _, cards, _, anchors, _ = self.priced(fake)
        # "coffee" names the ₱562.50 "Coffee for client review" rows; `_b` takes the remaining figure.
        self.assertEqual(cards["office_expense_meals_a"]["points"][0]["unit_price"], 56250)
        self.assertEqual(anchors["items"]["office_expense_meals_a"], {"basis": "item text", "figures": [56250]})
        self.assertEqual(cards["office_expense_meals_b"]["points"][0]["unit_price"], 34000)

    def test_committed_anchor_prices_an_item_the_ledgers_never_show(self):
        lto = ["LTO-8 tape cartridge", "LTO-8 data tape for the archive", "Archive tape, LTO-8"]
        fake = self.ws.llm.script("variants-02", edit_item("storage_devices_b", lambda v: v.update(descriptive=lto)))
        _, catalog, cards, text, anchors, out = self.priced(fake)
        card = cards["storage_devices_b"]
        self.assertEqual([p["unit_price"] for p in card["points"]], [402600, 460100])
        self.assertEqual([q["qty"] for q in card["quantities"]], [1, 5, 10, 20])
        self.assertEqual(card["points"][0]["tiers"], [{"min_qty": 4, "unit_price": 382500},  # 5% and 10% off
                                                      {"min_qty": 10, "unit_price": 362300}])
        record = anchors["items"]["storage_devices_b"]
        self.assertEqual((record["basis"], record["key"], record["confidence"]), ("committed", "lto8_cartridge", "low"))
        self.assertNotIn("pack_pcs", catalog["storage_devices_b"])  # the anchor prices one cartridge
        self.assertNotIn("box of", " ".join(text["storage_devices_b"]["descriptive"]))  # {pcs} variant dropped

    def test_committed_anchor_without_a_price_leaves_the_item_out(self):
        barcode = ["Barcode labels for tapes", "Barcode stickers, archive", "Printed barcode labels"]
        fake = self.ws.llm.script("variants-01", edit_item("office_supplies_b", lambda v: v.update(descriptive=barcode)))
        _, catalog, _, _, anchors, out = self.priced(fake)
        self.assertNotIn("office_supplies_b", catalog)
        self.assertIn("left out `office_supplies_b` (Office Supplies): named by committed anchor `barcode_labels`, "
                      "which has no sourced price", out)
        self.assertIn("office_supplies_b", [x["id"] for x in anchors["left_out"]])

    def test_pack_size_fills_pcs_and_prices_the_pack(self):  # FR-C3, ticket 11 pack_pcs
        _, catalog, cards, text, _, _ = self.priced()
        self.assertEqual(catalog["office_supplies_a"]["pack_pcs"], 5)  # the "5 reams" row
        self.assertEqual(catalog["office_supplies_a"]["goods"], "stock")
        self.assertIn("office supplies a, box of 5", text["office_supplies_a"]["descriptive"])
        self.assertTrue(packs.states_pack("office supplies a, box of 5", 5))
        self.assertEqual(cards["office_supplies_a"]["points"][0]["unit_price"], 48000)
        self.assertNotIn("{pcs}", json.dumps(text))

    def test_pcs_variants_are_dropped_without_a_pack_size(self):
        fake = self.ws.llm.script("catalog-01", edit_item("delivery_fee_a", lambda i: i.update(goods="stock")))
        _, catalog, _, text, _, _ = self.priced(fake)
        self.assertNotIn("pack_pcs", catalog["delivery_fee_a"])  # Delivery Fee shows no pack size
        self.assertEqual(text["delivery_fee_a"]["descriptive"],
                         ["delivery fee a purchase", "delivery fee a for the office", "delivery fee a order"])

    def test_one_seller_retail_item_gets_a_second_unnamed_seller(self):
        fake = self.ws.llm.script("catalog-01", edit_item("delivery_fee_a", lambda i: i.update(sellers=i["sellers"][:1])))
        _, _, cards, _, _, _ = self.priced(fake)
        points = cards["delivery_fee_a"]["points"]
        self.assertEqual([p["seller"] for p in points], ["vendor-2", "delivery-fee-a-shop2"])
        self.assertEqual([p["unit_price"] for p in points], [9500, 9800])  # +3%, whole pesos like the anchor

    def test_yearly_steps_after_the_ledgers(self):  # FR-F2
        _, _, cards, _, _, _ = self.priced()
        steps = cards["delivery_fee_a"]["steps"]
        self.assertEqual([s["date"] for s in steps], ["2026-01-01", "2027-01-01"])
        prices = [9500] + [s["points"][0]["unit_price"] for s in steps]
        for old, new in zip(prices, prices[1:]):
            self.assertTrue(1.03 * old <= new <= 1.15 * old, (old, new))
        self.assertNotIn("steps", cards["sound_system_and_equipment_b"])  # big tickets keep their figure

    def test_missing_anchor_day_is_filled(self):
        fake = self.ws.llm.script("catalog-01", edit_item("software_subscriptions_a", lambda i: i.pop("params")))
        _, catalog, _, _, _, _ = self.priced(fake)
        self.assertIn(catalog["software_subscriptions_a"]["params"]["anchor_day"], [1, 2, 11, 25, 29])

    def test_round_figure_event_item_is_approved(self):  # FR-F6, ticket 08 handoff
        fake = self.ws.llm.script("catalog-02", edit_item("sound_system_and_equipment_a", lambda i: i.update(
            archetype="deposit_balance", params={"per_quarter": 1.0})))
        _, catalog, cards, _, _, _ = self.priced(fake)
        self.assertIs(catalog["sound_system_and_equipment_a"]["round_figures"], True)  # ₱45,000, a venue-style figure
        self.assertEqual(cards["sound_system_and_equipment_a"]["points"], [{"seller": "vendor-7", "unit_price": 4500000}])
        self.assertNotIn("steps", cards["sound_system_and_equipment_a"])  # a step would break the round figure

    def test_decimal_draft_is_priced_per_purchase(self):
        fake = self.ws.llm.script("catalog-01", edit_item("delivery_fee_a", lambda i: i.update(decimal=True)))
        _, catalog, cards, _, _, out = self.priced(fake)
        self.assertNotIn("decimal", catalog["delivery_fee_a"])
        self.assertTrue(all(isinstance(q["qty"], int) for q in cards["delivery_fee_a"]["quantities"]))
        self.assertIn("item `delivery_fee_a`: decimal quantities dropped: no per-measure anchor", out)

    def test_terse_text_shared_across_prices_stays_with_one_item(self):  # FR-D2 gate 5
        fake = self.ws.llm.script("variants-01", edit_item(
            "office_expense_meals_b", lambda v: v["terse"].append("office expense meals a")))
        _, _, _, text, _, _ = self.priced(fake)
        self.assertIn("office expense meals a", text["office_expense_meals_a"]["terse"])
        self.assertNotIn("office expense meals a", text["office_expense_meals_b"]["terse"])

    def test_same_drafts_give_the_same_bundle(self):
        self.ok("--label", "a")
        first = self.only_bundle()
        other = Workspace(self)
        other.install_author_inputs()
        other.write_config(f'[author]\nmodel = "{MODEL}"\n')
        self.assertEqual(other.run("author", "--label", "a", env=KEY).code, 0)
        (name,) = [p.name for p in (other.cwd / "bundles").iterdir()]
        self.assertEqual(name, first.name)


class GateFailureTest(BundleCase):
    def test_duplicate_item_exits_4_and_leaves_bundles_untouched(self):  # T39
        self.ok("--label", "good")
        before = self.bundles()
        fake = self.ws.llm.script("variants-01", edit_item(
            "office_expense_meals_b", lambda v: v["descriptive"].append("Office Expense Meals A purchase")))
        self.ws.remove(".txns/author/drafts")  # draft again with the duplicate
        r = self.author("--label", "bad", fake=fake)
        self.failed(r, "4")
        self.assertIn("items `office_expense_meals_a` and `office_expense_meals_b` are the same item", r.stdout)
        self.assertEqual(self.bundles(), before)
        self.assertTrue(store.find(self.ws.cwd / "bundles", "latest").name.startswith("good-"))

    def test_smoke_generate_failure_exits_4(self):  # T39, gate 8: gap rules cannot reach the target
        self.ws.write_config(f'target = 1_000_000_000\nband_pct = 2\n[author]\nmodel = "{MODEL}"\n',
                             fixture_defaults=False)
        r = self.author()
        self.failed(r, "8")
        self.assertIn("would exit 5", r.stdout)
        self.assertEqual(self.bundles(), [])

    def test_smoke_hard_scorecard_failure_exits_4(self):  # T39, gate 8
        rules = self.ws.cwd / "inputs" / "bundle-rules.json"
        data = json.loads(rules.read_text(encoding="utf-8"))
        data["messiness"]["duplicate_group_rate"] = 0.4  # far above the ledger's: the duplicates check fails hard
        rules.write_text(json.dumps(data), encoding="utf-8")
        r = self.author()
        self.failed(r, "8")
        self.assertIn("hard scorecard failure on seed 1", r.stdout)
        self.assertIn("duplicates", r.stdout)
        self.assertEqual(self.bundles(), [])

    def test_prices_outside_tolerance_exit_4(self):  # T39, gate 2 (one tolerance setting)
        self.ws.write_config(f'tolerance_pct = 1\n[author]\nmodel = "{MODEL}"\n')
        r = self.author()
        self.failed(r, "2")
        self.assertIn("more than 1% from every anchor", r.stdout)
        self.assertEqual(self.bundles(), [])


class OfflineGatesTest(BundleCase):
    """The gate set on hand-edited copies of a promoted bundle (what `txns approve` re-runs)."""

    def setUp(self):
        super().setUp()
        self.ok()
        self.cfg = load_config(self.ws.cwd, None)
        self.index = gates.name_index(self.ws.cwd, self.ws.cwd / "inputs" / "ledgers")

    def edited(self, stem: str, fn) -> Path:
        copy = self.ws.cwd / "edited"
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(self.only_bundle(), copy)
        data = self.read(copy, stem)
        fn(data)
        (copy / f"{stem}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return copy

    def run_gates(self, folder: Path) -> gates.GateReport:
        return gates.run_offline(folder, config=self.cfg, today=date(2026, 10, 5), index=self.index)

    def failures(self, folder: Path) -> dict[str, tuple[str, ...]]:
        return {r.gate: r.problems or (r.skipped,) for r in self.run_gates(folder).failed()}

    def test_promoted_bundle_passes(self):
        report = self.run_gates(self.only_bundle())
        self.assertTrue(report.ok, report.lines())
        self.assertEqual([r.gate for r in report.results], ["2", "3", "4", "4-6", "7", "8"])

    def test_over_long_variant(self):  # T27
        folder = self.edited("text", lambda t: t["delivery_fee_a"]["descriptive"].append("x" * 101))
        self.assertIn("longer than 100 characters", " ".join(self.failures(folder)["4-6"]))

    def test_price_far_from_its_anchor(self):  # gate 2
        def far(cards):
            cards["delivery_fee_a"]["points"][0]["unit_price"] = 95000
            del cards["delivery_fee_a"]["steps"]

        folder = self.edited("rate_cards", far)
        problems = self.failures(folder)
        self.assertEqual(list(problems), ["2"])
        self.assertIn("item `delivery_fee_a`: price 950.00 is more than 25% from every anchor", problems["2"][0])

    def test_pack_size_not_observed(self):  # gate 2
        folder = self.edited("catalog", lambda c: c["items"]["office_supplies_a"].update(pack_pcs=12))
        self.assertIn("pack size 12 is not an observed or committed pack size", " ".join(self.failures(folder)["2"]))

    def test_real_ledger_name(self):  # gate 3, reported by location only
        folder = self.edited("text", lambda t: t["delivery_fee_a"]["terse"].append("Swiftlane Couriers"))
        problems = self.failures(folder)["3"]
        self.assertEqual(problems, ("text.json: $.delivery_fee_a.terse[2]",))

    def test_unknown_archetype(self):  # gate 7: the bundle still loads, the engine has no such timing
        folder = self.edited("catalog", lambda c: c["items"]["delivery_fee_a"].update(archetype="weekly_splurge"))
        problems = self.failures(folder)
        self.assertIn("archetype `weekly_splurge` is not one of", " ".join(problems["7"]))
        self.assertIn("8", problems)  # and generate cannot run it

    def test_retail_item_needs_two_sellers(self):  # gate 7 (ticket 09 handoff)
        folder = self.edited("rate_cards", lambda c: c["delivery_fee_a"].update(
            points=c["delivery_fee_a"]["points"][:1], steps=[{"date": s["date"], "points": s["points"][:1]}
                                                             for s in c["delivery_fee_a"]["steps"]]))
        self.assertIn("2 to 4 price points", " ".join(self.failures(folder)["7"]))

    def test_round_figure_price_step(self):  # gate 7 checks dated price steps too (review 5)
        def round_step(cards):
            card = cards["delivery_fee_a"]
            card["points"][0]["unit_price"], card["points"][1]["unit_price"] = 92000, 94000
            card["steps"][0]["points"] = [{"unit_price": 100000}, {"unit_price": 100000}]  # ₱1,000 at every qty
            card["steps"][1]["points"] = [{"unit_price": 105000}, {"unit_price": 108000}]

        problems = " ".join(self.failures(self.edited("rate_cards", round_step))["7"])
        self.assertIn("item `delivery_fee_a`: price step 2026-01-01 point 0 gives a whole-₱1,000 amount", problems)
        self.assertNotIn("price point 0", problems)  # the base card is fine

    def test_malformed_vendor_variants_are_not_duplicate_items(self):  # gate 4 (review 9)
        def no_separator(text):
            for item in ("delivery_fee_a", "office_supplies_a"):
                text[item]["vendor"][0]["text"] = f"Vendor{len(item)} purchase order"

        failures = self.failures(self.edited("text", no_separator))
        self.assertNotIn("4", failures)  # used to call both items '' and report them as the same item
        self.assertIn("4-6", failures)  # the malformed variants are gate 4-6's to report

    def test_bundle_that_does_not_load(self):  # gate 7; the gates needing a bundle do not run
        folder = self.edited("rate_cards", lambda c: c["delivery_fee_a"]["points"][0].update(unit_price=9512))
        failures = self.failures(folder)
        self.assertIn("untidy cents", failures["7"][0])
        self.assertEqual(failures["8"], ("the bundle does not load, see gate 7",))
        self.assertNotIn("3", failures)
