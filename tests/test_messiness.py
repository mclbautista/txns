"""Human messiness (ticket 11: FR-H3, FR-H4, FR-F5, FR-I5, T19, T21, T28, T29).

Everything is read back from the written CSV against the bundle, the way a
reviewer would: batch days and date tails, duplicate groups, entry order and
derived per-unit prices. The external-CSV cases go through the
scorecard-as-a-function seam.
"""

import calendar as cal
import re
import unittest
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from tests.helpers import Workspace, add_item, load_fixture_files
from txns.bundle import store
from txns.scorecard import score_csv

FULL_YEAR = 'start = "2026-01-01"\nend = "2026-12-31"\n'
HEADER = "date_of_transaction,qty,unit_price,item/service\n"
MONTHS = {m: i for i, m in enumerate(cal.month_abbr) if m}
TAIL = re.compile(r"^(.*) \((\w{3}) (\d{1,2}), (\d{4})\)$")
SLASH_TAIL = re.compile(r"^(.*) for (\d{1,2})/(\d{1,2})$")

ITEM_CHECKS = {
    "duplicates", "gap_rules", "min_gap", "daily_ceiling", "price_stability", "tidy_cents", "quantities",
    "terse_share", "distinct_per_item", "outliers", "plug_rows", "format",
}
DELIVERY_TEXTS = ["Delivery fee, supplier drop-off", "Rider fee for drive pickup", "Delivery charge, props run"]
DELIVERY_TERSE = ["Rider fee", "Delivery charge"]
BATTERY_PACK = ["AA batteries, 12pcs", "Alkaline batteries box of 12"]
BATTERY_PLAIN = ["AA batteries for wireless mics"]


def centavos(row) -> int:
    return int(Decimal(row["unit_price"]) * 100)


def amount(row) -> int:
    return int(Decimal(row["qty"]) * Decimal(row["unit_price"]) * 100)


def check(run_json, name):
    return next(c for c in run_json["scorecard"]["checks"] if c["name"] == name)


def hard_statuses(run_json):
    return {c["name"]: c["status"] for c in run_json["scorecard"]["checks"] if c["hard"]}


def add_delivery(files, *, per_week=3.0, max_per_day=10):
    add_item(
        files,
        "errands.delivery",
        storyline="errands",
        category="Delivery and Freight",
        archetype="batch_logged",
        points=[(9500, "rider-1"), (12000, "rider-2")],
        quantities=[(1, 1)],
        descriptive=DELIVERY_TEXTS,
        terse=DELIVERY_TERSE,
        params={"per_week": per_week},
    )
    files["rules"]["archetypes"]["batch_logged"] = {"max_per_day": max_per_day, "min_gap_days": 1}


def add_batteries(files, *, per_week=6.0):
    add_item(
        files,
        "pantry.batteries",
        storyline="office_pantry",
        category="Office Supplies",
        points=[(25000, "hardware-1"), (26500, "hardware-2")],
        quantities=[(1, 3), (2, 1)],
        descriptive=BATTERY_PACK + BATTERY_PLAIN,
        params={"per_week": per_week},
    )
    files["catalog"]["items"]["pantry.batteries"].update(goods="stock", pack_pcs=12)


def messiness(files, **settings):
    files["rules"].setdefault("messiness", {}).update(settings)


def text_to_item(files):
    return {
        t: i
        for i, v in files["text"].items()
        for t in v.get("descriptive", []) + v.get("terse", []) + [x["text"] for x in v.get("vendor", [])]
    }


