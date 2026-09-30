"""Calendar shape: weekdays, holidays and seasons (ticket 04: FR-E5, FR-E6, FR-E7, FR-I2, T23).

Driven through `main(argv)`; assertions are over the written CSV and run.json.
"""

import calendar as pycal
import json
import math
import unittest
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from tests.helpers import FIXTURE_BUNDLE, Workspace, add_item
from txns import holidays

REPO = Path(__file__).resolve().parent.parent
FIXTURE_TEXT = {
    k: v["descriptive"] + v["terse"]
    for k, v in json.loads((FIXTURE_BUNDLE / "text.json").read_text(encoding="utf-8")).items()
}
FULL_YEAR_2026 = 'start = "2026-01-01"\nend = "2026-12-31"\n'
NEW_METRICS = (
    "weekday_shares.mon",
    "weekday_shares.tue",
    "weekday_shares.wed",
    "weekday_shares.thu",
    "weekday_shares.fri",
    "weekday_shares.sat",
    "weekday_shares.sun",
    "weekday_shares.big_ticket_weekend",
    "holiday_share",
    "month_end_shape",
    "monthly_spread.rows",
    "monthly_spread.spend",
    "daily_ceiling",
)


def committed():
    return holidays.load_committed(REPO)


def dates_of(result):
    return [date.fromisoformat(r["date_of_transaction"]) for r in result.rows]


def metric(run_json, name):
    for c in run_json["scorecard"]["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"no scorecard metric {name}")


def set_calendar(**rules):
    def mutate(files):
        files["rules"].setdefault("calendar", {}).update(rules)

    return mutate


def set_archetype(name, **rules):
    def mutate(files):
        files["rules"]["archetypes"].setdefault(name, {}).update(rules)

    return mutate


class CommittedCalendarTest(unittest.TestCase):
    def test_covers_2023_through_the_latest_proclaimed_year_citing_proclamations(self):
        data = committed()
        cal = holidays.parse(data, "inputs/ph-holidays.json")
        self.assertEqual(cal.years[0], 2023)
        self.assertEqual(list(cal.years), list(range(2023, cal.years[-1] + 1)))
        self.assertGreaterEqual(cal.years[-1], 2027)  # 2027 proclaimed September 2026
        for year, table in data["years"].items():
            annual = [p for p in table["proclamations"] if "Regular holidays and special" in p["subject"]]
            self.assertTrue(annual, f"{year}: no annual proclamation cited")
            for p in table["proclamations"]:
                self.assertRegex(p["id"], r"^Proclamation No\. \d+, s\. \d{4}$")

    def test_regular_holidays_include_the_fixed_days_and_holy_week(self):
        cal = holidays.parse(committed())
        for year in cal.years:
            easter = holidays.easter(year)
            for d in (easter - timedelta(days=3), easter - timedelta(days=2)):  # Maundy Thursday, Good Friday
                self.assertTrue(cal.is_regular(d), d)
            self.assertTrue(cal.is_special(easter - timedelta(days=1)), f"Black Saturday {year}")
            for month, day in ((1, 1), (5, 1), (6, 12), (12, 25), (12, 30)):
                self.assertTrue(cal.is_regular(date(year, month, day)), date(year, month, day))
            regular = cal.regular_between(date(year, 1, 1), date(year, 12, 31))
            self.assertTrue(10 <= len(regular) <= 12, (year, len(regular)))

    def test_fixture_bundle_carries_the_committed_calendar_for_its_years(self):
        fixture = json.loads((FIXTURE_BUNDLE / "holidays.json").read_text(encoding="utf-8"))
        years = [int(y) for y in fixture["years"]]
        self.assertEqual(fixture, holidays.for_years(committed(), years))
        with self.assertRaises(holidays.CalendarInvalid):
            holidays.for_years(committed(), [2022])


class BundleCalendarTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)

    def test_bundle_without_a_holiday_calendar_exits_4(self):
        self.ws.install_bundle(mutate=lambda f: f.pop("holidays"))
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 4, r.stderr)
        self.assertIn("holidays.json", r.stderr)
        self.assertEqual(self.ws.all_csvs(), [])

    def test_malformed_calendar_exits_4(self):
        def mutate(files):
            files["holidays"]["years"]["2026"]["days"][0]["date"] = "2025-01-01"

        self.ws.install_bundle(mutate=mutate)
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 4)
        self.assertIn("2025-01-01 is listed under 2026", r.stderr)

    def test_period_outside_the_calendar_warns(self):
        def mutate(files):
            files["holidays"] = holidays.for_years(files["holidays"], [2023, 2024, 2025])

        self.ws.install_bundle(mutate=mutate)
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0, r.stdout)
        self.assertIn("no holiday shaping for 2026", r.stderr)
        self.assertTrue(any("2026" in w and "holiday" in w for w in r.run_json["warnings"]))


class WeekdayTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.write_config(FULL_YEAR_2026)

    def test_petty_rows_skew_to_weekdays(self):  # FR-E6
        self.ws.install_bundle()
        r = self.ws.run("generate", "--seed", "7")
        c = Counter(d.weekday() for d in dates_of(r))
        n = sum(c.values())
        for wd in range(5):
            self.assertGreater(c[wd] / n, 0.13, wd)
        self.assertLess(c[5] / n, 0.12)
        self.assertLess(c[6] / n, 0.08)
        self.assertLess(c[6], c[5])
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            self.assertEqual(metric(r.run_json, f"weekday_shares.{day}")["status"], "pass", day)

    def test_per_archetype_weights_override_the_bundle_default(self):
        self.ws.install_bundle(mutate=set_archetype("petty_daily", weekday_weights=[1, 1, 1, 1, 1, 0, 0]))
        r = self.ws.run("generate", "--seed", "7")
        self.assertTrue(r.rows)
        self.assertEqual([d for d in dates_of(r) if d.weekday() >= 5], [])

    def test_flat_weights_fail_the_weekday_metric(self):
        def mutate(files):
            files["rules"]["archetypes"]["petty_daily"].pop("weekday_weights")
            files["rules"]["calendar"]["weekday_weights"] = [1] * 7

        self.ws.install_bundle(mutate=mutate)
        r = self.ws.run("generate", "--seed", "7")
        self.assertEqual(r.code, 0)  # soft
        self.assertNotEqual(metric(r.run_json, "weekday_shares.sun")["status"], "pass")


def full_year_run(test, seed, mutate=None):
    ws = Workspace(test)
    ws.write_config(FULL_YEAR_2026)
    ws.install_bundle(mutate=mutate)
    r = ws.run("generate", "--seed", str(seed))
    test.assertEqual(r.code, 0, r.stdout)
    return r


class HolidayTest(unittest.TestCase):
    REGULAR_2026 = set(holidays.parse(committed()).regular_between(date(2026, 1, 1), date(2026, 12, 31)))

    def on_holidays(self, r):
        return sum(1 for d in dates_of(r) if d in self.REGULAR_2026)

    def test_regular_holidays_are_nearly_empty_with_a_bundle_set_leak(self):  # FR-E7, T23
        r = full_year_run(self, 11)
        leak = self.on_holidays(r)
        self.assertLessEqual(leak / len(r.rows), 0.01)
        self.assertEqual(metric(r.run_json, "holiday_share")["status"], "pass")

        self.assertEqual(self.on_holidays(full_year_run(self, 11, set_calendar(holiday_leak=0.0))), 0)

        loud = full_year_run(self, 11, set_calendar(holiday_leak=1.0))
        self.assertGreaterEqual(self.on_holidays(loud), 15)
        self.assertGreater(self.on_holidays(loud), 4 * leak)
        self.assertIn(metric(loud.run_json, "holiday_share")["status"], ("warn", "fail"))

    def test_holy_week_dips(self):  # FR-E7
        first, last = holidays.holy_week(2026)
        ds = dates_of(full_year_run(self, 11))
        self.assertLessEqual(sum(1 for d in ds if d in (date(2026, 4, 2), date(2026, 4, 3))), 1)
        ds = dates_of(full_year_run(self, 11, set_calendar(holy_week_factor=0.0)))
        self.assertTrue(ds)
        self.assertEqual([d for d in ds if first <= d <= last], [])


class SeasonTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.write_config(FULL_YEAR_2026)

    @staticmethod
    def per_day(ds, months):
        n_days = sum(pycal.monthrange(2026, m)[1] for m in months)
        return sum(1 for d in ds if d.month in months) / n_days

    def test_october_to_december_carries_the_event_season_lift(self):  # FR-E7
        self.ws.install_bundle()
        ds = dates_of(self.ws.run("generate", "--seed", "3"))
        self.assertGreater(self.per_day(ds, (10, 11, 12)), 1.05 * self.per_day(ds, (7, 8, 9)))

        ws = Workspace(self)
        ws.write_config(FULL_YEAR_2026)
        ws.install_bundle(mutate=set_calendar(month_weights={"10": 2.0, "11": 2.0, "12": 2.0}))
        ds = dates_of(ws.run("generate", "--seed", "3"))
        self.assertGreater(self.per_day(ds, (10, 11, 12)), 1.5 * self.per_day(ds, (7, 8, 9)))

    def test_storyline_month_weights_override_the_bundle_curve(self):  # FR-E10
        def mutate(files):
            files["storylines"]["errands"]["month_weights"] = {str(m): 0.0 for m in (10, 11, 12)}

        self.ws.install_bundle(mutate=mutate)
        r = self.ws.run("generate", "--seed", "3")
        errands = {t for item in ("courier", "parking", "mobile_load") for t in FIXTURE_TEXT[f"errands.{item}"]}
        texts = {row["item/service"] for row in r.rows if row["date_of_transaction"] < "2026-10-01"}
        self.assertTrue(texts & errands)  # errands run the rest of the year
        late = [row for row in r.rows if row["date_of_transaction"] >= "2026-10-01" and row["item/service"] in errands]
        self.assertEqual(late, [])

    def test_month_end_shape_follows_the_bundle(self):
        def share(ds):
            return sum(1 for d in ds if d.day > pycal.monthrange(d.year, d.month)[1] - 3) / len(ds)

        self.ws.install_bundle()
        r = self.ws.run("generate", "--seed", "5")
        base = share(dates_of(r))
        self.assertLess(base, 0.15)
        self.assertEqual(metric(r.run_json, "month_end_shape")["status"], "pass")
        ws = Workspace(self)
        ws.write_config(FULL_YEAR_2026)
        ws.install_bundle(mutate=set_calendar(month_end={"days": 3, "factor": 3.0}))
        self.assertGreater(share(dates_of(ws.run("generate", "--seed", "5"))), 0.18)


def sparse_bundle(files):
    """Only sparse items (about 0.4 rows a day in all), so a 3x-average day is a few rows."""
    for key in ("rate_cards", "text"):
        files[key].clear()
    files["catalog"]["items"].clear()
    for i in range(20):
        add_item(
            files,
            f"supply.item_{i:02d}",
            storyline="office_pantry",
            points=[(12350 + 100 * i, "shop-1")],
            quantities=[(1, 1)],
            descriptive=[f"Pantry supply number {i}"],
            params={"per_week": 0.15},
        )


class DailyCeilingTest(unittest.TestCase):  # FR-E5
    def busiest(self, r):
        ds = dates_of(r)
        cap = math.ceil(3 * len(ds) / 365)
        return max(Counter(ds).values()), cap

    def test_no_ordinary_day_exceeds_about_three_times_the_average(self):
        ws = Workspace(self)
        ws.write_config(FULL_YEAR_2026)
        ws.install_bundle(mutate=sparse_bundle)
        r = ws.run("generate", "--seed", "9")
        self.assertEqual(r.code, 0, r.stdout)  # gap rules still hold
        busiest, cap = self.busiest(r)
        self.assertLessEqual(busiest, cap)
        self.assertEqual(metric(r.run_json, "daily_ceiling")["status"], "pass")

        # Without the ceiling the same plan has days above it, so the check above bites.
        loose = Workspace(self)
        loose.write_config(FULL_YEAR_2026)

        def no_ceiling(files):
            sparse_bundle(files)
            files["rules"]["calendar"]["daily_ceiling"] = 1000

        loose.install_bundle(mutate=no_ceiling)
        r = loose.run("generate", "--seed", "9")
        busiest, cap = self.busiest(r)
        self.assertGreater(busiest, cap)


class ScorecardCalendarMetricsTest(unittest.TestCase):  # FR-I2, T23, T30
    def test_default_fixture_run_passes_the_calendar_metrics(self):
        ws = Workspace(self)
        ws.install_bundle()
        for seed in ("42", "1", "2"):
            r = ws.run("generate", "--seed", seed)
            self.assertEqual(r.code, 0)
            for name in NEW_METRICS:
                c = metric(r.run_json, name)
                self.assertEqual(c["status"], "pass", c)
                self.assertFalse(c["hard"])
                self.assertIn(f"/{name}: ", r.stdout)
