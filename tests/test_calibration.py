"""Hitting the target without plug rows (ticket 05: T11-T15, T20, T20a).

Driven through `main(argv)`. The fixture bundle spends about ₱25k a quarter on
its own; with every item on every day and the largest allowed quantities it
can reach about ₱200k, so targets here stay in that range.
"""

import statistics
import unittest
from collections import Counter, defaultdict
from decimal import Decimal

from tests.helpers import Workspace, add_item, load_fixture_files


def text_index(files):
    index = {}
    for item_id, variants in files["text"].items():
        vendor = [v["text"] for v in variants.get("vendor", [])]
        for t in variants["descriptive"] + vendor + variants["terse"]:
            index[t] = item_id
    return index


def centavos(row):
    return int(Decimal(row["unit_price"]) * 100)


def amount(row):
    return int(Decimal(row["qty"]) * Decimal(row["unit_price"]) * 100)


def rate_card_prices(card):
    """Every unit price a rate card lists (points, volume tiers, steps)."""
    versions = [card["points"]] + [s["points"] for s in card.get("steps", [])]
    return {p["unit_price"] for points in versions for p in points} | {
        t["unit_price"] for points in versions for p in points for t in p.get("tiers", [])
    }


def band(target, band_pct=2):
    return target * 100, target * (100 + band_pct)


def mean_qty(rows):
    return statistics.mean(float(r["qty"]) for r in rows)


class CalibrationCase(unittest.TestCase):
    mutate = None

    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_bundle(mutate=self.mutate)
        self.files = load_fixture_files()
        if self.mutate:
            self.mutate(self.files)
        self.index = text_index(self.files)

    def generate(self, config, seed=1):
        self.ws.write_config(config, fixture_defaults=False)
        return self.ws.run("generate", "--seed", str(seed))

    def assert_in_band(self, r, target, band_pct=2):
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        lo, hi = band(target, band_pct)
        total = sum(amount(row) for row in r.rows)
        self.assertEqual(total, r.run_json["total_centavos"])
        self.assertTrue(lo <= total <= hi, f"total {total} outside [{lo}, {hi}]")
        return total

    def assert_no_plug_rows(self, r):  # T14
        """Every row is qty x a rate-card price, qty from the allowed set, gap rules kept."""
        per_item_day = defaultdict(set)
        for row in r.rows:
            item_id = self.index[row["item/service"]]
            card = self.files["rate_cards"][item_id]
            self.assertIn(centavos(row), rate_card_prices(card), row)
            allowed = {Decimal(str(q["qty"])) for q in card["quantities"] if q["weight"] > 0}
            self.assertIn(Decimal(row["qty"]), allowed, row)
            if self.files["catalog"]["items"][item_id]["class"] != "big_ticket":
                self.assertNotEqual(amount(row) % 100_000, 0, f"round-thousand plug row {row}")
            # A same-day, same-amount duplicate group (batch entry, FR-H3) counts once.
            per_item_day[(item_id, row["date_of_transaction"])] |= {amount(row)}
        self.assertEqual(max(map(len, per_item_day.values())), 1, "at most one row per item per day")
        hard = {c["name"]: c["status"] for c in r.run_json["scorecard"]["checks"] if c["hard"]}
        self.assertEqual(set(hard.values()), {"pass"}, hard)

    def items_of(self, r):
        return Counter(self.index[row["item/service"]] for row in r.rows)


