"""Storylines and project bursts (ticket 07: FR-E3, FR-E4, FR-E9, FR-E10, FR-G2, FR-I4 (5), T11).

Driven through `main(argv)`; the interval check is also driven through
`score_csv` on hand-written CSVs. Project storylines are added to the fixture
bundle with `mutate=`, so the fixture itself is unchanged.
"""

import csv
import io
import statistics
import unittest
from collections import Counter
from datetime import date, timedelta

from tests import test_calibration as calibration
from tests.helpers import Workspace, add_item, load_fixture_files
from txns.bundle import store
from txns.scorecard import score_csv

FULL_YEAR = 'start = "2026-01-01"\nend = "2026-12-31"\n'
PROJECT = {"description": "Post jobs: media and cables bought per job.", "burst_days": [4, 8], "bursts_per_quarter": 2, "quiet_days": 10}
DRIVE_TEXT = ["Backup drive 4TB for job media", "External drive, project offload", "4TB storage drive"]
CABLE_TEXT = ["Thunderbolt cable for DIT cart", "Spare USB-C cable, job kit", "Braided data cable"]
DRIVE_TERSE = ["HDD 4TB", "Drive"]
CABLE_TERSE = ["Cable", "Data cable"]
DRIVE_VARIANTS = set(DRIVE_TEXT + DRIVE_TERSE)
CABLE_VARIANTS = set(CABLE_TEXT + CABLE_TERSE)
PROJECT_TEXT = DRIVE_VARIANTS | CABLE_VARIANTS
SEASON_TEXT = {"Party balloons", "Balloon set for year-end party", "Balloons", "Raffle prize voucher", "Prize voucher for raffle", "Raffle prize", "Prize"}


def add_project(storyline=None, *, drives=None, cables=None):
    """mutate= fn adding a `post_project` storyline with two project-burst items."""

    def mutate(files):
        files["storylines"]["post_project"] = dict(PROJECT, **(storyline or {}))
        add_item(
            files,
            "project.drives",
            storyline="post_project",
            category="Storage",
            points=[(345000, "store-1")],
            quantities=[(1, 4), (2, 3), (4, 2), (8, 1)],
            descriptive=DRIVE_TEXT,
            terse=DRIVE_TERSE,
            archetype="project_burst",
            params=dict({"per_burst": 2.0}, **(drives or {})),
        )
        add_item(
            files,
            "project.cables",
            storyline="post_project",
            category="Storage",
            points=[(45000, "store-1")],
            quantities=[(1, 4), (2, 3)],
            descriptive=CABLE_TEXT,
            terse=CABLE_TERSE,
            archetype="project_burst",
            params=dict({"per_burst": 1.5}, **(cables or {})),
        )

    return mutate


def project_dates(result):
    return sorted({date.fromisoformat(r["date_of_transaction"]) for r in result.rows if r["item/service"] in PROJECT_TEXT})


def clusters(dates, split):
    """Group sorted dates into runs whose neighbours are less than `split` days apart."""
    out = []
    for d in dates:
        if out and (d - out[-1][-1]).days < split:
            out[-1].append(d)
        else:
            out.append([d])
    return out