class BatchLoggingTest(unittest.TestCase):  # T29, T21, FR-H3
    def setUp(self):
        self.ws = Workspace(self)
        name = self.ws.install_bundle(mutate=add_delivery)
        self.ws.write_config(FULL_YEAR)
        self.r = self.ws.run("generate", "--seed", "11")
        self.bundle = store.load(self.ws.bundle_dir(name))
        self.delivery = [r for r in self.r.rows if TAIL.sub(r"\1", r["item/service"]) in DELIVERY_TEXTS + DELIVERY_TERSE]

    def test_run_passes_hard_checks(self):
        self.assertEqual(self.r.code, 0, self.r.stdout + self.r.stderr)
        self.assertEqual(set(hard_statuses(self.r.run_json).values()), {"pass"})
        self.assertGreater(len(self.delivery), 100)

    def test_rows_land_on_batch_days_several_per_day_up_to_the_cap(self):  # T21
        per_day = Counter(date.fromisoformat(r["date_of_transaction"]) for r in self.delivery)
        for d in per_day:
            self.assertTrue(d.weekday() < 5 and self.bundle.calendar.get(d) is None, f"batch day {d} is a business day")
            # The 15th or the month's last day, rolled forward (else back) to a business day.
            last = cal.monthrange(d.year, d.month)[1]
            self.assertTrue(13 <= d.day <= 20 or d.day >= last - 4, d)
        self.assertLessEqual(len(per_day), 24, "at most two batch days a month")
        self.assertGreater(max(per_day.values()), 1, "a batch day carries several rows of the item")
        self.assertLessEqual(max(per_day.values()), 10, "never above the item's cap")

    def test_about_5_percent_carry_an_original_date_tail_before_the_batch_day(self):  # T29
        tailed = [(r, TAIL.match(r["item/service"])) for r in self.delivery if TAIL.match(r["item/service"])]
        share = len(tailed) / len(self.delivery)
        self.assertTrue(0.03 <= share <= 0.07, share)
        for row, m in tailed:
            self.assertIn(m.group(1), DELIVERY_TEXTS + DELIVERY_TERSE)
            happened = date(int(m.group(4)), MONTHS[m.group(2)], int(m.group(3)))
            logged = date.fromisoformat(row["date_of_transaction"])
            self.assertLess(happened, logged)
            self.assertLessEqual((logged - happened).days, 45)

    def test_scorecard_maps_tailed_rows_to_their_item(self):
        # Re-scoring the CSV gives what generate gave on every check that keys on items or
        # engine tags (span-based checks see the CSV's own first and last dates instead).
        report = score_csv(self.r.csv_bytes, self.bundle, tolerance_pct=25).as_dict()["checks"]
        got = {c["name"]: c for c in report}
        for want in self.r.run_json["scorecard"]["checks"]:
            if want["name"].split(".")[0] in ITEM_CHECKS:
                self.assertEqual(got[want["name"]], want)
        tailed = next(r for r in self.delivery if TAIL.match(r["item/service"]))
        # A qty outside the allowed set, on a tailed row: the quantities check names the item.
        bad = f'{tailed["date_of_transaction"]},3,95.00,"{tailed["item/service"]}"\n'
        report = score_csv(HEADER + bad, self.bundle)
        self.assertEqual(report.get("quantities").status, "warn")
        self.assertIn("errands.delivery qty 3", report.get("quantities").detail)

    def test_tail_format_comes_from_the_bundle_vocabulary(self):
        def mutate(files):
            add_delivery(files)
            files["vocabulary"] = {"date_tails": ["for {m}/{d}"]}
            messiness(files, date_tail_share=0.5)

        ws = Workspace(self)
        ws.install_bundle(mutate=mutate)
        ws.write_config(FULL_YEAR)
        r = ws.run("generate", "--seed", "11")
        self.assertEqual(r.code, 0, r.stdout)
        tails = [SLASH_TAIL.match(row["item/service"]) for row in r.rows]
        tails = [m for m in tails if m]
        self.assertGreater(len(tails), 50)
        self.assertTrue(all(m.group(1) in DELIVERY_TEXTS + DELIVERY_TERSE for m in tails))
        self.assertFalse(any(TAIL.match(row["item/service"]) for row in r.rows))
        self.assertEqual(check(r.run_json, "gap_rules")["status"], "pass")

    def test_bad_vocabulary_is_an_invalid_bundle(self):
        for tails, needle in (
            (["on {weekday}"], "unknown placeholder"),
            (["({mon} {yyyy})"], "must name the day and the month"),
            (["- {m}/{d}"], "printable ASCII without ' - '"),
            ("(Mon 1)", "non-empty list"),
        ):
            with self.subTest(tails):
                ws = Workspace(self)
                ws.install_bundle(mutate=lambda f, t=tails: f.update(vocabulary={"date_tails": t}))
                r = ws.run("generate", "--seed", "1")
                self.assertEqual(r.code, 4, r.stderr)
                self.assertIn(needle, r.stderr)