class TotalInBandTest(CalibrationCase):
    def test_total_lands_in_band_across_20_seeds_and_varies(self):  # T11
        totals = set()
        for seed in range(20):
            r = self.generate("target = 40000\n", seed)
            totals.add(self.assert_in_band(r, 40_000))
            self.assert_no_plug_rows(r)
        self.assertGreater(len(totals), 10, "totals vary between seeds")

    def test_shrinks_a_plan_that_starts_above_the_band(self):
        natural = self.ws.run("generate", "--seed", "3")
        self.assertGreater(natural.run_json["total_centavos"], 15_000_00 * 1.02)
        for seed in range(5):
            r = self.generate("target = 15000\n", seed)
            self.assert_in_band(r, 15_000)
            self.assert_no_plug_rows(r)
        self.assertLess(len(self.generate("target = 15000\n", 3).rows), len(natural.rows))

    def test_a_narrow_band_is_closed_with_whole_occurrences(self):  # T14, FR-G3
        for seed in range(5):
            r = self.generate("target = 40000\nband_pct = 0.1\n", seed)
            self.assert_in_band(r, 40_000, Decimal("0.1"))
            self.assert_no_plug_rows(r)

    def test_calibrated_run_is_reproducible(self):  # T1 with calibration
        a = self.generate("target = 40000\nband_pct = 0.5\n", 11)
        a_csv, a_run = a.csv_bytes, a.run_bytes
        b = self.generate("target = 40000\nband_pct = 0.5\n", 11)
        self.assertEqual((b.csv_bytes, b.run_bytes), (a_csv, a_run))

    def test_scaling_order_occurrences_before_quantities(self):  # FR-G2
        natural = [self.ws.run("generate", "--seed", str(s)) for s in range(3)]
        grown = [self.generate("target = 60000\n", s) for s in range(3)]
        saturated = [self.generate("target = 160000\n", s) for s in range(3)]
        days = 92  # 2026Q3
        for r in grown:
            self.assert_in_band(r, 60_000)
            self.assert_no_plug_rows(r)
            self.assertLess(len(r.rows), 6 * days)
        for r in saturated:
            self.assert_in_band(r, 160_000)
            self.assert_no_plug_rows(r)
            # Every item is at its gap-rule limit (one row a day) before quantities grow.
            self.assertEqual(self.items_of(r), Counter({i: days for i in self.files["catalog"]["items"]}))
        base_q = mean_qty([row for r in natural for row in r.rows])
        grown_q = mean_qty([row for r in grown for row in r.rows])
        saturated_q = mean_qty([row for r in saturated for row in r.rows])
        self.assertLess(abs(grown_q - base_q), 0.15, "occurrences grow first; quantities keep the bundle's weights")
        self.assertGreater(saturated_q, grown_q + 0.2, "then larger quantities from the allowed sets")
        self.assertGreater(len(grown[0].rows), len(natural[0].rows))

    def test_monthly_row_counts_vary_by_seed(self):  # T15, FR-G5
        shapes = set()
        for seed in range(4):
            r = self.generate("target = 40000\n", seed)
            months = Counter(row["date_of_transaction"][:7] for row in r.rows)
            self.assertEqual(set(months), {"2026-07", "2026-08", "2026-09"})
            shapes.add(tuple(sorted(months.items())))
        self.assertGreater(len(shapes), 1)


class UnsatisfiableTest(CalibrationCase):
    def assert_exit(self, config, code, message):
        r = self.generate(config)
        self.assertEqual(r.code, code, r.stdout + r.stderr)
        self.assertIn(message, r.stderr)
        self.assertIsNone(r.csv_path)
        self.assertEqual(self.ws.all_csvs(), [], "no CSV on exit 5 or 6")

    def test_gap_rules_that_cannot_hold_at_the_requested_scale_exit_5(self):  # T12
        self.assert_exit('tier = "high"\ntarget = 4000000\n', 5, "gap rules cannot hold")
        self.assert_exit("target = 30000\ntarget_rows = 5000\n", 5, "gap rules cannot hold for target_rows = 5000")

    def test_target_rows_incompatible_with_the_total_exits_6(self):  # T13
        # 20 rows of at most ₱720 (4 coffees) can never reach ₱30,000.
        self.assert_exit("target = 30000\ntarget_rows = 20\n", 6, "target_rows = 20")


