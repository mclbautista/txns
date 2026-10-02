"""Realism scorecard and hard failures (ticket 03: T30-T32, T18).

Driven through `main(argv)`, plus the scorecard-as-a-function seam
(`txns.scorecard.score_csv`) for the negative sample.
"""

import unittest
from decimal import Decimal
from pathlib import Path

from tests.helpers import Workspace, add_item
from txns.bundle import store
from txns.scorecard import score_csv
from txns.scorecard.registry import registered

NEGATIVE_SAMPLE = Path(__file__).resolve().parent.parent / "inputs" / "negative-sample.csv"
HARD = {"format", "plug_rows", "price_stability", "gap_rules", "duplicates"}
STATUSES = {"pass", "warn", "fail"}

# The negative sample's items, cataloged with one rate-card price and one quantity each.
NEGATIVE_ITEMS = {
    "neg.offline_edit": ("Offline Edit (day)", 1_200_000),
    "neg.restoration_scan": ("Restoration Scan (ea)", 2_500_000),
    "neg.freelance_editor": ("Freelance Editor (day)", 750_000),
    "neg.stage_hire": ("Supplier Stage Hire (day)", 8_400_000),
    "neg.film_prep": ("Film Element Preparation (day)", 5_400_000),
    "neg.optical_media": ("Optical Media Stock (ea)", 500_000),
    "neg.archive_verification": ("Archive Verification (hr)", 615_000),
    "neg.lto_tape": ("LTO Tape Stock (ea)", 995_000),
    "neg.conform": ("Conform & Online (hr)", 815_000),
    "neg.color_assist": ("Color Assist (hr)", 1_205_000),
}


def catalog_negative_items(files):
    for item_id, (text, price) in NEGATIVE_ITEMS.items():
        add_item(
            files,
            item_id,
            storyline="post_services",
            points=[(price, "vendor-1")],
            quantities=[(1, 1)],
            descriptive=[text],
        )