class DuplicatesTest(unittest.TestCase):  # FR-H3, FR-I5
    def generate(self, mutate=None, seed="42", config=FULL_YEAR):
        ws = Workspace(self)
        ws.install_bundle(mutate=mutate)
        ws.write_config(config)
        return ws.run("generate", "--seed", seed)

    def groups(self, r):
        index = text_to_item(load_fixture_files())
        keys = Counter((row["date_of_transaction"], index[row["item/service"]], amount(row)) for row in r.rows)
        return {k: n for k, n in keys.items() if n > 1}

    def test_duplicate_groups_appear_at_the_ledger_rate(self):
        for seed in ("42", "7"):
            with self.subTest(seed=seed):
                r = self.generate(seed=seed)
                self.assertEqual(r.code, 0, r.stdout)
                groups = self.groups(r)
                rate = len(groups) / len(r.rows)
                self.assertTrue(0.015 <= rate <= 0.025, f"{len(groups)} groups in {len(r.rows)} rows")
                self.assertTrue(all(n == 2 for n in groups.values()))
                c = check(r.run_json, "duplicates")
                self.assertEqual((c["status"], c["hard"], c["reference"]), ("pass", True, 0.02))
                self.assertAlmostEqual(c["value"], rate, places=4)

    def test_a_duplicate_is_entered_right_after_its_twin(self):
        r = self.generate()
        index = text_to_item(load_fixture_files())
        keys = [(row["date_of_transaction"], index[row["item/service"]], amount(row)) for row in r.rows]
        for group in self.groups(r):
            at = [i for i, k in enumerate(keys) if k == group]
            self.assertEqual(at[1], at[0] + 1, group)

    def test_a_rate_beyond_the_fail_band_exits_1_with_the_csv_written(self):  # FR-I5, T32
        r = self.generate(lambda f: messiness(f, duplicate_group_rate=0.2))
        self.assertEqual(r.code, 1, r.stdout)
        self.assertTrue(r.csv_path.exists())
        hard = hard_statuses(r.run_json)
        self.assertEqual(hard.pop("duplicates"), "fail")
        self.assertEqual(set(hard.values()), {"pass"})
        self.assertIn("HARD FAILURE (1 hard check failed)", r.stdout)

    def test_no_duplicates_in_a_year_of_the_fixture_warns(self):
        # About 560 rows: two standard errors of a 2% rate are about 60% of it, so none at
        # all is off by 100%: beyond the pass band, inside the fail band.
        r = self.generate(lambda f: messiness(f, duplicate_group_rate=0))
        self.assertEqual(r.code, 0, r.stdout)
        self.assertEqual(self.groups(r), {})
        c = check(r.run_json, "duplicates")
        self.assertEqual((c["status"], c["value"]), ("warn", 0.0))

    def test_same_amount_pair_in_a_csv_is_a_duplicate_not_a_gap_violation(self):
        ws = Workspace(self)
        bundle = store.load(ws.bundle_dir(ws.install_bundle()))
        days = [date(2026, 1, 1) + timedelta(days=k) for k in range(0, 360, 2)]  # 180 rows, one every other day
        rows = [f"{d},1,50.00,Parking fee at client office" for d in days]
        rows += [f"{d},1,50.00,Parking fee" for d in days[:20]]  # 20 same-day, same-amount pairs
        report = score_csv(HEADER + "\n".join(rows) + "\n", bundle)
        self.assertEqual(report.get("gap_rules").status, "pass")
        dup = report.get("duplicates")
        self.assertEqual((dup.status, dup.hard, dup.value), ("fail", True, 0.1))
        self.assertIn("20 groups in 200 rows", dup.detail)
        self.assertTrue(report.hard_failure)


class EntryOrderTest(unittest.TestCase):  # T28, FR-H4
    def test_rows_are_not_date_sorted_and_back_dated_rows_are_rare_and_small(self):
        ws = Workspace(self)
        ws.install_bundle()
        ws.write_config(FULL_YEAR)
        for seed in ("42", "3"):
            with self.subTest(seed=seed):
                r = ws.run("generate", "--seed", seed)
                dates = [date.fromisoformat(row["date_of_transaction"]) for row in r.rows]
                self.assertNotEqual(dates, sorted(dates))
                late = [(a - b).days for a, b in zip(dates, dates[1:]) if b < a]
                self.assertGreater(len(late), 0)
                self.assertLessEqual(len(late) / len(dates), 0.04, late)
                self.assertLessEqual(max(late), 3, "back-dated by a few days only")

    def test_a_file_is_never_perfectly_sorted(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: messiness(f, backdate_share=0))
        ws.write_config('start = "2026-07-06"\nend = "2026-07-12"\n')
        r = ws.run("generate", "--seed", "5")
        dates = [row["date_of_transaction"] for row in r.rows]
        self.assertGreater(len(set(dates)), 1)
        self.assertEqual(sum(1 for a, b in zip(dates, dates[1:]) if b < a), 1)


