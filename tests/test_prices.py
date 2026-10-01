"""Stable prices: yearly steps, volume tiers and tidy cents (ticket 09: T16, T17, T19; FR-F1 to FR-F5).

Driven through `main(argv)`; external CSVs through `txns.scorecard.score_csv`.
"""

import csv
import io
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from tests.helpers import Workspace, add_item
from txns.bundle import store
from txns.scorecard import score_csv

NEGATIVE_SAMPLE = Path(__file__).resolve().parent.parent / "inputs" / "negative-sample.csv"
HEADER = "date_of_transaction,qty,unit_price,item/service\n"
STEP_DAY = date(2026, 8, 17)  # inside the default period, 2026-07-01 .. 2026-09-30


def check(run_json, name):
    for c in run_json["scorecard"]["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"no scorecard metric {name}")


def centavos(row):
    return int(Decimal(row["unit_price"]) * 100)


def rows_of(r, text):
    return [row for row in r.rows if row["item/service"] == text]


def add_toner(files, *, steps=None, points=((150_000, "office-1"), (172_000, "office-2")), price_class="retail"):
    """A retail item bought often, with an optional dated price step."""
    add_item(
        files,
        "office.toner",
        storyline="office_pantry",
        points=list(points),
        quantities=[(1, 3), (2, 1)],
        descriptive=["Toner cartridge for the office printer"],
        price_class=price_class,
        params={"per_week": 5.0},
    )
    if steps is not None:
        files["rate_cards"]["office.toner"]["steps"] = steps


TONER_STEP = [{"date": STEP_DAY.isoformat(), "points": [{"unit_price": 165_000}, {"unit_price": 185_000}]}]


def add_tape(files, *, tiers=None, goods="stock", steps=None):
    """A stock item with volume tiers: 1-4 at ₱1,950, 5-9 at ₱1,850, 10+ at ₱1,775."""
    add_item(
        files,
        "stock.tape",
        storyline="office_pantry",
        points=[(195_000, "media-1")],
        quantities=[(1, 3), (2, 2), (5, 2), (10, 1)],
        descriptive=["LTO tape stock for archive"],
        params={"per_week": 5.0},
    )
    files["catalog"]["items"]["stock.tape"]["goods"] = goods
    files["rate_cards"]["stock.tape"]["points"][0]["tiers"] = (
        tiers if tiers is not None else [{"min_qty": 5, "unit_price": 185_000}, {"min_qty": 10, "unit_price": 177_500}]
    )
    if steps is not None:
        files["rate_cards"]["stock.tape"]["steps"] = steps


def add_fuel(files, *, decimal=True, qtys=(("10.5", 2), ("12.25", 1), (20, 1)), points=((6_500, "fuel-1"), (6_600, "fuel-2"))):
    """A decimal item: fuel by the litre."""
    add_item(
        files,
        "errands.fuel",
        storyline="errands",
        points=list(points),
        quantities=[(1, 1)],
        descriptive=["Diesel for the location van"],
        params={"per_week": 4.0},
    )
    files["catalog"]["items"]["errands.fuel"]["decimal"] = decimal
    files["rate_cards"]["errands.fuel"]["quantities"] = [{"qty": q, "weight": w} for q, w in qtys]


class BundleRuleTest(unittest.TestCase):
    """Rate-card rules a bundle must keep; breaking one exits 4 with no CSV."""

    def assert_invalid(self, mutate, needle):
        ws = Workspace(self)
        ws.install_bundle(mutate=mutate)
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn(needle, r.stderr)
        self.assertEqual(ws.all_csvs(), [])

    def test_price_points_per_class(self):  # FR-F1
        def points(n, price_class):
            return lambda f: add_toner(f, points=[(150_000 + 1_000 * i, f"s-{i}") for i in range(n)], price_class=price_class)

        self.assert_invalid(points(2, "subscription"), "subscription item has at most 1 price point")
        self.assert_invalid(points(2, "big_ticket"), "big_ticket item has at most 1 price point")
        self.assert_invalid(points(5, "retail"), "retail item has at most 4 price point")
        for n, price_class in ((1, "subscription"), (1, "big_ticket"), (4, "retail")):
            ws = Workspace(self)
            ws.install_bundle(mutate=points(n, price_class))
            self.assertEqual(ws.run("generate", "--seed", "1").code, 0, (n, price_class))

    def test_unit_prices_have_tidy_cents(self):  # FR-F5
        self.assert_invalid(lambda f: add_toner(f, points=[(150_025, "s-1"), (172_000, "s-2")]), "1500.25 has untidy cents")
        self.assert_invalid(
            lambda f: add_toner(f, steps=[{"date": "2026-08-17", "points": [{"unit_price": 165_010}, {"unit_price": 185_000}]}]),
            "1650.10 has untidy cents",
        )
        self.assert_invalid(
            lambda f: add_tape(f, tiers=[{"min_qty": 5, "unit_price": 185_001}]),
            "1850.01 has untidy cents",
        )

    def test_price_step_rules(self):  # FR-F2
        def step(*steps):
            return lambda f: add_toner(f, steps=list(steps))

        def pts(a, b):
            return [{"unit_price": a}, {"unit_price": b}]

        cases = [
            (step({"date": "2026-08-17", "points": pts(180_000, 185_000)}), "outside +3% to +15%"),  # +20%
            (step({"date": "2026-08-17", "points": pts(152_000, 185_000)}), "outside +3% to +15%"),  # +1.3%
            (step({"date": "2026-08-17", "points": pts(140_000, 160_000)}), "outside +3% to +15%"),  # a cut
            (
                step(
                    {"date": "2026-01-01", "points": pts(160_000, 180_000)},
                    {"date": "2026-08-17", "points": pts(170_000, 190_000)},
                ),
                "at most one price step per item per year",
            ),
            (
                step(
                    {"date": "2027-01-01", "points": pts(160_000, 180_000)},
                    {"date": "2026-01-01", "points": pts(170_000, 190_000)},
                ),
                "strictly ascending",
            ),
            (step({"date": "2026-08-17", "points": [{"unit_price": 165_000}]}), "lists all 2 price point(s)"),
            (
                step({"date": "2026-08-17", "points": [{"unit_price": 165_000, "seller": "other"}, {"unit_price": 185_000}]}),
                "keeps each point's seller",
            ),
            (step({"date": "17 Aug 2026", "points": pts(165_000, 185_000)}), "YYYY-MM-DD"),
        ]
        for mutate, needle in cases:
            with self.subTest(needle):
                self.assert_invalid(mutate, needle)

    def test_steps_compound_from_the_preceding_step(self):  # FR-F2: each year's step is checked against the last
        def pts(a, b):
            return [{"unit_price": a}, {"unit_price": b}]

        def two(first, second):
            return lambda f: add_toner(
                f,
                points=((1_000, "office-1"), (1_500, "office-2")),
                steps=[{"date": "2026-01-01", "points": pts(*first)}, {"date": "2027-01-01", "points": pts(*second)}],
            )

        ws = Workspace(self)
        ws.install_bundle(mutate=two((1_100, 1_650), (1_200, 1_800)))  # ₱10 -> ₱11 -> ₱12 is +20% in all
        self.assertEqual(ws.run("generate", "--seed", "1").code, 0)

        outside = "outside +3% to +15%"
        for name, first, second in [
            ("decrease", (1_150, 1_700), (1_100, 1_650)),  # still +10% on the base
            ("flat step", (1_100, 1_650), (1_100, 1_650)),  # still +10% on the base
            ("undersized", (1_050, 1_575), (1_075, 1_600)),  # +2.4% on the last step, +7.5% on the base
            ("oversized", (1_100, 1_650), (1_300, 1_950)),  # +18% on the last step
        ]:
            with self.subTest(name):
                self.assert_invalid(two(first, second), outside)

    def test_tiered_steps_compound_from_the_preceding_step(self):  # FR-F2, FR-F4
        def tape(second):
            tiers = lambda a, b: [{"min_qty": 5, "unit_price": a}, {"min_qty": 10, "unit_price": b}]
            return lambda f: add_tape(
                f,
                steps=[
                    {"date": "2026-01-01", "points": [{"unit_price": 205_000, "tiers": tiers(195_000, 187_500)}]},
                    {"date": "2027-01-01", "points": [{"unit_price": second[0], "tiers": tiers(*second[1:])}]},
                ],
            )

        ws = Workspace(self)
        ws.install_bundle(mutate=tape((215_000, 205_000, 197_500)))
        self.assertEqual(ws.run("generate", "--seed", "1").code, 0)

        outside = "outside +3% to +15%"
        for name, second in [
            ("tier decrease", (215_000, 190_000, 185_000)),
            ("tier flat", (215_000, 195_000, 187_500)),
            ("tier undersized", (215_000, 196_000, 190_000)),  # +0.5% on the last step's tier
            ("tier oversized", (230_000, 226_000, 200_000)),  # the 5-pack tier +15.9%
        ]:
            with self.subTest(name):
                self.assert_invalid(tape(second), outside)

    def test_step_bounds_do_not_apply_to_big_ticket(self):
        ws = Workspace(self)
        ws.install_bundle(
            mutate=lambda f: add_toner(
                f,
                points=[(9_000_000, "stage-1")],
                price_class="big_ticket",
                steps=[{"date": "2026-08-17", "points": [{"unit_price": 12_000_000}]}],
            )
        )
        self.assertEqual(ws.run("generate", "--seed", "1").code, 0)

    def test_volume_tier_rules(self):  # FR-F4
        self.assert_invalid(lambda f: add_tape(f, goods=None), "only on items whose catalog `goods` is stock or hardware")
        self.assert_invalid(lambda f: add_tape(f, goods="services"), "`goods` must be one of stock, hardware")
        self.assert_invalid(
            lambda f: add_tape(
                f,
                tiers=[
                    {"min_qty": 3, "unit_price": 190_000},
                    {"min_qty": 5, "unit_price": 185_000},
                    {"min_qty": 10, "unit_price": 177_500},
                ],
            ),
            "at most 2",
        )
        self.assert_invalid(lambda f: add_tape(f, tiers=[{"min_qty": 5, "unit_price": 199_000}]), "cheaper than the one below")
        self.assert_invalid(
            lambda f: add_tape(f, tiers=[{"min_qty": 10, "unit_price": 185_000}, {"min_qty": 5, "unit_price": 177_500}]),
            "strictly ascending",
        )
        self.assert_invalid(lambda f: add_tape(f, tiers=[{"min_qty": 1, "unit_price": 185_000}]), "`min_qty` of 2 or more")
        self.assert_invalid(
            lambda f: add_tape(
                f,
                steps=[
                    {"date": "2026-08-17", "points": [{"unit_price": 205_000, "tiers": [{"min_qty": 6, "unit_price": 195_000}]}]}
                ],
            ),
            "keeps each point's tier breaks",
        )

    def test_decimal_quantity_rules(self):  # FR-F3
        self.assert_invalid(lambda f: add_fuel(f, decimal=False), "needs catalog `decimal: true`")
        self.assert_invalid(lambda f: add_fuel(f, qtys=(("10.333", 1),)), "is not a whole-centavo amount")
        self.assert_invalid(lambda f: add_fuel(f, qtys=(("0.1255", 1),)), "more than 3 decimal places")
        self.assert_invalid(lambda f: add_fuel(f, qtys=(("-2.5", 1),)), "must be positive")
        self.assert_invalid(
            lambda f: f["catalog"]["items"]["pantry.coffee"].update(decimal="yes"), "`decimal` must be true or false"
        )


class PriceStepTest(unittest.TestCase):  # T16
    def test_step_takes_effect_on_its_date_for_every_seed_and_old_price_never_returns(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: add_toner(f, steps=TONER_STEP))
        old, new = {150_000, 172_000}, {165_000, 185_000}
        for seed in ("1", "2", "3", "4"):
            r = ws.run("generate", "--seed", seed)
            self.assertEqual(r.code, 0, r.stdout)
            rows = rows_of(r, "Toner cartridge for the office printer")
            before = [row for row in rows if date.fromisoformat(row["date_of_transaction"]) < STEP_DAY]
            after = [row for row in rows if date.fromisoformat(row["date_of_transaction"]) >= STEP_DAY]
            self.assertTrue(before and after)
            self.assertEqual({centavos(row) for row in before} - old, set(), seed)
            self.assertEqual({centavos(row) for row in after} - new, set(), "old price after the step")
            self.assertLessEqual(len({centavos(row) for row in rows}), 4, "points + steps in span")
            self.assertEqual(check(r.run_json, "price_stability")["status"], "pass")

    def test_step_outside_the_period_leaves_the_card_unchanged(self):
        ws = Workspace(self)
        steps = [{"date": "2027-01-01", "points": [{"unit_price": 165_000}, {"unit_price": 185_000}]}]
        ws.install_bundle(mutate=lambda f: add_toner(f, steps=steps))
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0)
        self.assertEqual({centavos(row) for row in rows_of(r, "Toner cartridge for the office printer")}, {150_000, 172_000})

    def test_step_on_the_first_day_of_the_period_replaces_the_card(self):
        ws = Workspace(self)
        steps = [{"date": "2026-07-01", "points": [{"unit_price": 165_000}, {"unit_price": 185_000}]}]
        ws.install_bundle(mutate=lambda f: add_toner(f, steps=steps))
        r = ws.run("generate", "--seed", "1")
        self.assertEqual({centavos(row) for row in rows_of(r, "Toner cartridge for the office printer")}, {165_000, 185_000})

    def scored(self, steps, lines):
        ws = Workspace(self)
        bundle = store.load(ws.bundle_dir(ws.install_bundle(mutate=lambda f: add_toner(f, steps=steps))))
        return score_csv(HEADER + "".join(f"{line},Toner cartridge for the office printer\n" for line in lines), bundle)

    def test_old_price_after_its_step_date_fails_hard(self):
        report = self.scored(TONER_STEP, ["2026-08-10,1,1500.00", "2026-08-18,1,1650.00", "2026-08-20,1,1500.00"])
        c = report.get("price_stability")
        self.assertEqual((c.status, c.hard), ("fail", True))
        self.assertIn("office.toner: old price ₱1,500.00 on 2026-08-20 after its step", c.detail)
        self.assertEqual(c.value, {"office.toner": 2})
        self.assertTrue(report.hard_failure)

    def test_distinct_prices_count_steps_in_the_span(self):
        four = ["2026-08-10,1,1500.00", "2026-08-11,1,1720.00", "2026-08-18,1,1650.00", "2026-08-19,1,1850.00"]
        self.assertEqual(self.scored(TONER_STEP, four).get("price_stability").status, "pass")
        # A step dated after the span adds nothing: four prices then exceed the two points.
        later = [{"date": "2026-12-01", "points": TONER_STEP[0]["points"]}]
        c = self.scored(later, four).get("price_stability")
        self.assertEqual(c.status, "fail")
        self.assertIn("office.toner: 4 prices, rate card allows 2", c.detail)


