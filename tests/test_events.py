"""Events and big-ticket purchases (ticket 08: FR-E3 to FR-E6, FR-F6, FR-G2, T11, T18, T21, T23, T24).

Driven through `main(argv)`; the gap check is also driven through `score_csv`
on hand-written CSVs. The event items are added to the fixture bundle with
`mutate=` (`add_events`), so the fixture itself is unchanged.
"""

import unittest
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from tests.helpers import Workspace, add_item, load_fixture_files
from txns.bundle import store
from txns.scorecard import score_csv

FULL_YEAR = 'start = "2026-01-01"\nend = "2026-12-31"\n'
EVENTS = {
    "description": "Company events: venue bookings, party catering, raffle prizes, stage hire.",
    "parties_per_quarter": 3,
}
VENUE, CATERING, PRIZES, STAGE = "events.venue", "events.catering", "events.prizes", "events.stage"
TEXT = {
    VENUE: ["Function hall booking, year-end party", "Event venue reservation", "Venue hire for the company party"],
    CATERING: ["Party catering trays", "Catering for team party", "Buffet trays for the party"],
    PRIZES: ["Raffle prize cash voucher", "Party raffle prize", "Prize voucher for the raffle draw"],
    STAGE: ["Stage and truss hire, launch event", "LED wall and stage rental", "Event stage setup and hire"],
}
TERSE = {CATERING: ["Catering", "Food trays"], PRIZES: ["Raffle prize", "Prize"]}
DEPOSIT_PRICE, BALANCE_PRICE = "30000.00", "60000.00"
SPECS = {
    # One deposit_balance item: point 0 the deposit figure, point 1 the balance figure.
    VENUE: dict(
        points=[(3_000_000, "venue-1"), (6_000_000, "venue-1")],
        quantities=[(1, 3), (2, 1)],
        price_class="big_ticket",
        archetype="deposit_balance",
        params={"per_quarter": 2.0, "offset_days": [21, 35]},
    ),
    # Party items: project_burst items of a storyline with `parties_per_quarter`.
    CATERING: dict(
        points=[(185_000, "cater-1"), (212_500, "cater-2")],
        quantities=[(1, 3), (2, 2), (3, 1)],
        archetype="project_burst",
        params={"per_burst": 2.5},
    ),
    PRIZES: dict(
        points=[(100_000, "prize-1"), (500_000, "prize-2")],
        quantities=[(1, 4), (2, 1)],
        archetype="project_burst",
        params={"per_burst": 2.0},
    ),
    STAGE: dict(
        points=[(9_000_000, "stage-1")],
        quantities=[(1, 3), (2, 1)],
        price_class="big_ticket",
        archetype="one_off_big_ticket",
        params={"per_quarter": 1.5},
    ),
}


def add_events(files, *, storyline=None, only=None, **overrides):
    """Add an `events` party storyline: a venue deposit/balance item, two party items and a one-off stage hire.

    `only` limits the items added; `overrides` maps an item id (dots as `__`) to
    a dict merged into its catalog entry (`params` merged key by key).
    """
    files["storylines"]["events"] = dict(EVENTS, **(storyline or {}))
    for item_id, spec in SPECS.items():
        if only is not None and item_id not in only:
            continue
        spec = dict(spec, params=dict(spec["params"]))
        add_item(
            files,
            item_id,
            storyline="events",
            category="Events",
            descriptive=TEXT[item_id],
            terse=TERSE.get(item_id, []),
            **spec,
        )
    if PRIZES in files["catalog"]["items"]:
        files["catalog"]["items"][PRIZES]["round_figures"] = True
    for key, extra in overrides.items():
        entry = files["catalog"]["items"][key.replace("__", ".")]
        extra = dict(extra)
        entry["params"] = dict(entry["params"], **extra.pop("params", {}))
        entry.update(extra)


def events(**kw):
    return lambda files: add_events(files, **kw)


def text_index(files):
    index = {}
    for item_id, variants in files["text"].items():
        for t in variants["descriptive"] + [v["text"] for v in variants.get("vendor", [])] + variants["terse"]:
            index[t] = item_id
    return index


def centavos(row):
    return int(Decimal(row["unit_price"]) * 100)


def amount(row):
    return int(Decimal(row["qty"]) * Decimal(row["unit_price"]) * 100)


def day(row):
    return date.fromisoformat(row["date_of_transaction"])


def metric(run_json, name):
    for c in run_json["scorecard"]["checks"]:
        if c["name"] == name:
            return c
    raise AssertionError(f"no scorecard metric {name}")


