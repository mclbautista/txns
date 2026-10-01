"""Recurring charges: subscriptions and top-ups (ticket 06: FR-E3 to FR-E9, FR-F3, FR-I2, T11, T21, T22).

Driven through `main(argv)`; assertions are over the written CSV and run.json.
Subscription and top-up items are added to the fixture bundle in the test.
"""

import calendar as pycal
import statistics
import unittest
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from tests.helpers import Workspace, add_item, load_fixture_files

FULL_YEAR_2026 = 'start = "2026-01-01"\nend = "2026-12-31"\n'
SUBS = ("subs.render_farm", "subs.site_hosting", "subs.stock_music")
TOPUPS = ("topup.prepaid_data", "topup.toll_card")
NO_SLIPS = {"slip_share": 0, "skip_share": 0, "double_share": 0}


def add_recurring(files, *, sub_params=None, topup_params=None):
    sub_params = sub_params or {}
    topup_params = topup_params or {}
    subs = {
        "subs.render_farm": dict(points=[(119_000, "render-1")], quantities=[(1, 4), (2, 3), (3, 2), (4, 1), (5, 1)]),
        "subs.site_hosting": dict(points=[(89_900, "host-1")], quantities=[(1, 1)]),
        "subs.stock_music": dict(points=[(64_950, "music-1")], quantities=[(1, 2), (2, 1)]),
    }
    anchors = {
        "subs.render_farm": {"anchor_day": 5},
        "subs.site_hosting": {"anchor_day": 31},
        "subs.stock_music": {"anchor_day": 1},
    }
    for item_id, card in subs.items():
        name = item_id.split(".")[1].replace("_", " ")
        add_item(
            files,
            item_id,
            storyline="subscriptions",
            archetype="fixed_day_subscription",
            price_class="subscription",
            category="Subscriptions",
            params={**anchors[item_id], **sub_params.get(item_id, {})},
            descriptive=[f"Monthly {name} plan", f"{name.title()} subscription, monthly", f"{name} plan renewal"],
            terse=[f"{name} sub", f"{name} monthly"],
            **card,
        )
    topups = {
        "topup.prepaid_data": dict(points=[(30_000, "telco-2"), (50_000, "telco-2")], quantities=[(1, 1)], rate=1.0),
        "topup.toll_card": dict(points=[(75_000, "toll-1"), (50_000, "toll-1")], quantities=[(1, 3), (2, 1)], rate=2.0),
    }
    for item_id, card in topups.items():
        name = item_id.split(".")[1].replace("_", " ")
        rate = card.pop("rate")
        add_item(
            files,
            item_id,
            storyline="errands",
            archetype="periodic_top_up",
            category="Transportation",
            params={"per_month": rate, **topup_params.get(item_id, {})},
            descriptive=[f"{name.title()} top-up", f"Reload of {name}", f"{name} top up, end of period"],
            terse=[f"{name} load", f"{name} reload"],
            **card,
        )


def with_rules(**archetypes):
    def mutate(files):
        add_recurring(files)
        for name, rules in archetypes.items():
            files["rules"]["archetypes"].setdefault(name, {}).update(rules)

    return mutate