class VolumeTierTest(unittest.TestCase):  # FR-F4
    def test_each_quantity_gets_its_tier_price_from_the_rate_card(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=add_tape)
        r = ws.run("generate", "--seed", "3")
        self.assertEqual(r.code, 0, r.stdout)
        seen = {(int(row["qty"]), row["unit_price"]) for row in rows_of(r, "LTO tape stock for archive")}
        self.assertEqual(seen, {(1, "1950.00"), (2, "1950.00"), (5, "1850.00"), (10, "1775.00")})
        self.assertEqual(check(r.run_json, "price_stability")["status"], "pass")
        self.assertEqual(check(r.run_json, "quantities")["status"], "pass")

    def test_tiers_step_together(self):
        steps = [
            {
                "date": STEP_DAY.isoformat(),
                "points": [
                    {
                        "unit_price": 205_000,
                        "tiers": [{"min_qty": 5, "unit_price": 195_000}, {"min_qty": 10, "unit_price": 187_500}],
                    }
                ],
            }
        ]
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: add_tape(f, steps=steps))
        r = ws.run("generate", "--seed", "3")
        self.assertEqual(r.code, 0, r.stdout)
        tiers = {1: 0, 2: 0, 5: 1, 10: 2}
        for row in rows_of(r, "LTO tape stock for archive"):
            stepped = date.fromisoformat(row["date_of_transaction"]) >= STEP_DAY
            card = (205_000, 195_000, 187_500) if stepped else (195_000, 185_000, 177_500)
            self.assertEqual(centavos(row), card[tiers[int(row["qty"])]], row)
        self.assertEqual(check(r.run_json, "price_stability")["status"], "pass")