def metric(run_json, name):
    for c in run_json["scorecard"]["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"no scorecard metric {name}")


class BurstCase(unittest.TestCase):
    def assert_bursts(self, result, *, max_days=8, quiet_days=10):
        """Project rows sit in bursts no longer than max_days, with more than quiet_days between them."""
        self.assertEqual(result.code, 0, result.stdout + result.stderr)
        runs = clusters(project_dates(result), max_days)
        for run in runs:
            self.assertLessEqual((run[-1] - run[0]).days, max_days - 1, f"burst too long: {run}")
        for a, b in zip(runs, runs[1:]):
            self.assertGreater((b[0] - a[-1]).days, quiet_days, f"quiet gap too short between {a} and {b}")
        self.assertEqual(metric(result.run_json, "gap_rules")["status"], "pass")
        return runs


class StorylineSchemaTest(unittest.TestCase):
    def assert_invalid(self, mutate, message):
        ws = Workspace(self)
        ws.install_bundle(mutate=mutate)
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn(message, r.stderr)
        self.assertEqual(ws.all_csvs(), [])

    def test_bad_storyline_settings_exit_4(self):
        cases = [
            ({"burst_days": [9, 4]}, "`burst_days` must be [min, max]"),
            ({"burst_days": [0, 4]}, "`burst_days` must be [min, max]"),
            ({"burst_days": 5}, "`burst_days` must be [min, max]"),
            ({"bursts_per_quarter": -1}, "`bursts_per_quarter` must be a number >= 0"),
            ({"quiet_days": 2.5}, "`quiet_days` must be a whole number"),
            ({"month_weights": {"13": 1.0}}, "`month_weights` key `13`"),
            ({"month_weights": {"12": -1}}, "`month_weights` for month 12"),
        ]
        for settings, message in cases:
            with self.subTest(settings=settings):
                self.assert_invalid(add_project(settings), f"storyline `post_project`: {message}")

    def test_bad_project_burst_rules_and_params_exit_4(self):
        def bad_rules(files):
            files["rules"]["archetypes"]["project_burst"] = {"burst_days": [3]}

        self.assert_invalid(bad_rules, "rules.archetypes.project_burst: `burst_days`")
        self.assert_invalid(add_project(drives={"per_burst": "lots"}), "item `project.drives`: param `per_burst`")


ERRANDS_ITEMS = ("errands.courier", "errands.parking", "errands.mobile_load")


def override_errands(overrides):
    def mutate(files):
        files["storylines"]["errands"]["archetype_overrides"] = overrides

    return mutate


class ArchetypeOverrideTest(unittest.TestCase):  # FR-E3: one primary archetype per item, overridable per storyline
    def item_dates(self, result, files):
        index = {t: i for i, v in files["text"].items()
                 for t in v["descriptive"] + v["terse"] + [x["text"] for x in v.get("vendor", [])]}
        out = {}
        for row in result.rows:
            out.setdefault(index[row["item/service"]], []).append(date.fromisoformat(row["date_of_transaction"]))
        return {k: sorted(v) for k, v in out.items()}

    def test_storyline_runs_its_items_with_the_override(self):
        files = load_fixture_files()
        base_ws, ws = Workspace(self), Workspace(self)
        base_ws.install_bundle()
        name = ws.install_bundle(mutate=override_errands({"petty_daily": "periodic_top_up"}))
        bundle = store.load(ws.bundle_dir(name))
        self.assertEqual(bundle.items["errands.parking"].archetype, "periodic_top_up")
        self.assertEqual(bundle.items["errands.parking"].raw["archetype"], "petty_daily")  # the catalog keeps the primary
        self.assertEqual(bundle.items["pantry.coffee"].archetype, "petty_daily")  # other storylines keep theirs
        for seed in range(4):
            base = self.item_dates(base_ws.run("generate", "--seed", str(seed)), files)
            r = ws.run("generate", "--seed", str(seed))
            self.assertEqual(r.code, 0, r.stdout + r.stderr)
            got = self.item_dates(r, files)
            for item_id in ERRANDS_ITEMS:  # top-ups: one near each month end, at least 7 days apart
                days = got.get(item_id, [])
                self.assertTrue(2 <= len(days) <= 4, (item_id, days))
                for d in days:
                    ends = [date(d.year, d.month + 1, 1) - timedelta(days=1) if d.month < 12 else date(d.year, 12, 31),
                            date(d.year, d.month, 1) - timedelta(days=1)]
                    self.assertLessEqual(min(abs((d - e).days) for e in ends), 2, (item_id, d))
                self.assertTrue(all((b - a).days >= 7 for a, b in zip(days, days[1:])), days)
                self.assertGreater(len(base[item_id]), len(days), "petty daily ran more often")
            for item_id in ("pantry.coffee", "pantry.water", "pantry.snacks"):
                self.assertEqual(got[item_id], base[item_id], "another storyline's rows unchanged")
            self.assertEqual(metric(r.run_json, "gap_rules")["status"], "pass")

    def test_bad_override_exits_4(self):
        cases = [
            ("petty_daily", "`archetype_overrides` must map archetype names to archetype names"),
            ({"petty_daily": 3}, "`archetype_overrides` must map archetype names to archetype names"),
            ({"petty_daily": "weekly_splurge"}, "unknown archetype `weekly_splurge`"),
        ]
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                ws = Workspace(self)
                ws.install_bundle(mutate=override_errands(overrides))
                r = ws.run("generate", "--seed", "1")
                self.assertEqual(r.code, 4, r.stdout + r.stderr)
                self.assertIn(message, r.stderr)
                self.assertEqual(ws.all_csvs(), [])


class ProjectBurstTest(BurstCase):
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_bundle(mutate=add_project())
        self.ws.write_config(FULL_YEAR)

    def test_project_rows_cluster_in_bursts_with_quiet_gaps(self):  # FR-E4
        counts = []
        for seed in range(6):
            r = self.ws.run("generate", "--seed", str(seed))
            runs = self.assert_bursts(r)
            counts.append(len(runs))
            # Petty spend runs all year alongside.
            self.assertEqual(len({row["date_of_transaction"][:7] for row in r.rows}), 12)
        # About 2 bursts a quarter (fewer seen: quiet gaps, and a burst may hold no row).
        self.assertTrue(4 <= statistics.mean(counts) <= 9, counts)

    def test_seed_moves_burst_starts_and_same_seed_repeats(self):  # FR-E9, T1
        a = project_dates(self.ws.run("generate", "--seed", "1"))
        b = project_dates(self.ws.run("generate", "--seed", "2"))
        self.assertNotEqual(a, b)
        self.assertEqual(project_dates(self.ws.run("generate", "--seed", "1")), a)

    def test_items_of_a_storyline_share_its_bursts(self):
        r = self.ws.run("generate", "--seed", "3")
        drives, cables = set(), set()
        for row in r.rows:
            day = date.fromisoformat(row["date_of_transaction"])
            if row["item/service"] in DRIVE_VARIANTS:
                drives.add(day)
            elif row["item/service"] in CABLE_VARIANTS:
                cables.add(day)
        runs = clusters(sorted(drives | cables), 8)
        self.assertTrue(any(set(run) & drives and set(run) & cables for run in runs), "some burst holds both items")

    def test_adding_a_project_storyline_leaves_other_rows_unchanged(self):  # T5
        plain = Workspace(self)
        plain.install_bundle()
        plain.write_config(FULL_YEAR)
        base = plain.run("generate", "--seed", "5")
        with_project = self.ws.run("generate", "--seed", "5")

        def key(rows):
            return Counter(tuple(sorted(r.items())) for r in rows if r["item/service"] not in PROJECT_TEXT)

        self.assertEqual(key(with_project.rows), key(base.rows))


class BurstSettingsTest(BurstCase):
    def run_year(self, mutate, seeds=range(4)):
        ws = Workspace(self)
        ws.install_bundle(mutate=mutate)
        ws.write_config(FULL_YEAR)
        return [ws.run("generate", "--seed", str(s)) for s in seeds]

    def test_bundle_fixes_burst_length(self):  # FR-E9
        for r in self.run_year(add_project({"burst_days": [1, 1], "quiet_days": 3}, drives={"per_burst": 5.0})):
            runs = self.assert_bursts(r, max_days=1, quiet_days=3)
            self.assertTrue(all(len(run) == 1 for run in runs))

    def test_more_bursts_per_quarter_give_more_bursts(self):  # FR-E10
        few = [len(self.assert_bursts(r)) for r in self.run_year(add_project({"bursts_per_quarter": 0.5}))]
        many = [len(self.assert_bursts(r)) for r in self.run_year(add_project({"bursts_per_quarter": 4}))]
        self.assertGreater(sum(many), 2 * sum(few), (few, many))

    def test_no_bursts_no_project_rows(self):
        for r in self.run_year(add_project({"bursts_per_quarter": 0}), seeds=range(2)):
            self.assertEqual(r.code, 0, r.stderr)
            self.assertEqual(project_dates(r), [])

    def test_defaults_come_from_project_burst_rules(self):
        def mutate(files):
            add_project()(files)
            files["storylines"]["post_project"] = {"description": "no burst keys"}
            files["rules"]["archetypes"]["project_burst"] = {"burst_days": [2, 2], "bursts_per_quarter": 3, "quiet_days": 5}

        for r in self.run_year(mutate):
            self.assert_bursts(r, max_days=2, quiet_days=5)


def add_season(files):
    """A seasonal `year_end_party` storyline: nothing before October."""
    weights = {str(m): 0 for m in range(1, 10)}
    weights.update({"10": 1.0, "11": 1.5, "12": 3.0})
    files["storylines"]["year_end_party"] = {
        "description": "Year-end party: decorations, raffle prizes.",
        "month_weights": weights,
        "burst_days": [3, 6],
        "bursts_per_quarter": 3,
        "quiet_days": 5,
    }
    add_item(
        files,
        "party.balloons",
        storyline="year_end_party",
        points=[(35000, "party-1")],
        quantities=[(1, 3), (2, 1)],
        descriptive=["Party balloons", "Balloon set for year-end party"],
        terse=["Balloons"],
        params={"per_week": 2.0},
    )
    add_item(
        files,
        "party.prizes",
        storyline="year_end_party",
        points=[(150000, "party-2")],
        quantities=[(1, 3), (2, 1)],
        descriptive=["Raffle prize voucher", "Prize voucher for raffle"],
        terse=["Raffle prize", "Prize"],
        archetype="project_burst",
        params={"per_burst": 2.0},
    )


class SeasonalStorylineTest(unittest.TestCase):  # FR-E10
    def test_seasonal_storyline_lands_in_its_months_while_petty_spend_runs_all_year(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=add_season)
        ws.write_config(FULL_YEAR)
        season = Counter()
        for seed in range(4):
            r = ws.run("generate", "--seed", str(seed))
            self.assertEqual(r.code, 0, r.stderr)
            party = [row for row in r.rows if row["item/service"] in SEASON_TEXT]
            other = [row for row in r.rows if row["item/service"] not in SEASON_TEXT]
            self.assertTrue(party)
            self.assertTrue(all(row["date_of_transaction"][5:7] in ("10", "11", "12") for row in party), party)
            self.assertEqual(len({row["date_of_transaction"][:7] for row in other}), 12)
            season.update(row["date_of_transaction"][5:7] for row in party)
        self.assertGreater(season["12"], season["10"], "December weighs 3x October")


class CalibratedBurstTest(calibration.CalibrationCase, BurstCase):
    mutate = staticmethod(add_project())

    def test_total_lands_in_band_across_20_seeds_with_project_bursts(self):  # T11
        totals = set()
        for seed in range(20):
            r = self.generate("target = 90000\n", seed)
            totals.add(self.assert_in_band(r, 90_000))
            self.assert_no_plug_rows(r)
            self.assert_bursts(r)
        self.assertGreater(len(totals), 10, "totals vary between seeds")

    def test_calibration_adds_bursts_and_occurrences_never_shrinking_gaps(self):  # FR-G2
        natural = [self.ws.run("generate", "--seed", str(seed)) for seed in range(5)]
        grown = [self.generate("target = 150000\n", seed) for seed in range(5)]
        for r in grown:
            self.assert_in_band(r, 150_000)
            self.assert_no_plug_rows(r)
        bursts = lambda runs: sum(len(self.assert_bursts(r)) for r in runs)  # noqa: E731
        project_rows = lambda runs: sum(len(project_dates(r)) for r in runs)  # noqa: E731
        self.assertGreater(bursts(grown), bursts(natural), "more bursts")
        self.assertGreater(project_rows(grown), project_rows(natural), "more project rows")

    def test_saturated_storyline_keeps_its_quiet_gaps(self):  # FR-G2, T21
        for seed in range(2):
            r = self.generate("target = 400000\n", seed)
            self.assert_in_band(r, 400_000)
            self.assert_no_plug_rows(r)
            runs = self.assert_bursts(r)
            self.assertGreater(len(runs), 3)

    def test_storyline_multiplier_adds_bursts(self):  # FR-E12, T20a
        base = [self.assert_bursts(self.ws.run("generate", "--seed", str(s))) for s in range(4)]
        self.ws.write_config("[multipliers.storyline]\npost_project = 3.0\n")
        more = [self.assert_bursts(self.ws.run("generate", "--seed", str(s))) for s in range(4)]
        self.assertGreater(sum(map(len, more)), sum(map(len, base)))


class IntervalRegularityTest(unittest.TestCase):  # FR-I4 (5)
    def setUp(self):
        self.files = load_fixture_files()
        self.coffee = self.files["text"]["pantry.coffee"]["descriptive"][0]
        self.water = self.files["text"]["pantry.water"]["descriptive"][0]

    def bundle(self, mutate=None):
        ws = Workspace(self)
        return store.load(ws.bundle_dir(ws.install_bundle(mutate=mutate)))

    def csv(self, rows):
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["date_of_transaction", "qty", "unit_price", "item/service"])
        writer.writerows([d.isoformat(), 1, price, text] for d, price, text in rows)
        return out.getvalue()

    def score(self, rows, mutate=None):
        report = score_csv(self.csv(rows), self.bundle(mutate))
        return report.get("interval_regularity.metronomic"), report.get("interval_regularity.clumped")

    def test_same_item_every_seven_days_is_metronomic(self):
        rows = [(date.fromordinal(date(2026, 7, 1).toordinal() + 7 * k), "165.00", self.coffee) for k in range(13)]
        metronomic, clumped = self.score(rows)
        self.assertEqual((metronomic.status, metronomic.hard), ("warn", False))
        self.assertIn("pantry.coffee", metronomic.value)
        self.assertEqual(clumped.status, "pass")

    def test_run_of_consecutive_days_then_nothing_is_clumped(self):
        rows = [(date(2026, 7, d), "165.00", self.coffee) for d in range(1, 21)]
        rows.append((date(2026, 9, 30), "37.50", self.water))  # the span runs to the end of September
        metronomic, clumped = self.score(rows)
        self.assertEqual(clumped.status, "warn")
        self.assertIn("pantry.coffee", clumped.value)
        self.assertGreater(clumped.value["pantry.coffee"], 2.0)

        def relaxed(files):
            files["rules"]["archetypes"]["petty_daily"]["interval_cv_max"] = 10

        self.assertEqual(self.score(rows, relaxed)[1].status, "pass")

    def test_scheduled_items_may_be_regular(self):
        def add_subscription(files):
            add_item(
                files,
                "stack.editing_suite",
                storyline="office_pantry",
                points=[(264200, "soft-1")],
                quantities=[(1, 1)],
                descriptive=["Editing suite monthly plan"],
                price_class="subscription",
            )

        rows = [(date(2026, m, 5), "2642.00", "Editing suite monthly plan") for m in range(1, 13)]
        metronomic, clumped = self.score(rows, add_subscription)
        self.assertEqual((metronomic.status, clumped.status), ("pass", "pass"))

    def test_generated_run_reports_both_metrics(self):
        ws = Workspace(self)
        ws.install_bundle(mutate=add_project())
        ws.write_config(FULL_YEAR)
        r = ws.run("generate", "--seed", "4")
        self.assertEqual(r.code, 0)
        for name in ("interval_regularity.metronomic", "interval_regularity.clumped"):
            m = metric(r.run_json, name)
            self.assertEqual((m["section"], m["hard"], m["status"]), ("anomaly", False, "pass"), m)
            self.assertIn(name, r.stdout)


if __name__ == "__main__":
    unittest.main()
