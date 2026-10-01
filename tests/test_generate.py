"""`txns generate` walking skeleton, driven through main(argv) (ticket 02: T1-T9, T26)."""

import csv
import io
import json
import re
import socket
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from tests.helpers import FIXTURE_CONFIG, Workspace, add_item, load_fixture_files

CSV_NAME = re.compile(r"^txns-2026Q3-[0-9a-f]{6}\.csv$")


def text_to_item(files):
    index = {}
    for item_id, variants in files["text"].items():
        vendor = [v["text"] for v in variants.get("vendor", [])]
        for t in variants["descriptive"] + vendor + variants["terse"]:
            assert t not in index, "fixture texts must be unique per item for these tests"
            index[t] = item_id
    return index


class DeterminismTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_bundle()

    def test_same_inputs_give_byte_identical_csv_and_run_json(self):  # T1
        a = self.ws.run("generate", "--seed", "42")
        a_csv, a_run = a.csv_bytes, a.run_bytes
        b = self.ws.run("generate", "--seed", "42")
        self.assertEqual((a.code, b.code), (0, 0))
        self.assertEqual(a.csv_path, b.csv_path)
        self.assertEqual(b.csv_bytes, a_csv)
        self.assertEqual(b.run_bytes, a_run)
        self.assertGreater(len(a.rows), 50)

    def test_out_is_not_part_of_run_identity(self):  # T2
        self.ws.write_config('out = "first"\n')
        a = self.ws.run("generate", "--seed", "42")
        self.ws.write_config('out = "second/nested"\n')
        b = self.ws.run("generate", "--seed", "42")
        self.assertEqual(a.run_json["run_id"], b.run_json["run_id"])
        self.assertEqual(a.csv_path.name, b.csv_path.name)
        self.assertNotEqual(a.csv_path, b.csv_path)
        self.assertEqual(a.csv_bytes, b.csv_bytes)
        self.assertEqual(b.csv_path.parent, self.ws.cwd / "second" / "nested")

    def test_every_other_key_seed_and_bundle_change_the_run_id(self):  # T3
        base = self.ws.run("generate", "--seed", "42").run_json["run_id"]
        variants = [
            'start = "2026-07-02"\nend = "2026-09-30"\n',
            'start = "2026-07-01"\nend = "2026-09-29"\n',
            "target = 2\n",
            "band_pct = 2_000_000_000_000\n",
            "target_rows = 100\n",
            'tier = "high"\n',
            "tolerance_pct = 30\n",
            "[multipliers.class]\nretail = 1.5\n",
            "[multipliers.storyline]\nerrands = 2.0\n",
        ]
        seen = {base}
        for text in variants:
            self.ws.write_config(text)
            r = self.ws.run("generate", "--seed", "42")
            self.assertEqual(r.code, 0, r.stderr)
            self.assertNotIn(r.run_json["run_id"], seen, text)
            seen.add(r.run_json["run_id"])
        self.ws.write_config("")
        self.assertNotIn(self.ws.run("generate", "--seed", "43").run_json["run_id"], seen)
        other = self.ws.install_bundle(
            label="other", mutate=lambda f: f["storylines"]["errands"].update(description="changed")
        )
        r = self.ws.run("generate", "--seed", "42", "--bundle", other)
        self.assertEqual(r.run_json["bundle"]["id"], other)
        self.assertNotIn(r.run_json["run_id"], seen)

    def test_committed_txns_toml_holds_the_defaults(self):
        base = self.ws.run("generate", "--seed", "42").run_json["run_id"]
        repo_config = Path(__file__).resolve().parent.parent / "txns.toml"
        text = repo_config.read_text(encoding="utf-8")
        # Only the ₱4M target and its band are swapped for the fixture's (FIXTURE_CONFIG).
        for key, value in FIXTURE_CONFIG.items():
            text, n = re.subn(rf"^{key} = \S+", f"{key} = {value}", text, flags=re.MULTILINE)
            self.assertEqual(n, 1, key)
        self.ws.write_config(text)
        self.assertEqual(self.ws.run("generate", "--seed", "42").run_json["run_id"], base)

    def test_explicit_values_equal_to_defaults_keep_the_run_id(self):
        base = self.ws.run("generate", "--seed", "42").run_json["run_id"]
        self.ws.write_config(
            'bundle = "latest"\nstart = "auto"\nend = "auto"\ntarget = 1\nband_pct = 1e12\n'
            'tier = "mid"\ntolerance_pct = 25\nseed = ""\n[multipliers.class]\nretail = 1\n'
        )
        self.assertEqual(self.ws.run("generate", "--seed", "42").run_json["run_id"], base)

    def test_drawn_seed_is_printed_recorded_and_reproduces_the_csv(self):  # T4
        a = self.ws.run("generate")
        self.assertEqual(a.code, 0, a.stderr)
        seed = a.run_json["seed"]
        self.assertIsInstance(seed, int)
        self.assertIn(f"seed: {seed}", a.stdout)
        a_csv = a.csv_bytes
        a.csv_path.unlink()
        b = self.ws.run("generate", "--seed", str(seed))
        self.assertEqual(b.csv_bytes, a_csv)
        self.assertEqual(b.run_json["run_id"], a.run_json["run_id"])

    def test_config_seed_is_used_and_flag_overrides_it(self):
        self.ws.write_config("seed = 42\n")
        from_config = self.ws.run("generate")
        self.assertEqual(from_config.run_json["seed"], 42)
        self.assertNotIn("drawn", from_config.stdout)
        flag = self.ws.run("generate", "--seed", "43")
        self.assertEqual(flag.run_json["seed"], 43)

    def test_adding_a_storyline_leaves_other_storylines_rows_unchanged(self):  # T5
        base = self.ws.run("generate", "--seed", "42")

        def add_transport(files):
            add_item(
                files,
                "transport.taxi",
                storyline="transport",
                points=[(25000, "taxi-1"), (32000, "taxi-2")],
                quantities=[(1, 1)],
                descriptive=["Taxi fare to color grading session"],
                terse=["Taxi fare"],
                params={"per_week": 4.0},
            )

        ws2 = Workspace(self)
        ws2.install_bundle(mutate=add_transport)
        grown = ws2.run("generate", "--seed", "42")
        self.assertEqual(grown.code, 0, grown.stderr)
        new_texts = {"Taxi fare to color grading session", "Taxi fare"}
        kept = [r for r in grown.rows if r["item/service"] not in new_texts]
        # Same rows. Entry order interleaves storylines, and the one row entered late when a
        # file would otherwise be perfectly date-sorted (FR-H4) depends on every row, so
        # the comparison ignores order.
        key = lambda r: tuple(r.values())
        self.assertEqual(sorted(kept, key=key), sorted(base.rows, key=key))
        self.assertGreater(len(grown.rows), len(base.rows))

    def test_generate_runs_offline_without_api_key_or_ledgers(self):  # T6
        def refuse(*a, **k):
            raise AssertionError("network access attempted")

        self.assertFalse((self.ws.cwd / "inputs").exists())
        with mock.patch.object(socket, "socket", refuse), mock.patch.object(
            socket, "create_connection", refuse
        ), mock.patch.object(socket, "getaddrinfo", refuse):
            r = self.ws.run("generate", "--seed", "1", env={})
        self.assertEqual(r.code, 0, r.stderr)
        self.assertTrue(r.csv_path.exists())


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_bundle()

    def test_auto_period_is_latest_full_quarter_before_today(self):  # T7
        self.ws.write_config('start = "auto"\nend = "auto"\n')
        q3 = self.ws.run("generate", "--seed", "5", today=date(2026, 10, 5))
        self.assertEqual((q3.run_json["period"]["start"], q3.run_json["period"]["end"]), ("2026-07-01", "2026-09-30"))
        self.assertTrue(q3.csv_path.name.startswith("txns-2026Q3-"))
        dates = {r["date_of_transaction"] for r in q3.rows}
        self.assertTrue(all("2026-07-01" <= d <= "2026-09-30" for d in dates))
        self.assertEqual(min(dates)[:7], "2026-07")
        self.assertEqual(max(dates)[:7], "2026-09")

        q4 = self.ws.run("generate", "--seed", "5", today=date(2027, 1, 5))
        self.assertEqual((q4.run_json["period"]["start"], q4.run_json["period"]["end"]), ("2026-10-01", "2026-12-31"))
        self.assertTrue(q4.csv_path.name.startswith("txns-2026Q4-"))
        self.assertTrue(all("2026-10-01" <= r["date_of_transaction"] <= "2026-12-31" for r in q4.rows))
        self.assertNotEqual(q3.run_json["run_id"], q4.run_json["run_id"])

    def test_auto_period_edges(self):
        cases = {
            date(2026, 9, 30): ("2026-04-01", "2026-06-30"),  # quarter not yet over
            date(2026, 1, 1): ("2025-10-01", "2025-12-31"),
            date(2026, 4, 1): ("2026-01-01", "2026-03-31"),
        }
        for today, expected in cases.items():
            p = self.ws.run("generate", "--seed", "5", today=today).run_json["period"]
            self.assertEqual((p["start"], p["end"]), expected, today)

    def test_explicit_dates_are_inclusive(self):
        self.ws.write_config("start = 2026-02-01\nend = 2026-02-28\n")
        r = self.ws.run("generate", "--seed", "5")
        self.assertEqual(r.code, 0, r.stderr)
        self.assertTrue(r.csv_path.name.startswith("txns-20260201-20260228-"))
        self.assertFalse(r.run_json["period"]["auto"])
        self.assertTrue(all("2026-02-01" <= x["date_of_transaction"] <= "2026-02-28" for x in r.rows))

    def test_every_generate_key_is_recorded_in_run_json(self):
        self.ws.write_config(
            'target = 50000\nband_pct = 3\ntarget_rows = 150\ntier = "high"\ntolerance_pct = 30\n'
            'out = "runs"\n[multipliers.class]\nbig_ticket = 2.0\n[multipliers.storyline]\nerrands = 0.5\n'
            '[author]\nmodel = "vendor/some-model"\nmax_cost_usd = 5\nledgers_dir = "inputs/ledgers"\n'
        )
        r = self.ws.run("generate", "--seed", "9")
        self.assertEqual(r.code, 0, r.stderr)
        c = r.run_json["config"]
        self.assertEqual(
            {k: c[k] for k in ("target", "band_pct", "target_rows", "tier", "tolerance_pct", "out", "seed")},
            {"target": 50000, "band_pct": 3, "target_rows": 150, "tier": "high", "tolerance_pct": 30, "out": "runs", "seed": 9},
        )
        self.assertEqual(c["multipliers"]["class"], {"big_ticket": 2.0, "retail": 1.0, "subscription": 1.0})
        self.assertEqual(c["multipliers"]["storyline"], {"errands": 0.5, "office_pantry": 1.0})
        self.assertEqual(c["bundle"], r.run_json["bundle"]["id"])
        self.assertNotIn("OPENROUTER", json.dumps(r.run_json))

    def test_invalid_config_exits_2_without_csv(self):
        bad = [
            'tier = "huge"\n',
            "colour = 1\n",
            "target = -5\n",
            "target = 1.5\n",
            "band_pct = -1\n",
            'start = "yesterday"\n',
            "start = 2026-07-01T00:00:00\n",  # a TOML datetime is not a date
            "end = 2026-09-30T10:00:00+08:00\n",
            'seed = "²"\n',  # a digit to str.isdigit, not to int()
            'start = "2026-09-01"\nend = "2026-08-01"\n',
            "seed = -1\n",
            "[multipliers.class]\nluxury = 2.0\n",
            "[multipliers.storyline]\nerrands = 0\n",
            "[author]\nfoo = 1\n",
            "this is not toml",
        ]
        for text in bad:
            self.ws.write_config(text)
            r = self.ws.run("generate", "--seed", "1")
            self.assertEqual(r.code, 2, text)
            self.assertIn("error:", r.stderr)
        self.assertEqual(self.ws.all_csvs(), [])

    def test_bad_flags_and_missing_explicit_config_exit_2(self):
        self.assertEqual(self.ws.run("generate", "--seed", "abc").code, 2)
        self.assertEqual(self.ws.run("generate", "--seed", "²").code, 2)
        self.assertEqual(self.ws.run("generate", "--seed", "1_000").code, 2)
        self.assertEqual(self.ws.run("generate", "--config", "nope.toml").code, 2)
        self.assertEqual(self.ws.run("generate", "--frobnicate").code, 2)
        self.assertEqual(self.ws.run().code, 2)
        self.assertEqual(self.ws.all_csvs(), [])

    def test_config_path_flag(self):
        self.ws.write_config('out = "elsewhere"\nseed = 3\n', name="custom.toml")
        r = self.ws.run("generate", "--config", "custom.toml")
        self.assertEqual(r.code, 0, r.stderr)
        self.assertEqual(r.csv_path.parent, self.ws.cwd / "elsewhere")
        self.assertEqual(r.run_json["seed"], 3)

    def test_unknown_storyline_multiplier_warns(self):
        self.ws.write_config("[multipliers.storyline]\nfestival = 2.0\n")
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0, r.stderr)
        self.assertIn("festival", r.stderr)


class BundleLookupTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)

    def test_no_bundle_exits_2_without_csv(self):  # T8
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 2)
        self.assertIn("no bundle", r.stderr)
        (self.ws.cwd / "bundles").mkdir()
        self.assertEqual(self.ws.run("generate", "--seed", "1").code, 2)
        self.assertEqual(self.ws.all_csvs(), [])

    def test_unknown_bundle_exits_2_without_csv(self):  # T8
        self.ws.install_bundle()
        r = self.ws.run("generate", "--seed", "1", "--bundle", "fixture-000000000000")
        self.assertEqual(r.code, 2)
        self.assertIn("unknown bundle", r.stderr)
        self.ws.write_config('bundle = "nope-123"\n')
        self.assertEqual(self.ws.run("generate", "--seed", "1").code, 2)
        self.assertEqual(self.ws.all_csvs(), [])

    def test_latest_is_most_recently_promoted_and_bundle_flag_pins_one(self):
        # "zzz" sorts after "aaa" by name, but "aaa" is promoted later.
        first = self.ws.install_bundle(label="zzz")
        second = self.ws.install_bundle(label="aaa", mutate=lambda f: f["storylines"]["errands"].update(description="v2"))
        self.assertEqual(self.ws.run("generate", "--seed", "1").run_json["bundle"]["id"], second)
        self.assertEqual(self.ws.run("generate", "--seed", "1", "--bundle", first).run_json["bundle"]["id"], first)
        self.ws.write_config(f'bundle = "{first}"\n')
        self.assertEqual(self.ws.run("generate", "--seed", "1").run_json["bundle"]["id"], first)
        self.assertEqual(self.ws.run("generate", "--seed", "1", "--bundle", second).run_json["bundle"]["id"], second)

    def test_bundle_edited_in_place_fails_hash_check(self):
        name = self.ws.install_bundle()
        rc = self.ws.bundle_dir(name) / "rate_cards.json"
        rc.write_text(rc.read_text(encoding="utf-8").replace("16500", "16600"), encoding="utf-8")
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 4)
        self.assertIn("hash", r.stderr)
        self.assertEqual(self.ws.all_csvs(), [])

    def test_reviewed_flag_is_not_part_of_the_hash(self):
        name = self.ws.install_bundle()
        m = self.ws.bundle_dir(name) / "manifest.json"
        data = json.loads(m.read_text(encoding="utf-8"))
        data["reviewed"] = True
        m.write_text(json.dumps(data), encoding="utf-8")
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0, r.stderr)
        self.assertTrue(r.run_json["bundle"]["reviewed"])

    def test_bundle_with_unknown_archetype_exits_4(self):
        self.ws.install_bundle(mutate=lambda f: f["catalog"]["items"]["pantry.coffee"].update(archetype="teleport"))
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 4)
        self.assertIn("teleport", r.stderr)
        self.assertEqual(self.ws.all_csvs(), [])

    def test_unreviewed_bundle_warns_and_run_completes(self):  # T9
        self.ws.install_bundle(reviewed=False)
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0)
        self.assertRegex(r.stderr, r"warning: bundle .* unreviewed")
        self.assertFalse(r.run_json["bundle"]["reviewed"])
        self.assertTrue(r.csv_path.exists())

    def test_reviewed_bundle_with_same_interpreter_does_not_warn(self):
        self.ws.install_bundle(reviewed=True)
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0)
        self.assertEqual(r.stderr, "")
        self.assertTrue(r.run_json["bundle"]["reviewed"])

    def test_interpreter_mismatch_warns(self):  # T9
        self.ws.install_bundle(reviewed=True, interpreter="CPython 3.12.0-other")
        r = self.ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 0)
        self.assertIn("warning: interpreter", r.stderr)
        self.assertEqual(r.run_json["bundle"]["interpreter"], "CPython 3.12.0-other")


class OutputFormatTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)
        self.ws.install_bundle()
        self.files = load_fixture_files()
        self.result = self.ws.run("generate", "--seed", "2026")
        self.assertEqual(self.result.code, 0, self.result.stderr)

    def test_csv_bytes_follow_the_format_rules(self):  # T26
        data = self.result.csv_bytes
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "no BOM")
        self.assertNotIn(b"\r", data)
        self.assertTrue(data.endswith(b"\n"))
        text = data.decode("utf-8")
        self.assertTrue(text.startswith("date_of_transaction,qty,unit_price,item/service\n"))
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        self.assertEqual(records[0], ["date_of_transaction", "qty", "unit_price", "item/service"])
        for rec in records[1:]:
            self.assertEqual(len(rec), 4, rec)
            d, qty, price, item = rec
            self.assertEqual(date.fromisoformat(d).isoformat(), d)
            self.assertRegex(qty, r"^[1-9][0-9]*$")
            self.assertRegex(price, r"^[1-9][0-9]*\.[0-9]{2}$|^0\.(0[1-9]|[1-9][0-9])$")
            self.assertTrue(0 < len(item) <= 100)
            self.assertEqual(item, item.strip())
            self.assertTrue(all(" " <= ch <= "~" or ch == "₱" for ch in item), item)
        # RFC 4180 quoting of a field with a comma, and only when needed.
        self.assertRegex(text, r'\n\d{4}-\d\d-\d\d,\d+,[\d.]+,"[^"\n]*,[^"\n]*"\n')
        self.assertNotRegex(text, r'\n\d{4}-\d\d-\d\d,\d+,[\d.]+,"[^,"\n]*"\n')

    def test_rows_use_rate_card_prices_allowed_quantities_one_per_item_per_day(self):
        index = text_to_item(self.files)
        seen = {}
        for row in self.result.rows:
            item = index[row["item/service"]]
            card = self.files["rate_cards"][item]
            centavos = int(Decimal(row["unit_price"]) * 100)
            self.assertIn(centavos, [p["unit_price"] for p in card["points"]])
            self.assertIn(int(row["qty"]), [q["qty"] for q in card["quantities"] if q["weight"] > 0])
            # A same-day, same-amount duplicate (batch entry, FR-H3) is the only second row allowed.
            key = (item, row["date_of_transaction"])
            amount = (row["qty"], row["unit_price"])
            self.assertEqual(seen.setdefault(key, amount), amount, "at most one row per item per day")
        self.assertEqual(len({index[r["item/service"]] for r in self.result.rows}), len(self.files["catalog"]["items"]))

    def test_run_json_documents_the_run(self):
        run = self.result.run_json
        for key in ("config", "seed", "config_hash", "run_id", "rows", "total", "interpreter", "generator", "scorecard"):
            self.assertIn(key, run)
        self.assertEqual(set(run["bundle"]), {"id", "hash", "reviewed", "interpreter"})
        self.assertEqual(len(run["bundle"]["hash"]), 64)
        self.assertTrue(run["bundle"]["id"].endswith("-" + run["bundle"]["hash"][:12]))
        self.assertRegex(self.result.csv_path.name, CSV_NAME)
        self.assertEqual(self.result.csv_path.name, f"txns-2026Q3-{run['run_id'][:6]}.csv")
        self.assertEqual(run["csv"], self.result.csv_path.name)
        self.assertEqual(run["rows"], len(self.result.rows))
        total = sum(int(r["qty"]) * int(Decimal(r["unit_price"]) * 100) for r in self.result.rows)
        self.assertEqual(run["total_centavos"], total)
        self.assertEqual(Decimal(run["total"]) * 100, total)
        self.assertRegex(run["interpreter"], r"^\w+ 3\.1[2-9]")
        self.assertTrue(run["generator"].startswith("txns "))
        self.assertIn(f"{run['rows']} rows", self.result.stdout)

    def test_format_violation_is_a_hard_failure_with_csv_written(self):
        ws = Workspace(self)
        ws.install_bundle(
            mutate=lambda f: f["text"].update({"pantry.coffee": {"descriptive": ["Coffee " + "x" * 120], "terse": []}})
        )
        r = ws.run("generate", "--seed", "1")
        self.assertEqual(r.code, 1)
        self.assertTrue(r.csv_path.exists())
        self.assertTrue(r.run_json["scorecard"]["hard_failure"])
        self.assertIn("FAIL", r.stdout)


if __name__ == "__main__":
    unittest.main()