class DecimalQuantityTest(unittest.TestCase):  # T17
    def test_decimal_item_draws_decimals_from_its_allowed_set(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=add_fuel)
        r = ws.run("generate", "--seed", "5")
        self.assertEqual(r.code, 0, r.stdout)
        records = list(csv.reader(io.StringIO(r.csv_bytes.decode("utf-8"), newline=""), strict=True))[1:]
        fuel = {rec[1] for rec in records if rec[3] == "Diesel for the location van"}
        self.assertEqual(fuel, {"10.5", "12.25", "20"})
        for rec in records:
            if rec[3] != "Diesel for the location van":
                self.assertRegex(rec[1], r"^[1-9][0-9]*$", "decimals only on decimal items")
        total = sum(Decimal(rec[1]) * Decimal(rec[2]) * 100 for rec in records)
        self.assertEqual(r.run_json["total_centavos"], int(total))
        self.assertEqual(total, int(total), "every amount is whole centavos")
        for name in ("format", "quantities", "price_stability"):
            self.assertEqual(check(r.run_json, name)["status"], "pass", name)
        again = ws.run("generate", "--seed", "5")
        self.assertEqual(again.csv_bytes, r.csv_bytes)

    def test_decimal_qty_on_a_non_decimal_item_fails_the_format_check(self):
        ws = Workspace(self)
        bundle = store.load(ws.bundle_dir(ws.install_bundle(mutate=add_fuel)))
        report = score_csv(HEADER + "2026-08-03,2.5,165.00,Coffee\n2026-08-04,10.5,65.00,Diesel for the location van\n", bundle)
        c = report.get("format")
        self.assertEqual(c.status, "fail")
        self.assertIn("row 1: qty 2.5 is a decimal on an item not marked decimal", c.detail)
        self.assertNotIn("row 2", c.detail)
        self.assertEqual(report.get("quantities").status, "warn")
        self.assertIn("pantry.coffee qty 2.5", report.get("quantities").detail)