class EventsCase(unittest.TestCase):
    mutate = staticmethod(events())
    config = FULL_YEAR

    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_bundle(mutate=self.mutate)
        self.files = load_fixture_files()
        self.mutate(self.files)
        self.index = text_index(self.files)
        self.ws.write_config(self.config)

    def run_seed(self, seed):
        r = self.ws.run("generate", "--seed", str(seed))
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        return r

    def by_item(self, r):
        out = defaultdict(list)
        for row in r.rows:
            out[self.index[row["item/service"]]].append(row)
        return out

    def latest(self, label="fixture"):
        return next(p.name for p in sorted((self.ws.cwd / "bundles").iterdir()) if p.name.startswith(label + "-"))

    def events_bundle(self):
        return store.load(self.ws.bundle_dir(self.latest()))


def invalid(test, mutate, message):
    ws = Workspace(test)
    ws.install_bundle(mutate=mutate)
    r = ws.run("generate", "--seed", "1")
    test.assertEqual(r.code, 4, r.stdout + r.stderr)
    test.assertIn(message, r.stderr)
    test.assertEqual(ws.all_csvs(), [])


def match(deposits, balances, lo, hi):
    """Pair each balance, in date order, with the earliest free deposit lo..hi days before it (None if any is left)."""
    free = sorted(deposits)
    pairs = []
    for b in sorted(balances):
        d = next((d for d in free if lo <= (b - d).days <= hi), None)
        if d is None:
            return None
        free.remove(d)
        pairs.append((d, b))
    return pairs if not free else None


def csv_text(*rows):
    """CSV text from (date, qty, unit_price, text) tuples."""
    return "date_of_transaction,qty,unit_price,item/service\n" + "".join(f'{d},{q},{p},"{t}"\n' for d, q, p, t in rows)


class DepositBalanceTest(EventsCase):  # FR-E4, T24
    def split(self, rows):
        deposits = [x for x in rows if x["unit_price"] == DEPOSIT_PRICE]
        balances = [x for x in rows if x["unit_price"] == BALANCE_PRICE]
        self.assertEqual(len(deposits) + len(balances), len(rows))
        return deposits, balances

    def test_every_balance_follows_its_deposit_by_the_offset(self):
        pairs = 0
        for seed in range(6):
            r = self.run_seed(seed)
            deposits, balances = self.split(self.by_item(r)[VENUE])
            matched = match([day(x) for x in deposits], [day(x) for x in balances], 21, 35)
            self.assertIsNotNone(matched, f"seed {seed}: deposits {deposits} balances {balances}")
            pairs += len(matched)
            for d, b in matched:
                self.assertLess(d, b)
            # Settled at the scope it was booked at: the pair shares its quantity.
            self.assertEqual(Counter(x["qty"] for x in deposits), Counter(x["qty"] for x in balances))
            self.assertEqual(metric(r.run_json, "deposit_offsets")["status"], "pass")
        self.assertGreater(pairs, 20)

    def test_offset_range_is_bundle_data(self):
        def mutate(files):
            add_events(files)
            del files["catalog"]["items"][VENUE]["params"]["offset_days"]
            files["rules"]["archetypes"]["deposit_balance"] = {"offset_days": [7, 9]}

        self.ws.install_bundle(label="offset", mutate=mutate)
        pairs = 0
        for seed in range(3):
            r = self.ws.run("generate", "--seed", str(seed), "--bundle", self.latest("offset"))
            self.assertEqual(r.code, 0, r.stderr)
            deposits, balances = self.split(self.by_item(r)[VENUE])
            matched = match([day(x) for x in deposits], [day(x) for x in balances], 7, 9)
            self.assertIsNotNone(matched)
            pairs += len(matched)
            self.assertEqual(metric(r.run_json, "deposit_offsets")["status"], "pass")
        self.assertGreater(pairs, 5)

    def test_unpaired_rows_warn(self):
        text = csv_text(
            ("2026-03-02", 1, DEPOSIT_PRICE, TEXT[VENUE][0]),
            ("2026-03-10", 1, BALANCE_PRICE, TEXT[VENUE][1]),  # 8 days on: too soon
            ("2026-05-04", 1, DEPOSIT_PRICE, TEXT[VENUE][1]),
            ("2026-05-29", 1, BALANCE_PRICE, TEXT[VENUE][0]),  # 25 days on: paired
            ("2026-08-03", 1, BALANCE_PRICE, TEXT[VENUE][2]),  # no deposit at all
        )
        got = score_csv(text, self.events_bundle()).get("deposit_offsets")
        self.assertEqual(got.status, "warn")
        self.assertEqual(got.value, {VENUE: 3})
        self.assertIn("events.venue balance 2026-08-03: no deposit 21-35 days before", got.detail)

    def test_bad_pairs_are_rejected(self):
        def three_points(files):
            add_events(files)
            files["rate_cards"][VENUE]["points"].append({"unit_price": 1_000_000, "seller": "venue-1"})

        cases = [
            (three_points, "a big_ticket item has at most 2 price point(s), got 3"),
            (events(events__venue={"params": {"offset_days": [0, 5]}}), "`offset_days` must be [min, max]"),
            (events(events__venue={"params": {"offset_days": [10, 5]}}), "`offset_days` must be [min, max]"),
            (events(events__venue={"params": {"offset_days": 14}}), "`offset_days` must be [min, max]"),
            (events(events__venue={"params": {"per_quarter": -1}}), "param `per_quarter` must be a number >= 0"),
        ]
        for mutate, message in cases:
            with self.subTest(message=message):
                invalid(self, mutate, message)