def metric(run_json, name):
    for c in run_json["scorecard"]["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"no scorecard metric {name}")


def business_day(d, cal):
    return d.weekday() < 5 and d not in cal


def roll_forward(d, cal):
    while not business_day(d, cal):
        d += timedelta(days=1)
    return d


def anchor(year, month, day):
    return date(year, month, min(day, pycal.monthrange(year, month)[1]))


class RecurringCase(unittest.TestCase):
    mutate = staticmethod(add_recurring)

    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_bundle(mutate=self.mutate)
        self.files = load_fixture_files()
        self.mutate(self.files)
        self.index = {
            t: i
            for i, v in self.files["text"].items()
            for t in v["descriptive"] + v["terse"] + [x["text"] for x in v.get("vendor", [])]
        }
        self.params = {i: e["params"] for i, e in self.files["catalog"]["items"].items()}
        self.holidays = {
            date.fromisoformat(d["date"]) for y in self.files["holidays"]["years"].values() for d in y["days"]
        }

    def generate(self, config="", seed=1):
        self.ws.write_config(config)
        r = self.ws.run("generate", "--seed", str(seed))
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        return r

    def dates(self, r):
        """{item id: sorted dates} for the run's rows."""
        out = defaultdict(list)
        for row in r.rows:
            out[self.index[row["item/service"]]].append(date.fromisoformat(row["date_of_transaction"]))
        return {k: sorted(v) for k, v in out.items()}

    def on_time(self, item_id, year):
        day = self.params[item_id]["anchor_day"]
        days = set()
        for y, m in [(year - 1, 12)] + [(year, m) for m in range(1, 13)]:
            a = anchor(y, m, day)
            days |= {a, roll_forward(a, self.holidays)}
        return days


class SubscriptionTest(RecurringCase):
    def test_anchor_day_with_weekend_and_holiday_roll_forward(self):  # FR-E8
        ws = Workspace(self)
        ws.install_bundle(mutate=with_rules(fixed_day_subscription=NO_SLIPS))
        r = ws.run("generate", "--seed", "3")
        self.assertEqual(r.code, 0, r.stderr)
        got = self.dates(r)
        # Q3 2026: Aug 31 is National Heroes Day (Monday), Aug 1 a Saturday.
        self.assertEqual(got["subs.site_hosting"], [date(2026, 7, 31), date(2026, 9, 1), date(2026, 9, 30)])
        self.assertEqual(got["subs.stock_music"], [date(2026, 7, 1), date(2026, 8, 3), date(2026, 9, 1)])
        self.assertEqual(got["subs.render_farm"], [date(2026, 7, 6), date(2026, 8, 5), date(2026, 9, 7)])

    def test_seed_moves_only_slips(self):  # FR-E9
        ws = Workspace(self)
        ws.install_bundle(mutate=with_rules(fixed_day_subscription=NO_SLIPS))
        ws.write_config(FULL_YEAR_2026)
        runs = [ws.run("generate", "--seed", str(s)) for s in (1, 2, 3)]
        subs = [{k: v for k, v in self.dates(r).items() if k in SUBS} for r in runs]
        self.assertEqual(subs[0], subs[1])
        self.assertEqual(subs[0], subs[2])
        for item_id, days in subs[0].items():
            self.assertEqual(len(days), 12, item_id)
            self.assertTrue(set(days) <= self.on_time(item_id, 2026), item_id)
            self.assertTrue(all(business_day(d, self.holidays) for d in days), item_id)

    def test_about_95_percent_on_anchor_with_rare_slips_skips_and_doubles(self):  # T22, FR-E8
        hits = n = 0
        shifts = Counter()
        months = Counter()
        for seed in range(20):
            r = self.generate(FULL_YEAR_2026, seed)
            for item_id, days in self.dates(r).items():
                if item_id not in SUBS:
                    continue
                on_time = self.on_time(item_id, 2026)
                if item_id != "subs.site_hosting":  # a day-31 anchor often rolls into the next month
                    months.update(Counter(Counter(d.month for d in days).values()))
                    months[0] += 12 - len({d.month for d in days})
                for d in days:
                    n += 1
                    if d in on_time:
                        hits += 1
                    else:
                        nearest = min(on_time, key=lambda a: abs((d - a).days))
                        shifts[(d - nearest).days] += 1
            m = metric(r.run_json, "anchor_hits")
            self.assertEqual(m["section"], "timing")
            self.assertIn(m["status"], ("pass", "warn"))
        rate = hits / n
        self.assertGreaterEqual(rate, 0.92, f"hit rate {rate:.3f}")
        self.assertLess(rate, 1.0, "some charges slip")
        self.assertTrue(set(shifts) <= set(range(-7, 0)) | set(range(1, 8)), shifts)
        self.assertGreater(sum(v for k, v in shifts.items() if k > 0), sum(v for k, v in shifts.items() if k < 0))
        # Calendar months: one charge nearly always; a skipped (0) or doubled (2) month is rare.
        rare = months[0] + months[2]
        self.assertLess(rare / sum(months.values()), 0.1, months)

    def test_subscriptions_ignore_weekday_weights(self):  # FR-E6
        def midweek_only(files):
            add_recurring(files)
            files["rules"]["calendar"]["weekday_weights"] = [0, 0, 1, 0, 0, 0, 0]
            files["rules"]["archetypes"]["fixed_day_subscription"] = {"weekday_weights": [0, 0, 1, 0, 0, 0, 0]}

        ws = Workspace(self)
        ws.install_bundle(mutate=midweek_only)
        skewed = ws.run("generate", "--seed", "5")
        plain = self.generate("", 5)
        subs = lambda r: {k: v for k, v in self.dates(r).items() if k in SUBS}  # noqa: E731
        self.assertEqual(subs(skewed), subs(plain))
        self.assertGreater(len({d.weekday() for v in subs(plain).values() for d in v}), 1)

    def test_qty_is_a_steady_seat_count_from_the_allowed_set(self):  # FR-F3
        seen = set()
        for seed in range(6):
            r = self.generate(FULL_YEAR_2026, seed)
            by_item = defaultdict(set)
            for row in r.rows:
                by_item[self.index[row["item/service"]]].add(row["qty"])
            for item_id in SUBS:
                self.assertEqual(len(by_item[item_id]), 1, f"{item_id} seats change within a run")
            self.assertLessEqual(by_item["subs.render_farm"], {"1", "2", "3", "4", "5"})
            seen |= by_item["subs.render_farm"]
        self.assertGreater(len(seen), 1, "seat count drawn from the weighted allowed set")

    def test_seats_are_the_calibration_lever_not_charges(self):  # FR-G2, FR-E12
        ws = Workspace(self)
        ws.install_bundle(mutate=self.mutate)
        ws.write_config("[multipliers.class]\nsubscription = 4.0\n")

        def subs(r):
            rows = [row for row in r.rows if self.index[row["item/service"]] == "subs.render_farm"]
            return (
                [row["date_of_transaction"] for row in rows],
                [int(row["qty"]) for row in rows],
                {row["unit_price"] for row in rows},
            )

        q0, q1 = [], []
        for seed in range(6):
            base = self.generate("", seed)
            raised = ws.run("generate", "--seed", str(seed))
            self.assertEqual(raised.code, 0, raised.stderr)
            d0, s0, p0 = subs(base)
            d1, s1, p1 = subs(raised)
            self.assertEqual(d0, d1, "charges keep their schedule")
            self.assertEqual(p0, p1, "no unit price moves")
            q0 += s0
            q1 += s1
        self.assertGreater(statistics.mean(q1), statistics.mean(q0) + 1, "more seats")

    def test_missing_anchor_day_exits_4(self):  # FR-E9
        def no_anchor(files):
            add_recurring(files)
            del files["catalog"]["items"]["subs.site_hosting"]["params"]["anchor_day"]

        ws = Workspace(self)
        ws.install_bundle(mutate=no_anchor)
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 4, r.stderr)
        self.assertIn("anchor_day", r.stderr)
        self.assertEqual(ws.all_csvs(), [])

    def test_quarterly_cycle(self):
        def quarterly(files):
            add_recurring(files, sub_params={"subs.site_hosting": {"every_months": 3, "anchor_month": 2}})

        ws = Workspace(self)
        ws.install_bundle(mutate=quarterly)
        ws.write_config(FULL_YEAR_2026)
        r = ws.run("generate", "--seed", "4")
        self.assertEqual(r.code, 0, r.stderr)
        months = [d.month for d in self.dates(r)["subs.site_hosting"]]
        self.assertTrue(set(months) <= {2, 3, 5, 6, 8, 9, 11, 12}, months)
        self.assertLessEqual(len(months), 4)
        self.assertGreaterEqual(len(months), 3)