class TidyCentsTest(unittest.TestCase):  # T19
    def test_ordinary_run_has_tidy_cents_and_a_whole_peso_share_near_60_percent(self):
        ws = Workspace(self)
        ws.install_bundle()
        for seed in ("1", "42"):
            r = ws.run("generate", "--seed", seed)
            self.assertEqual(r.code, 0)
            for row in r.rows:
                self.assertIn(row["unit_price"][-3:], (".00", ".50", ".75"), row)
            share = check(r.run_json, "whole_peso_share")
            whole = sum(1 for row in r.rows if row["unit_price"].endswith(".00")) / len(r.rows)
            self.assertEqual((share["section"], share["hard"], share["status"]), ("price_quantity", False, "pass"))
            self.assertAlmostEqual(share["value"], whole)
            self.assertEqual(share["reference"], 0.6)
            self.assertIn("no ledger reference", share["detail"])
            self.assertEqual(check(r.run_json, "tidy_cents")["status"], "pass")

    def test_whole_peso_share_uses_the_ledger_figure_when_there_is_one(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: f["reference"].update(whole_peso_share=0.25))
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0, "a soft metric never exits 1")
        share = check(r.run_json, "whole_peso_share")
        self.assertEqual((share["status"], share["reference"]), ("fail", 0.25))
        self.assertIn("vs ledger 25.0%", share["detail"])

    def test_all_whole_peso_prices_are_flagged_but_never_fail_the_run(self):
        def whole(files):
            for card in files["rate_cards"].values():
                for p in card["points"]:
                    p["unit_price"] = p["unit_price"] // 100 * 100

        ws = Workspace(self)
        ws.install_bundle(mutate=whole)
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0)
        share = check(r.run_json, "whole_peso_share")
        self.assertEqual((share["value"], share["status"]), (1.0, "fail"))

    def test_negative_sample_random_cents_warn(self):
        ws = Workspace(self)
        bundle = store.load(ws.bundle_dir(ws.install_bundle()))
        report = score_csv(NEGATIVE_SAMPLE.read_bytes(), bundle)
        cents = report.get("tidy_cents")
        self.assertEqual((cents.status, cents.hard, cents.value), ("warn", False, 11))
        self.assertIn("117880.66", cents.detail)
        share = report.get("whole_peso_share")
        self.assertEqual((share.hard, round(share.value, 4)), (False, round(1 / 12, 4)))
        self.assertEqual(share.status, "fail")


if __name__ == "__main__":
    unittest.main()
