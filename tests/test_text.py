"""Believable item text (ticket 10: FR-H1, FR-D2 gates 4-6, FR-I4 checks 1, 7, 8, T25).

Generated text is checked from the written CSV against the bundle, the way a
reviewer would; text-to-item mapping through the scorecard-as-a-function seam;
the bundle text gates through `text_problems`, which `author`/`approve` run.
"""

import unittest
from collections import Counter, defaultdict
from decimal import Decimal

from tests.helpers import Workspace, add_item, load_fixture_files
from txns.bundle import store
from txns.bundle.text_rules import text_problems
from txns.scorecard import score_csv

FULL_YEAR = 'start = "2025-01-01"\nend = "2025-12-31"\n'
HEADER = "date_of_transaction,qty,unit_price,item/service\n"


def centavos(row):
    return int(Decimal(row["unit_price"]) * 100)


def check(run_json, name):
    return next(c for c in run_json["scorecard"]["checks"] if c["name"] == name)


def variants_by_text(files):
    """text -> (item id, kind, seller) from the source bundle files."""
    index = {}
    for item_id, v in files["text"].items():
        for t in v.get("descriptive", []):
            index[t] = (item_id, "descriptive", None)
        for x in v.get("vendor", []):
            index[x["text"]] = (item_id, "vendor", x["seller"])
        for t in v.get("terse", []):
            index[t] = (item_id, "terse", None)
    return index


class GeneratedTextTest(unittest.TestCase):
    """A full year from the fixture bundle, read back from the CSV."""

    @classmethod
    def setUpClass(cls):
        cls.files = load_fixture_files()
        cls.index = variants_by_text(cls.files)

    def setUp(self):
        self.ws = Workspace(self)

    def generate(self, mutate=None, seed="42"):
        self.ws.install_bundle(mutate=mutate)
        self.ws.write_config(FULL_YEAR)
        r = self.ws.run("generate", "--seed", seed)
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        return r

    def test_every_row_names_a_catalog_item_and_vendors_match_the_seller(self):  # T25
        r = self.generate()
        vendors = {x["text"].split(" - ")[0].lower() for v in self.files["text"].values() for x in v.get("vendor", [])}
        kinds = Counter()
        for row in r.rows:
            text = row["item/service"]
            self.assertIn(text, self.index, "every row uses one of its item's variants")
            self.assertNotIn(text.lower(), vendors, "no vendor-only text")
            item_id, kind, seller = self.index[text]
            kinds[kind] += 1
            if kind == "vendor":
                sold = {p["unit_price"] for p in self.files["rate_cards"][item_id]["points"] if p["seller"] == seller}
                self.assertIn(centavos(row), sold, f"{text!r} at {row['unit_price']}: vendor does not sell that price")
        self.assertTrue(all(kinds[k] > 0 for k in ("descriptive", "vendor", "terse")), kinds)
        self.assertLess(kinds["vendor"], kinds["descriptive"], "vendor-prefixed text is a minority")

    def test_terse_share_follows_reference_per_category(self):  # FR-H1
        r = self.generate()
        categories = {i: e["category"] for i, e in self.files["catalog"]["items"].items()}
        rows, terse = Counter(), Counter()
        for row in r.rows:
            item_id, kind, _ = self.index[row["item/service"]]
            rows[categories[item_id]] += 1
            terse[categories[item_id]] += kind == "terse"
        reference = self.files["reference"]["terse_share"]
        for cat, n in rows.items():
            self.assertAlmostEqual(terse[cat] / n, reference[cat], delta=0.25 * reference[cat], msg=cat)
        c = check(r.run_json, "terse_share")
        self.assertEqual((c["status"], c["section"], c["hard"]), ("pass", "anomaly", False), c["detail"])
        self.assertEqual(set(c["value"]), set(rows))

    def test_terse_share_is_bundle_data(self):
        def shares(files):
            files["reference"]["terse_share"].update({"Transportation": 0.9, "Meals and Entertainment": 0.0})

        r = self.generate(mutate=shares)
        by_item = defaultdict(Counter)
        for row in r.rows:
            item_id, kind, _ = self.index[row["item/service"]]
            by_item[item_id][kind] += 1
        parking = by_item["errands.parking"]
        self.assertAlmostEqual(parking["terse"] / sum(parking.values()), 0.9, delta=0.05)
        for item_id in ("pantry.coffee", "pantry.snacks"):
            self.assertEqual(by_item[item_id]["terse"], 0, item_id)
            self.assertGreater(sum(by_item[item_id].values()), 0)

    def test_big_ticket_rows_are_never_terse(self):  # FR-H1, T25
        def big(files):
            add_item(
                files,
                "events.stage",
                storyline="events",
                points=[(1_500_000, "stage-1")],
                quantities=[(1, 1)],
                descriptive=["Stage hire for launch event", "Stage and truss hire", "Event stage rental, one day"],
                terse=["Stage"],  # a gate failure, but generate must still never use it
                price_class="big_ticket",
                category="Events",
                params={"per_week": 1.0},
            )

        r = self.generate(mutate=big)
        texts = [row["item/service"] for row in r.rows if centavos(row) == 1_500_000]
        self.assertGreater(len(texts), 10)
        self.assertNotIn("Stage", texts)

    def test_category_without_reference_share_still_gets_some_terse_text(self):
        r = self.generate(mutate=lambda f: f["reference"].pop("terse_share"))
        kinds = Counter(self.index[row["item/service"]][1] for row in r.rows)
        self.assertGreater(kinds["terse"], 0.15 * len(r.rows))
        self.assertLess(kinds["terse"], 0.45 * len(r.rows))
        self.assertIn("no ledger reference", check(r.run_json, "terse_share")["detail"])

    def test_terse_share_check_only_warns(self):  # FR-I4 (8)
        def far(files):
            files["reference"]["terse_share"] = {cat: 0.02 for cat in files["reference"]["terse_share"]}

        r = self.generate(mutate=far)
        c = check(r.run_json, "terse_share")
        self.assertEqual(c["status"], "warn")
        self.assertIn("vs ledger 2%", c["detail"])
        self.assertFalse(r.run_json["scorecard"]["hard_failure"])