class TightBandTest(CalibrationCase):
    @staticmethod
    def mutate(files):
        # Every amount a whole multiple of ₱10, so ₱30,005 exactly can never be hit.
        for card in files["rate_cards"].values():
            card["points"] = [{"unit_price": 5_000, "seller": "s-1"}, {"unit_price": 6_000, "seller": "s-2"}]
            card["quantities"] = [{"qty": 1, "weight": 2}, {"qty": 2, "weight": 1}]
            card.pop("steps", None)

    def test_band_too_tight_exits_6(self):  # T13
        r = self.generate("target = 30005\nband_pct = 0\n")
        self.assertEqual(r.code, 6, r.stderr)
        self.assertIn("cannot be met", r.stderr)
        self.assertEqual(self.ws.all_csvs(), [])
        self.assert_in_band(self.generate("target = 30010\nband_pct = 0\n"), 30_010, 0)


def add_seats(files):
    add_item(
        files,
        "subs.cloud_seats",
        storyline="subscriptions",
        points=[(119_000, "cloud-1")],
        quantities=[(1, 4), (2, 3), (3, 2), (4, 1), (5, 1)],
        descriptive=["Cloud storage plan, per seat"],
        price_class="subscription",
        category="Subscriptions",
        params={"per_week": 1.0},
    )


class SubscriptionCase(CalibrationCase):
    mutate = staticmethod(add_seats)

    def test_plan_that_cannot_shrink_into_the_band_exits_6(self):  # T13
        # Subscriptions keep their planned occurrences, which alone overshoot ₱5,000.
        r = self.generate("target = 5000\n")
        self.assertEqual(r.code, 6, r.stderr)
        self.assertIn("smallest plan", r.stderr)
        self.assertEqual(self.ws.all_csvs(), [])

    def test_class_multiplier_raises_its_share_and_moves_no_price(self):  # T20a
        def spend_share(r):
            subs = sum(amount(row) for row in r.rows if self.index[row["item/service"]] == "subs.cloud_seats")
            return subs / r.run_json["total_centavos"]

        base, raised = [], []
        for seed in range(3):
            a = self.generate("target = 120000\n", seed)
            b = self.generate("target = 120000\n[multipliers.class]\nsubscription = 3.0\n", seed)
            for r in (a, b):
                self.assert_in_band(r, 120_000)
                self.assert_no_plug_rows(r)
            self.assertEqual(
                {row["unit_price"] for row in b.rows if self.index[row["item/service"]] == "subs.cloud_seats"},
                {"1190.00"},
            )
            self.assertNotEqual(a.run_json["run_id"], b.run_json["run_id"])
            base.append(spend_share(a))
            raised.append(spend_share(b))
        self.assertGreater(statistics.mean(raised), statistics.mean(base) * 1.5)

    def test_seats_grow_before_retail_quantities(self):  # FR-G2 step 2 before 3
        def qty(r, seats):
            is_seat = lambda row: self.index[row["item/service"]] == "subs.cloud_seats"  # noqa: E731
            return [float(row["qty"]) for row in r.rows if is_seat(row) == seats]

        runs = {t: self.generate(f"target = {t}\n", 0) for t in (150_000, 190_000, 240_000)}
        for t, r in runs.items():
            self.assert_in_band(r, t)
            self.assert_no_plug_rows(r)
        seats = {t: statistics.mean(qty(r, True)) for t, r in runs.items()}
        retail = {t: statistics.mean(qty(r, False)) for t, r in runs.items()}
        self.assertGreater(seats[190_000], seats[150_000] + 1, "seats grow once occurrences are at their limit")
        self.assertLess(abs(retail[190_000] - retail[150_000]), 0.1, "while retail quantities wait")
        self.assertEqual(set(qty(runs[240_000], True)), {5.0}, "seats at the largest allowed count")
        self.assertGreater(retail[240_000], retail[190_000] + 0.2, "before retail quantities grow")