class PartyDayTest(EventsCase):  # FR-E5, T21
    def party_days(self, rows):
        party = (CATERING, PRIZES)
        return Counter((self.index[r["item/service"]], day(r)) for r in rows if self.index[r["item/service"]] in party)

    def test_several_rows_of_one_item_on_a_party_day_up_to_the_cap(self):
        most = Counter()
        for seed in range(5):
            r = self.run_seed(seed)
            per_day = self.party_days(r.rows)
            for (item_id, _), n in per_day.items():
                most[item_id] = max(most[item_id], n)
                self.assertLessEqual(n, 4)
            # Party days are the storyline's: both items share them, a week or more apart.
            days = sorted({d for _, d in per_day})
            self.assertTrue(all((b - a).days >= 7 for a, b in zip(days, days[1:])), days)
            # Every other item keeps one row a day (a same-amount batch-entry duplicate aside).
            others = defaultdict(set)
            for row in r.rows:
                item_id = self.index[row["item/service"]]
                if item_id not in (CATERING, PRIZES):
                    others[(item_id, row["date_of_transaction"])].add(amount(row))
            self.assertEqual(max(map(len, others.values())), 1)
            hard = {c["name"]: c["status"] for c in r.run_json["scorecard"]["checks"] if c["hard"]}
            self.assertEqual(set(hard.values()), {"pass"}, hard)
        self.assertEqual(most, Counter({CATERING: 4, PRIZES: 4}))

    def test_cap_is_the_items_gap_rule(self):
        self.ws.install_bundle(label="capped", mutate=events(events__catering={"params": {"max_per_day": 2}}))
        most = Counter()
        for seed in range(3):
            r = self.ws.run("generate", "--seed", str(seed), "--bundle", self.latest("capped"))
            self.assertEqual(r.code, 0, r.stderr)
            for (item_id, _), n in self.party_days(r.rows).items():
                most[item_id] = max(most[item_id], n)
        self.assertEqual(most, Counter({CATERING: 2, PRIZES: 4}))

    def test_rescoring_the_csv_treats_party_rows_as_the_party(self):
        r = self.run_seed(2)
        report = score_csv(r.csv_bytes, self.events_bundle(), tolerance_pct=25).as_dict()["checks"]
        got = {c["name"]: c for c in report}
        for want in r.run_json["scorecard"]["checks"]:
            if want["name"] in ("gap_rules", "duplicates", "plug_rows", "min_gap", "deposit_offsets"):
                self.assertEqual(got[want["name"]], want)

    def test_gap_check_allows_the_party_cap_and_nothing_more(self):
        bundle = self.events_bundle()
        party = [("2026-12-18", q, "1850.00", TEXT[CATERING][0]) for q in (1, 2, 1, 1)]
        ok = score_csv(csv_text(*party), bundle)
        self.assertEqual(ok.get("gap_rules").status, "pass")
        self.assertEqual(ok.get("duplicates").status, "pass")  # same-amount rows of a party are the party
        over = score_csv(csv_text(*party, ("2026-12-18", 2, "2125.00", TEXT[CATERING][1])), bundle)
        self.assertEqual(over.get("gap_rules").status, "fail")
        self.assertIn("events.catering: 5 rows on 2026-12-18 (max 4)", over.get("gap_rules").detail)
        coffee = self.files["text"]["pantry.coffee"]["descriptive"][0]
        price = "%.2f" % (self.files["rate_cards"]["pantry.coffee"]["points"][0]["unit_price"] / 100)
        two = score_csv(csv_text(("2026-12-18", 1, price, coffee), ("2026-12-18", 2, price, coffee)), bundle)
        self.assertEqual(two.get("gap_rules").status, "fail")

    def test_only_party_and_batch_items_may_have_more_than_one_row_a_day(self):
        def petty_param(files):
            files["catalog"]["items"]["pantry.coffee"]["params"]["max_per_day"] = 2

        def petty_rule(files):
            files["rules"]["archetypes"]["petty_daily"]["max_per_day"] = 3

        for mutate, message in (
            (petty_param, "item `pantry.coffee`: `max_per_day` 2: only batch_logged items and party items"),
            (petty_rule, "`max_per_day` 3: only batch_logged items and party items"),
            (events(events__catering={"params": {"max_per_day": 21}}), "`max_per_day` must be at most 20"),
            (events(storyline={"parties_per_quarter": -1}), "`parties_per_quarter` must be a number >= 0"),
        ):
            with self.subTest(message=message):
                invalid(self, mutate, message)

    def test_seasonal_party_storyline_parties_in_its_season(self):  # FR-E10
        season = {str(m): 0 for m in range(1, 10)}
        self.ws.install_bundle(label="season", mutate=events(storyline={"month_weights": season}))
        months = Counter()
        for seed in range(3):
            r = self.ws.run("generate", "--seed", str(seed), "--bundle", self.latest("season"))
            self.assertEqual(r.code, 0, r.stderr)
            months.update(d.month for _, d in self.party_days(r.rows))
        self.assertTrue(months)
        self.assertLessEqual(set(months), {10, 11, 12})