def check(run_json, name):
    for c in run_json["scorecard"]["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"no scorecard metric {name}")


def centavos(row):
    return int(Decimal(row["unit_price"]) * 100)


class ScorecardReportTest(unittest.TestCase):  # T30
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_bundle()

    def test_ordinary_run_prints_and_stores_every_metric_and_passes_hard_checks(self):
        r = self.ws.run("generate", "--seed", "42")
        self.assertEqual(r.code, 0, r.stdout)
        sc = r.run_json["scorecard"]
        self.assertFalse(sc["hard_failure"])
        self.assertEqual(sc["tolerance_pct"], 25)
        names = [c["name"] for c in sc["checks"]]
        for reg in registered():
            self.assertTrue(any(n == reg.name or n.startswith(reg.name + ".") for n in names), reg.name)
        for c in sc["checks"]:
            self.assertIn(c["status"], STATUSES)
            self.assertIn(f"  {c['status'].upper():4}  {c['section']}/{c['name']}", r.stdout)
            self.assertEqual(c["hard"], c["name"] in HARD, c["name"])
            if c["hard"]:
                self.assertEqual(c["status"], "pass", c)
        self.assertEqual(sum(sc["counts"].values()), len(sc["checks"]))
        self.assertIn("result: ok", r.stdout)
        # The metrics the ticket names are all there.
        for name in (
            "format",
            "plug_rows",
            "price_stability",
            "gap_rules",
            "quantities",
            "row_amount_percentiles.p50",
            "round_amount_share",
            "outliers.amount",
            "outliers.unit_price",
            "distinct_per_item.prices",
            "distinct_per_item.quantities",
            "largest_row_share",
            "benford",
        ):
            self.assertIn(name, names)

    def test_ledger_metrics_grade_by_tolerance_pct(self):
        measured = check(self.ws.run("generate", "--seed", "42").run_json, "row_amount_percentiles.p50")["value"]

        def with_p50(ref_value, tolerance=None):
            ws = Workspace(self)
            ws.install_bundle(mutate=lambda f: f["reference"]["row_amount_percentiles"].update(p50=ref_value))
            if tolerance is not None:
                ws.write_config(f"tolerance_pct = {tolerance}\n")
            r = ws.run("generate", "--seed", "42")
            self.assertEqual(r.code, 0, "ledger-relative metrics never exit 1")
            return check(r.run_json, "row_amount_percentiles.p50")

        # 20% off: pass at 25%. 40% off: warn at 25% (within 2x), pass at 50%. 80% off: fail.
        self.assertEqual(with_p50(measured * 10 // 12)["status"], "pass")
        c = with_p50(measured * 10 // 14)
        self.assertEqual((c["status"], c["reference"], c["value"]), ("warn", measured * 10 // 14, measured))
        self.assertEqual(with_p50(measured * 10 // 14, tolerance=50)["status"], "pass")
        failed = with_p50(measured * 10 // 18)
        self.assertEqual(failed["status"], "fail")
        self.assertFalse(failed["hard"])

    def test_metric_without_ledger_reference_passes_and_says_so(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: f.update(reference={}))
        r = ws.run("generate", "--seed", "42")
        self.assertEqual(r.code, 0)
        c = check(r.run_json, "round_amount_share")
        self.assertEqual((c["status"], c["reference"]), ("pass", None))
        self.assertIn("no ledger reference", c["detail"])

    def test_scorecard_of_the_written_csv_matches_run_json(self):
        r = self.ws.run("generate", "--seed", "7")
        bundle = store.load(self.ws.bundle_dir(r.run_json["bundle"]["id"]))
        report = score_csv(r.csv_bytes, bundle, tolerance_pct=25)
        self.assertEqual(report.as_dict(), r.run_json["scorecard"])


class HardFailureTest(unittest.TestCase):  # T32
    def assert_hard_failure(self, r, name):
        self.assertEqual(r.code, 1, r.stdout + r.stderr)
        self.assertTrue(r.csv_path.exists(), "CSV is still written")
        self.assertGreater(len(r.rows), 0)
        self.assertTrue(r.run_json["scorecard"]["hard_failure"])
        c = check(r.run_json, name)
        self.assertEqual((c["status"], c["hard"]), ("fail", True))
        self.assertRegex(r.stdout, rf"FAIL  \w+/{name} \(hard\)")
        self.assertIn("result: HARD FAILURE", r.stdout)

    def test_unavoidable_round_thousand_amount_fails_plug_rows(self):
        ws = Workspace(self)
        ws.install_bundle(
            mutate=lambda f: add_item(
                f,
                "errands.gift_voucher",
                storyline="errands",
                points=[(100_000, "shop-1")],
                quantities=[(1, 1), (2, 1)],
                descriptive=["Gift voucher for crew raffle"],
                params={"per_week": 2.0},
            )
        )
        self.assert_hard_failure(ws.run("generate", "--seed", "42"), "plug_rows")

    def test_minimum_gap_violation_fails_gap_rules(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: f["catalog"]["items"]["pantry.coffee"]["params"].update(min_gap_days=4))
        r = ws.run("generate", "--seed", "42")
        self.assert_hard_failure(r, "gap_rules")
        self.assertIn("pantry.coffee", check(r.run_json, "gap_rules")["detail"])

    def test_benford_deviation_never_fails(self):
        def all_nines(files):
            for card in files["rate_cards"].values():
                card["points"] = [{"unit_price": 9_500, "seller": "s-1"}, {"unit_price": 9_750, "seller": "s-2"}]
                card["quantities"] = [{"qty": 1, "weight": 1}]

        ws = Workspace(self)
        ws.install_bundle(mutate=all_nines)
        r = ws.run("generate", "--seed", "42")
        self.assertEqual(r.code, 0, r.stdout)
        self.assertTrue(all(row["unit_price"].startswith("9") and row["qty"] == "1" for row in r.rows))
        c = check(r.run_json, "benford")
        self.assertEqual(c["status"], "warn")
        self.assertGreater(c["value"]["mad"], 0.015)
        self.assertIn("report only", c["detail"])


class RoundFigureTest(unittest.TestCase):  # T18
    def test_engine_redraws_quantities_that_would_give_round_thousands(self):
        def add_cake(files):
            add_item(
                files,
                "pantry.cake",
                storyline="office_pantry",
                points=[(50_000, "bakery-1"), (25_000, "bakery-2")],
                quantities=[(1, 1), (2, 10), (4, 10)],
                descriptive=["Birthday cake for the edit team"],
                params={"per_week": 5.0},
            )

        ws = Workspace(self)
        ws.install_bundle(mutate=add_cake)
        seen = set()
        for seed in ("1", "2", "3"):
            r = ws.run("generate", "--seed", seed)
            self.assertEqual(r.code, 0, r.stdout)
            self.assertEqual(check(r.run_json, "plug_rows")["status"], "pass")
            for row in r.rows:
                amount = int(row["qty"]) * centavos(row)
                self.assertNotEqual(amount % 100_000, 0, row)
                if row["item/service"] == "Birthday cake for the edit team":
                    seen.add((row["qty"], row["unit_price"]))
        # ₱500 only as qty 1; ₱250 as 1 or 2, never 4.
        self.assertEqual(seen, {("1", "500.00"), ("1", "250.00"), ("2", "250.00")})

    def test_big_ticket_items_may_carry_round_figures(self):
        ws = Workspace(self)
        ws.install_bundle(
            mutate=lambda f: add_item(
                f,
                "events.stage",
                storyline="events",
                points=[(9_000_000, "stage-1")],
                quantities=[(1, 1)],
                descriptive=["Stage and truss hire, year-end party"],
                price_class="big_ticket",
                params={"per_week": 0.5},
            )
        )
        r = ws.run("generate", "--seed", "42")
        self.assertEqual(r.code, 0, r.stdout)
        self.assertIn("90000.00", {row["unit_price"] for row in r.rows})
        self.assertEqual(check(r.run_json, "plug_rows")["status"], "pass")
        self.assertEqual(check(r.run_json, "largest_row_share")["status"], "warn")


class NegativeSampleTest(unittest.TestCase):  # T31
    def setUp(self):
        self.ws = Workspace(self)
        name = self.ws.install_bundle(mutate=catalog_negative_items)
        self.bundle = store.load(self.ws.bundle_dir(name))

    def test_negative_sample_fails_hard_on_price_stability_and_plug_rows(self):
        report = score_csv(NEGATIVE_SAMPLE.read_bytes(), self.bundle)
        self.assertTrue(report.hard_failure)
        price = report.get("price_stability")
        self.assertEqual((price.status, price.hard), ("fail", True))
        self.assertEqual(price.value, {"neg.freelance_editor": 2, "neg.restoration_scan": 2})
        plug = report.get("plug_rows")
        self.assertEqual((plug.status, plug.hard), ("fail", True))
        self.assertIn("neg.optical_media", plug.detail)
        self.assertEqual(plug.value, 1)
        self.assertEqual(report.get("format").status, "pass")
        self.assertEqual(report.get("gap_rules").status, "pass")
        self.assertEqual(report.get("quantities").status, "warn")
        self.assertEqual(report.get("benford").status, "pass", "12 rows are too few to judge")

    def test_same_item_twice_on_one_day_fails_gap_rules(self):
        # Two different amounts: a same-amount pair would be a batch-entry duplicate,
        # judged by the duplicates check instead (ticket 11, tests/test_messiness.py).
        data = (
            "date_of_transaction,qty,unit_price,item/service\n"
            "2026-02-25,1,7500.00,Freelance Editor (day)\n"
            "2026-02-25,2,7500.00,Freelance Editor (day)\n"
            "2026-02-27,1,9950.00,LTO Tape Stock (ea)\n"
        )
        report = score_csv(data, self.bundle)
        self.assertTrue(report.hard_failure)
        self.assertEqual(report.get("gap_rules").status, "fail")
        self.assertIn("neg.freelance_editor: 2 rows on 2026-02-25", report.get("gap_rules").detail)
        self.assertEqual(report.get("price_stability").status, "pass")

    def test_per_item_amount_outlier_warns_without_failing(self):
        lines = [f"2026-03-{day:02d},1,9950.00,LTO Tape Stock (ea)" for day in range(1, 21, 2)]
        lines.append("2026-03-25,99,9950.00,LTO Tape Stock (ea)")
        report = score_csv("date_of_transaction,qty,unit_price,item/service\n" + "\n".join(lines) + "\n", self.bundle)
        amount = report.get("outliers.amount")
        self.assertEqual((amount.status, amount.hard), ("warn", False))
        self.assertIn("neg.lto_tape 2026-03-25", amount.detail)
        self.assertEqual(report.get("outliers.unit_price").status, "pass")
        self.assertFalse(report.hard_failure)

    def test_unknown_item_text_is_scored_as_unapproved(self):
        data = "date_of_transaction,qty,unit_price,item/service\n2026-02-25,2,1500.00,Mystery charge\n"
        report = score_csv(data, self.bundle)
        self.assertEqual(report.get("plug_rows").status, "fail")
        self.assertEqual(report.get("price_stability").status, "pass")


if __name__ == "__main__":
    unittest.main()


class AnomalyCheckTest(unittest.TestCase):
    """`minimum_amount_and_denied_terms`: no row under the floor, no denied item text."""

    HEADER = "date_of_transaction,qty,unit_price,item/service\n"
    CLEAN = "2026-02-06,1,5000.00,Optical Media Stock (ea)\n2026-03-16,1,9950.00,LTO Tape Stock (ea)\n"

    def setUp(self):
        self.ws = Workspace(self)

    def bundle(self, floor=500):
        def mutate(files):
            catalog_negative_items(files)
            if floor is not None:
                files["rules"].setdefault("scorecard", {})["min_transaction_amount"] = floor

        return store.load(self.ws.bundle_dir(self.ws.install_bundle(mutate=mutate)))

    def anomaly(self, csv_text, bundle):
        return score_csv(self.HEADER + csv_text, bundle, tolerance_pct=25).get("minimum_amount_and_denied_terms")

    def test_scorecard_flags_sub_500_and_denied_terms(self):
        bundle = self.bundle()
        self.assertEqual(self.anomaly(self.CLEAN, bundle).status, "pass")

        under = self.anomaly(self.CLEAN + "2026-03-20,1,450.00,Optical Media Stock (ea)\n", bundle)
        self.assertEqual((under.status, under.value), ("fail", 1))
        self.assertIn("row 3: ₱450.00 is under the ₱500.00 floor", under.detail)

        denied = self.anomaly(self.CLEAN + "2026-03-20,1,5000.00,Out Fee\n", bundle)
        self.assertEqual((denied.status, denied.value), ("fail", 1))
        self.assertIn("row 3: text 'Out Fee' matches `denied_item_patterns`", denied.detail)

        both = self.anomaly(self.CLEAN + "2026-03-20,1,450.00,Reimbursement Fees\n", bundle)
        self.assertEqual((both.status, both.value), ("fail", 2))
        self.assertFalse(both.hard)  # soft: generate warns, approve fails (gate 8)

    def test_without_a_floor_only_text_is_checked(self):
        bundle = self.bundle(floor=None)
        self.assertEqual(self.anomaly(self.CLEAN + "2026-03-20,1,1.00,Optical Media Stock (ea)\n", bundle).status, "pass")
        self.assertEqual(self.anomaly(self.CLEAN + "2026-03-20,1,5000.00,out fee\n", bundle).status, "fail")

    def test_check_function_on_rows(self):
        from datetime import date

        from txns.engine.rows import Row
        from txns.scorecard.checks.anomalies import check_minimum_amount_and_denied_terms

        rows = [Row(date(2026, 7, 1), 1, 50_000, "Courier"), Row(date(2026, 7, 2), 1, 49_999, "Out fee")]
        bad = check_minimum_amount_and_denied_terms(rows, floor=50_000)
        self.assertEqual(len(bad), 2, bad)
        self.assertEqual(check_minimum_amount_and_denied_terms(rows[:1], floor=50_000), [])

    def test_generate_warns_on_anomalies_but_keeps_its_exit_code(self):
        self.ws.install_bundle(mutate=lambda f: f["rules"].setdefault("scorecard", {}).update(min_transaction_amount=500))
        r = self.ws.run("generate", "--seed", "3")
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        self.assertIn("warning: anomalies in the output:", r.stderr)
        self.assertEqual(check(r.run_json, "minimum_amount_and_denied_terms")["status"], "fail")
        self.assertTrue(any(w.startswith("anomalies in the output") for w in r.run_json["warnings"]))