class TextToItemTest(unittest.TestCase):  # FR-D2 gate 5, FR-I4 (1) and (7)
    def setUp(self):
        self.ws = Workspace(self)

    def bundle(self, mutate=None):
        return store.load(self.ws.bundle_dir(self.ws.install_bundle(mutate=mutate)))

    def test_price_checks_key_on_item_id_across_text_variants(self):
        bundle = self.bundle()
        two_prices = (
            "2026-07-01,1,165.00,Coffee\n"
            "2026-07-02,1,165.00,Bean Harbor Cafe - Coffee for client review\n"
            "2026-07-03,2,180.00,Iced coffee for editing team\n"
        )
        report = score_csv(HEADER + two_prices, bundle)
        self.assertEqual(report.get("price_stability").status, "pass")
        self.assertEqual(report.get("distinct_per_item.prices").value, {"Meals and Entertainment": 2.0})
        self.assertEqual(report.get("distinct_per_item.quantities").value, {"Meals and Entertainment": 2.0})
        self.assertEqual(report.get("terse_share").value, {"Meals and Entertainment": round(1 / 3, 4)})

        report = score_csv(HEADER + two_prices + "2026-07-06,1,170.00,Iced coffee\n", bundle)
        stability = report.get("price_stability")
        self.assertEqual((stability.status, stability.value), ("fail", {"pantry.coffee": 3}))

    def test_shared_terse_string_maps_to_one_item_when_price_points_match(self):
        def twins(files):
            for item_id, desc in (("stock.tape_a", "LTO tape for archive"), ("stock.tape_b", "LTO tape for client")):
                add_item(
                    files,
                    item_id,
                    storyline="stock",
                    points=[(99_500, "media-1"), (104_000, "media-2")],
                    quantities=[(1, 3), (2, 1)],
                    descriptive=[f"{desc}, {n}" for n in ("rush", "backup", "delivery")],
                    terse=["LTO tape", f"Tape {item_id[-1]}"],
                )

        bundle = self.bundle(twins)
        self.assertEqual(text_problems(bundle), [])
        report = score_csv(HEADER + "2026-07-01,1,995.00,LTO tape\n2026-07-02,1,1040.00,LTO tape\n", bundle)
        self.assertEqual(report.get("price_stability").status, "pass")
        self.assertEqual(report.get("distinct_per_item.prices").value, {"Office Expenses": 2.0})

        def twins_apart(files):
            twins(files)
            files["rate_cards"]["stock.tape_b"]["points"][1]["unit_price"] = 110_000

        problems = text_problems(self.bundle(twins_apart))
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("'LTO tape' is used by stock.tape_a, stock.tape_b", problems[0])