class BigTicketTest(EventsCase):  # FR-E6, FR-F1, T23
    BIG = (VENUE, STAGE)

    def test_big_tickets_fall_on_weekdays_at_their_negotiated_figure(self):
        stages = 0
        for seed in range(6):
            r = self.run_seed(seed)
            by = self.by_item(r)
            for item_id in self.BIG:
                for row in by[item_id]:
                    self.assertLess(day(row).weekday(), 5, row)
            self.assertLessEqual({x["unit_price"] for x in by[STAGE]}, {"90000.00"})
            days = sorted(day(x) for x in by[STAGE])
            self.assertTrue(all((b - a).days >= 30 for a, b in zip(days, days[1:])), days)
            stages += len(days)
            self.assertEqual(metric(r.run_json, "weekday_shares.big_ticket_weekend")["status"], "pass")
        self.assertGreater(stages, 12)

    def test_big_ticket_party_items_skip_weekend_parties(self):
        def mutate(files):
            add_events(files, events__prizes={"class": "big_ticket"})
            files["rate_cards"][PRIZES]["points"] = files["rate_cards"][PRIZES]["points"][:1]

        self.ws.install_bundle(label="bigprize", mutate=mutate)
        weekend = Counter()
        prizes = 0
        for seed in range(4):
            r = self.ws.run("generate", "--seed", str(seed), "--bundle", self.latest("bigprize"))
            self.assertEqual(r.code, 0, r.stderr)
            for row in r.rows:
                item_id = self.index[row["item/service"]]
                weekend[item_id] += day(row).weekday() >= 5
                prizes += item_id == PRIZES
        self.assertGreater(prizes, 10)
        self.assertEqual(weekend[PRIZES], 0)
        self.assertGreater(weekend[CATERING], 0, "retail party items still party on weekends")


class RoundFiguresTest(EventsCase):  # FR-F6, T18
    APPROVED = {VENUE, STAGE, PRIZES}

    def test_round_thousands_only_on_approved_items(self):
        seen = Counter()
        for seed in range(6):
            r = self.run_seed(seed)
            for row in r.rows:
                if amount(row) % 100_000 == 0:
                    item_id = self.index[row["item/service"]]
                    self.assertIn(item_id, self.APPROVED, row)
                    seen[item_id] += 1
            self.assertEqual(metric(r.run_json, "plug_rows")["status"], "pass")
        self.assertEqual(set(seen), self.APPROVED)

    def test_without_approval_a_round_figure_is_a_plug_row(self):
        for key in ("events__stage", "events__prizes"):
            with self.subTest(item=key):
                ws = Workspace(self)
                ws.install_bundle(mutate=events(**{key: {"round_figures": False}}))
                ws.write_config(FULL_YEAR)
                r = ws.run("generate", "--seed", "1")
                self.assertEqual(r.code, 1, r.stdout + r.stderr)
                self.assertIsNotNone(r.csv_path)
                self.assertEqual(metric(r.run_json, "plug_rows")["status"], "fail")

    def test_approval_is_limited_to_round_figure_event_items(self):
        def coffee(files):
            files["catalog"]["items"]["pantry.coffee"]["round_figures"] = True

        for mutate, message in (
            (coffee, "item `pantry.coffee`: `round_figures` is only for big-ticket items and event items"),
            (events(events__catering={"round_figures": True}), "`round_figures` needs a round-figure rate card"),
            (events(events__prizes={"round_figures": "yes"}), "`round_figures` must be true or false"),
        ):
            with self.subTest(message=message):
                invalid(self, mutate, message)