class TopUpTest(RecurringCase):
    def test_anchored_to_period_end_with_jitter(self):  # FR-E4
        for seed in range(5):
            r = self.generate(FULL_YEAR_2026, seed)
            got = self.dates(r)
            for d in got["topup.prepaid_data"]:
                ends = [anchor(d.year, d.month, 31), date(d.year, d.month, 1) - timedelta(days=1)]
                self.assertLessEqual(min(abs((d - e).days) for e in ends), 2, d)
            self.assertGreaterEqual(len(got["topup.prepaid_data"]), 11)
            for d in got["topup.toll_card"]:  # twice a month: mid-month and month end
                dim = pycal.monthrange(d.year, d.month)[1]
                mids = [date(d.year, d.month, dim // 2), date(d.year, d.month, dim // 2 + 1)]
                ends = mids + [anchor(d.year, d.month, 31), date(d.year, d.month, 1) - timedelta(days=1)]
                self.assertLessEqual(min(abs((d - e).days) for e in ends), 2, d)
            self.assertGreaterEqual(len(got["topup.toll_card"]), 20)

    def test_skewed_to_weekdays_and_off_regular_holidays(self):  # FR-E6
        days = []
        for seed in range(12):
            r = self.generate(FULL_YEAR_2026, seed)
            got = self.dates(r)
            days += got["topup.prepaid_data"] + got["topup.toll_card"]
        weekend = sum(1 for d in days if d.weekday() >= 5) / len(days)
        self.assertLess(weekend, 0.22, f"weekend share {weekend:.2f} (uniform would be 0.29)")
        regular = {
            date.fromisoformat(d["date"])
            for d in self.files["holidays"]["years"]["2026"]["days"]
            if d["kind"] == "regular"
        }
        self.assertLess(sum(1 for d in days if d in regular) / len(days), 0.02)

    def test_seed_moves_the_jitter(self):  # FR-E9
        a = self.dates(self.generate("", 1))["topup.toll_card"]
        b = self.dates(self.generate("", 2))["topup.toll_card"]
        self.assertNotEqual(a, b)


class GapTest(RecurringCase):
    def gaps(self, r):
        # Distinct days: a same-day, same-amount duplicate (batch entry, FR-H3) is not a gap.
        days = {k: sorted(set(v)) for k, v in self.dates(r).items()}
        return {k: min((b - a).days for a, b in zip(v, v[1:])) for k, v in days.items() if len(v) > 1}

    def test_per_archetype_minimum_gaps_hold(self):  # T21, FR-E5
        def busy(files):
            # Top-ups every 5 days would break the 7-day gap: gap rules win.
            add_recurring(files, topup_params={"topup.toll_card": {"per_month": 6.0}})

        ws = Workspace(self)
        ws.install_bundle(mutate=busy)
        ws.write_config(FULL_YEAR_2026)
        for seed in range(8):
            r = ws.run("generate", "--seed", str(seed))
            self.assertEqual(r.code, 0, r.stderr)
            gaps = self.gaps(r)
            for item_id in SUBS:
                self.assertGreaterEqual(gaps[item_id], 25, item_id)
            for item_id in TOPUPS:
                self.assertGreaterEqual(gaps[item_id], 7, item_id)
            self.assertEqual(metric(r.run_json, "gap_rules")["status"], "pass")
            m = metric(r.run_json, "min_gap")
            self.assertEqual((m["section"], m["status"]), ("timing", "pass"))
            self.assertEqual(m["reference"]["subs.site_hosting"], 25)
            self.assertEqual(m["reference"]["topup.toll_card"], 7)
            self.assertEqual(m["reference"]["pantry.coffee"], 1)
            self.assertEqual(m["value"]["topup.toll_card"], gaps["topup.toll_card"])

    def test_per_item_override(self):  # FR-E5
        def override(files):
            add_recurring(files, topup_params={"topup.toll_card": {"min_gap_days": 12}})
            files["rules"]["archetypes"]["fixed_day_subscription"] = {"min_gap_days": 27}

        ws = Workspace(self)
        ws.install_bundle(mutate=override)
        ws.write_config(FULL_YEAR_2026)
        r = ws.run("generate", "--seed", "9")
        self.assertEqual(r.code, 0, r.stderr)
        m = metric(r.run_json, "min_gap")
        self.assertEqual(m["reference"]["topup.toll_card"], 12)
        self.assertEqual(m["reference"]["subs.render_farm"], 27)
        self.assertEqual(metric(r.run_json, "gap_rules")["status"], "pass")
        self.assertGreaterEqual(self.gaps(r)["topup.toll_card"], 12)
        self.assertGreaterEqual(self.gaps(r)["subs.render_farm"], 27)


class ScorecardTest(RecurringCase):
    def test_anchor_hit_rate_warns_when_charges_wander(self):  # FR-I2
        ws = Workspace(self)
        ws.install_bundle(mutate=with_rules(fixed_day_subscription={"slip_share": 0.6}))
        ws.write_config(FULL_YEAR_2026)
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0, r.stderr)
        m = metric(r.run_json, "anchor_hits")
        self.assertEqual(m["status"], "warn", m)
        self.assertLess(m["value"], 0.8)
        self.assertIn("anchor_hits", r.stdout)

    def test_no_subscriptions_passes(self):
        ws = Workspace(self)
        ws.install_bundle()
        r = ws.run("generate", "--seed", "1")
        m = metric(r.run_json, "anchor_hits")
        self.assertEqual((m["status"], m["value"]), ("pass", None))


class TotalsTest(RecurringCase):
    def test_totals_stay_in_band_across_seeds(self):  # T11
        totals = set()
        for seed in range(20):
            self.ws.write_config("target = 60000\n", fixture_defaults=False)
            r = self.ws.run("generate", "--seed", str(seed))
            self.assertEqual(r.code, 0, r.stdout + r.stderr)
            total = sum(int(Decimal(x["qty"]) * Decimal(x["unit_price"]) * 100) for x in r.rows)
            self.assertTrue(6_000_000 <= total <= 6_120_000, total)
            totals.add(total)
            hard = {c["name"]: c["status"] for c in r.run_json["scorecard"]["checks"] if c["hard"]}
            self.assertEqual(set(hard.values()), {"pass"}, hard)
        self.assertGreater(len(totals), 10)


if __name__ == "__main__":
    unittest.main()