def add_wide_seats(files):
    """A subscription whose seat count (drawn once per run) ranges 1-10: the plan as drawn is
    often far above a band that fewer seats would meet."""
    add_item(
        files,
        "subs.cloud_seats",
        storyline="subscriptions",
        points=[(119_000, "cloud-1")],
        quantities=[(q, 1) for q in range(1, 11)],
        descriptive=["Cloud storage plan, per seat"],
        price_class="subscription",
        category="Subscriptions",
        params={"per_week": 1.0},
    )


def only_wide_seats(files):
    """The seats item alone: no item has an occurrence lever."""
    add_wide_seats(files)
    for item_id in list(files["catalog"]["items"]):
        if item_id != "subs.cloud_seats":
            for stem in ("catalog", "rate_cards", "text"):
                (files[stem]["items"] if stem == "catalog" else files[stem]).pop(item_id)


class ShrinkQuantitiesTest(CalibrationCase):
    """A plan above the band lowers the quantity stages (scope, quantities, seats) when
    fewer occurrences alone cannot get under it (review 1)."""

    mutate = staticmethod(add_wide_seats)

    def shrinks(self, config, target, band_pct, seeds=range(8)):
        above = 0
        for seed in seeds:
            self.ws.write_config("")
            natural = self.ws.run("generate", "--seed", str(seed))
            above += natural.run_json["total_centavos"] > target * (100 + band_pct)
            r = self.generate(config, seed)
            self.assert_in_band(r, target, band_pct)
            self.assert_no_plug_rows(r)
        self.assertGreater(above, 2, "several seeds start above the band")

    def test_seats_shrink_when_occurrences_cannot(self):
        self.shrinks("target = 100000\nband_pct = 10\n", 100_000, 10)

    def test_bundle_without_occurrence_levers_shrinks_seats(self):
        self.ws = Workspace(self)
        self.ws.install_bundle(mutate=only_wide_seats)
        self.shrinks("target = 30000\nband_pct = 60\n", 30_000, 60)


class TierAndStorylineTest(CalibrationCase):
    def test_tier_changes_rows_and_quantities_never_prices(self):  # T20, FR-E11
        runs = {}
        for tier in ("independent", "mid", "high"):
            r = self.generate(f'target = 60000\ntier = "{tier}"\n', 7)
            self.assert_in_band(r, 60_000)
            self.assert_no_plug_rows(r)
            runs[tier] = r
        rows = {t: len(r.rows) for t, r in runs.items()}
        qty = {t: mean_qty(r.rows) for t, r in runs.items()}
        self.assertGreater(rows["independent"], rows["mid"])
        self.assertGreater(rows["mid"], rows["high"])
        self.assertLess(qty["independent"], qty["mid"])
        self.assertLess(qty["mid"], qty["high"])

    def test_tier_factors_are_bundle_configurable(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: f["rules"]["tier_factors"].update(high=1.0))
        ws.write_config('target = 60000\ntier = "high"\n', fixture_defaults=False)
        high_as_mid = ws.run("generate", "--seed", "7")
        mid = self.generate('target = 60000\ntier = "mid"\n', 7)
        self.assertEqual(high_as_mid.code, 0, high_as_mid.stderr)
        self.assertEqual(high_as_mid.rows, mid.rows)

    def test_storyline_multiplier_raises_its_share_and_moves_no_price(self):  # T20a
        def errands_share(r):
            n = sum(1 for row in r.rows if self.index[row["item/service"]].startswith("errands."))
            return n / len(r.rows)

        base, raised = [], []
        for seed in range(3):
            a = self.generate("target = 40000\n", seed)
            b = self.generate("target = 40000\n[multipliers.storyline]\nerrands = 3.0\n", seed)
            for r in (a, b):
                self.assert_in_band(r, 40_000)
                self.assert_no_plug_rows(r)
            base.append(errands_share(a))
            raised.append(errands_share(b))
        self.assertGreater(statistics.mean(raised), statistics.mean(base) + 0.1)


if __name__ == "__main__":
    unittest.main()