class ScopeTest(unittest.TestCase):  # FR-G2 step 4, FR-E12, T11
    PANELS = "events.led_panels"
    BIG = (VENUE, PANELS)
    RETAIL_MAX = 210_000  # ₱: the fixture's petty items every day at their largest quantities (about ₱202k a quarter)

    @classmethod
    def add_scope(cls, files):
        """The venue pair plus a one-off big ticket with a unit count: LED wall panels at ₱5,000 each."""
        add_events(files, only=[VENUE])
        add_item(
            files,
            cls.PANELS,
            storyline="events",
            category="Events",
            points=[(500_000, "stage-1")],
            quantities=[(4, 4), (6, 3), (8, 2), (10, 1), (12, 1)],
            descriptive=["LED wall panel hire, launch event", "LED video wall panels, per panel", "Rental of LED wall panels"],
            price_class="big_ticket",
            archetype="one_off_big_ticket",
            params={"per_quarter": 3.0},
        )

    def setUp(self):
        self.ws = Workspace(self)
        self.files = load_fixture_files()

    def install(self, mutate):
        self.ws.install_bundle(mutate=mutate)
        mutate(self.files)
        self.index = text_index(self.files)

    def generate(self, seed, target=None, period=""):
        if target is None:
            self.ws.write_config(period)
        else:
            self.ws.write_config(period + f"target = {target}\n", fixture_defaults=False)
        r = self.ws.run("generate", "--seed", str(seed))
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        if target is not None:
            self.assertTrue(target * 100 <= r.run_json["total_centavos"] <= target * 102, r.run_json["total"])
        hard = {c["name"]: c["status"] for c in r.run_json["scorecard"]["checks"] if c["hard"]}
        self.assertEqual(set(hard.values()), {"pass"}, hard)
        return r

    def big_rows(self, r):
        rows = [x for x in r.rows if self.index[x["item/service"]] in self.BIG]
        return sorted((self.index[x["item/service"]], x["date_of_transaction"], x["qty"], x["unit_price"]) for x in rows)

    def test_big_ticket_scope_is_the_last_lever(self):
        self.install(self.add_scope)
        largest = {i: max(q["qty"] for q in c["quantities"]) for i, c in self.files["rate_cards"].items()}
        grown = 0
        for seed in range(6):
            base = self.big_rows(self.generate(seed))
            spend = sum(int(Decimal(q) * Decimal(p)) for _, _, q, p in base)
            headroom = sum(largest[i] * int(Decimal(p)) for i, _, _, p in base) - spend
            if headroom < 40_000:
                continue
            # Retail occurrences and quantities move first: big-ticket rows stay as planned.
            self.assertEqual(self.big_rows(self.generate(seed, spend + 150_000)), base)
            # Past every petty item every day at its largest quantity, scope grows: the same
            # big-ticket purchases on the same days at the same prices, more units.
            late = self.generate(seed, spend + self.RETAIL_MAX + headroom // 2)
            scoped = self.big_rows(late)
            self.assertEqual([(i, d, p) for i, d, _, p in scoped], [(i, d, p) for i, d, _, p in base])
            self.assertGreater(sum(int(q) for _, _, q, _ in scoped), sum(int(q) for _, _, q, _ in base))
            retail = [x for x in late.rows if self.index[x["item/service"]] not in self.BIG]
            self.assertEqual({int(x["qty"]) == largest[self.index[x["item/service"]]] for x in retail}, {True})
            grown += 1
        self.assertGreaterEqual(grown, 3)

    def test_totals_stay_in_band_across_20_seeds(self):  # T11 with events
        # A year, so every seed's events (up to about ₱1.8M of big tickets) fit under the target.
        self.install(events())
        totals = {self.generate(seed, 2_500_000, FULL_YEAR).run_json["total_centavos"] for seed in range(20)}
        self.assertGreater(len(totals), 10)


if __name__ == "__main__":
    unittest.main()