class PerUnitPriceTest(unittest.TestCase):  # FR-F5, T19
    def setUp(self):
        self.ws = Workspace(self)
        self.name = self.ws.install_bundle(mutate=add_batteries)
        self.ws.write_config(FULL_YEAR)
        self.r = self.ws.run("generate", "--seed", "21")
        self.batteries = [r for r in self.r.rows if r["item/service"] in BATTERY_PACK + BATTERY_PLAIN]

    def test_about_5_percent_of_stock_rows_show_a_derived_per_unit_price(self):
        self.assertEqual(self.r.code, 0, self.r.stdout)
        card = {25000: 25000, 26500: 26500}
        per_unit = [r for r in self.batteries if centavos(r) not in card]
        share = len(per_unit) / len(self.batteries)
        self.assertGreater(len(self.batteries), 200)
        self.assertTrue(0.035 <= share <= 0.065, share)
        for row in per_unit:
            self.assertIn(row["item/service"], BATTERY_PACK, "only text that states the pack size")
            qty = int(row["qty"])
            self.assertEqual(qty % 12, 0)
            self.assertTrue(any(centavos(row) in (p // 12, -(-p // 12)) for p in card), row)
        self.assertTrue(any(centavos(r) % 100 not in (0, 50, 75) for r in per_unit), "random cents do show")
        for name in ("price_stability", "tidy_cents", "quantities"):
            self.assertEqual(check(self.r.run_json, name)["status"], "pass", name)

    def test_the_total_never_drops_and_moves_by_centavos_only(self):
        base = sum(amount(r) for r in self.r.rows)
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: (add_batteries(f), messiness(f, per_unit_share=0)))
        ws.write_config(FULL_YEAR)
        plain = ws.run("generate", "--seed", "21")
        per_unit = sum(1 for r in self.batteries if centavos(r) not in (25000, 26500))
        plain_total = sum(amount(r) for r in plain.rows)
        self.assertGreaterEqual(base, plain_total)
        self.assertLess(base - plain_total, 12)
        self.assertGreater(per_unit, 0)

    def test_checks_accept_exactly_the_derived_rows(self):
        bundle = store.load(self.ws.bundle_dir(self.name))
        card = "2026-03-03,1,250.00,AA batteries for wireless mics\n2026-03-04,1,265.00,AA batteries for wireless mics\n"
        ok = "2026-03-02,12,20.84,\"AA batteries, 12pcs\"\n"
        report = score_csv(HEADER + card + ok, bundle)
        for name in ("price_stability", "tidy_cents", "quantities"):
            self.assertEqual(report.get(name).status, "pass", name)
        for bad, why in (
            ("2026-03-02,12,20.84,AA batteries for wireless mics\n", "text does not state the pack size"),
            ("2026-03-02,10,20.84,\"AA batteries, 12pcs\"\n", "qty is not whole packs"),
            ("2026-03-02,12,20.81,\"AA batteries, 12pcs\"\n", "not a pack price over pieces"),
        ):
            with self.subTest(why):
                report = score_csv(HEADER + card + bad, bundle)
                self.assertEqual(report.get("tidy_cents").status, "warn")
                self.assertEqual(report.get("price_stability").status, "fail")

    def test_pack_size_only_on_stock_items(self):
        def mutate(files):
            add_batteries(files)
            files["catalog"]["items"]["pantry.batteries"]["goods"] = "hardware"

        ws = Workspace(self)
        ws.install_bundle(mutate=mutate)
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 4)
        self.assertIn("`pack_pcs` is only for items with goods \"stock\"", r.stderr)


class CalibratedMessyRunTest(unittest.TestCase):  # FR-G1 with messiness
    def test_total_stays_in_band_with_batch_items_duplicates_and_per_unit_rows(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=lambda f: (add_delivery(f), add_batteries(f)))
        ws.write_config("target = 80000\n", fixture_defaults=False)
        for seed in range(4):
            with self.subTest(seed=seed):
                r = ws.run("generate", "--seed", str(seed))
                self.assertEqual(r.code, 0, r.stdout + r.stderr)
                total = sum(amount(row) for row in r.rows)
                self.assertTrue(8_000_000 <= total <= 8_160_000, total)
                self.assertEqual(set(hard_statuses(r.run_json).values()), {"pass"})


if __name__ == "__main__":
    unittest.main()