class TextGateTest(unittest.TestCase):  # FR-D2 gates 4-6, FR-C3
    def setUp(self):
        self.ws = Workspace(self)

    def problems(self, mutate=None):
        return text_problems(store.load(self.ws.bundle_dir(self.ws.install_bundle(mutate=mutate))))

    def assertRejected(self, mutate, *fragments):
        problems = self.problems(mutate)
        joined = "\n".join(problems)
        for fragment in fragments:
            self.assertIn(fragment, joined)
        return problems

    def test_fixture_bundle_passes(self):
        self.assertEqual(self.problems(), [])

    def test_too_few_variants(self):
        def few(files):
            files["text"]["pantry.snacks"]["descriptive"].pop()
            files["text"]["errands.parking"]["terse"].pop()

        self.assertRejected(
            few,
            "item `pantry.snacks`: 2 descriptive variant(s), needs at least 3",
            "item `errands.parking`: 1 terse variant(s), needs at least 2",
        )

    def test_big_ticket_items_have_no_terse_variants(self):
        def big(files):
            files["catalog"]["items"]["errands.courier"]["class"] = "big_ticket"
            files["rate_cards"]["errands.courier"]["points"] = files["rate_cards"]["errands.courier"]["points"][:1]  # big-ticket: one point

        self.assertRejected(big, "item `errands.courier`: big-ticket items have no terse variants")

        def big_ok(files):
            big(files)
            files["text"]["errands.courier"]["terse"] = []

        self.assertEqual(self.problems(big_ok), [])

    def test_blank_and_vendor_only_text(self):
        def bad(files):
            files["text"]["pantry.water"]["terse"].append("Clearspring Refills")
            files["text"]["pantry.snacks"]["descriptive"].append("  ")
            files["text"]["errands.courier"]["vendor"].append({"seller": "courier-2", "text": "Swiftlane Couriers - "})
            files["text"]["pantry.coffee"]["descriptive"].append("cafe-2")

        self.assertRejected(
            bad,
            "variant 'Clearspring Refills' is vendor-only",
            "variant '  ': blank item text",
            "vendor variant 'Swiftlane Couriers - ' must read `Vendor - item`",
            "variant 'cafe-2' is vendor-only",
        )

    def test_vendor_must_sell_the_item_and_terse_is_unattributed(self):
        def bad(files):
            files["text"]["pantry.snacks"]["vendor"] = [{"seller": "cafe-1", "text": "Bean Harbor Cafe - Pastries"}]
            files["text"]["pantry.water"]["terse"].append("Clearspring Refills - Water")
            files["text"]["pantry.coffee"]["descriptive"].append("Bean Harbor Cafe - Espresso")

        self.assertRejected(
            bad,
            "names seller `cafe-1`, which has no price point here",
            "terse variant 'Clearspring Refills - Water' carries a vendor",
            "descriptive variant 'Bean Harbor Cafe - Espresso' carries a vendor",
        )

    def test_vendor_prefixes_are_a_minority_and_one_name_per_seller(self):
        def bad(files):
            files["text"]["pantry.coffee"]["vendor"] += [
                {"seller": "cafe-2", "text": "Other Cafe - Coffee for crew"},
                {"seller": "cafe-2", "text": "Third Cafe - Coffee for editors"},
            ]

        self.assertRejected(
            bad,
            "item `pantry.coffee`: vendor-prefixed variants must be a minority",
            "seller `cafe-2` appears under several vendor names: Other Cafe, Third Cafe",
        )

    def test_each_string_belongs_to_one_item(self):
        def dup(files):
            files["text"]["pantry.snacks"]["descriptive"].append("Iced coffee for editing team")
            files["text"]["pantry.snacks"]["terse"].append("Parking")

        self.assertRejected(
            dup,
            "'Iced coffee for editing team' is used by pantry.coffee, pantry.snacks",
            "'Parking' is used by errands.parking, pantry.snacks",
        )

    def test_csv_text_rules(self):  # gate 6, FR-H2
        def long(files):
            files["text"]["pantry.coffee"]["descriptive"].append("Coffee " + "x" * 100)

        self.assertRejected(long, "item text longer than 100 characters")


if __name__ == "__main__":
    unittest.main()
