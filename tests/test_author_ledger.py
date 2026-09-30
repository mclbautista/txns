"""`txns author`, first part: API key check, strict ledger reader, spend-only rule
and reference.json (ticket 12: FR-B1 to FR-B4, FR-C1, T33, T40).

Uses the fabricated fixture ledgers in tests/fixtures/ledgers, never the real ones.
Fixture facts the figures below rely on (2024 + 2025 exports):
- 65 transaction rows; 57 kept. Left out: 2 Consulting & Accounting, 1 Utility/Labor
  (paying a person), 1 credit line, 1 blank-source depreciation, 1 Receive Money,
  1 Receivable Invoice, 1 Receivable Credit Note. Labour repairs stay.
- 30 subscription rows; 27 others, 2 of them on regular holidays (2024-06-12, 2025-04-17).
- 2 same-day same-amount same-category groups (Delivery Fee).
- Pack sizes in text: 15pcs and 4 pcs drives, 5 reams, 2 units.
"""

import hashlib
import json
import shutil
import unittest

from tests.helpers import FIXTURE_LEDGERS, Workspace
from txns.canonical import pretty_json

KEY = {"OPENROUTER_API_KEY": "sk-or-test-key-123"}
REFERENCE = ".txns/author/reference.json"
LEDGERS = ".txns/author/ledgers.json"
LEDGER_2024 = "account-transactions-2024.csv"

KEPT_CATEGORIES = [
    "Conferences and Events - Travel",
    "Delivery Fee",
    "Office Expense - Meals",
    "Office Supplies",
    "Repairs and Maintenance-Labor",
    "Software Subscriptions",
    "Sound System and Equipment",
    "Storage Devices",
]
EXCLUDED_CATEGORIES = ["Consulting & Accounting", "Office Expense - Utility/Labor", "Bad Debts Expense"]
FIXTURE_NAMES = [
    "Fixture Post Studio",
    "Bean Harbor",
    "Swiftlane",
    "Lutong Bahay",
    "Juan Dela Cruz",
    "Northgate",
    "Pixelforge",
    "Streamwave",
    "Clientele Pictures",
    "Harvest Moon",
    "Coffee for client review",
]


class AuthorCase(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)

    def author(self, *argv, env=KEY):
        return self.ws.run("author", *argv, env=env)

    def reference(self) -> dict:
        return json.loads((self.ws.cwd / REFERENCE).read_text(encoding="utf-8"))

    def assert_exit_2(self, r, *fragments):
        self.assertEqual(r.code, 2, r.stdout + r.stderr)
        for f in fragments:
            self.assertIn(f, r.stderr)
        self.assertFalse((self.ws.cwd / REFERENCE).exists())

    def edit_ledger(self, old: str, new: str, name: str = LEDGER_2024, count: int = 1) -> None:
        path = self.ws.cwd / "inputs" / "ledgers" / name
        text = path.read_bytes().decode("utf-8")
        self.assertEqual(text.count(old), count, f"fixture text {old!r} not found as expected")
        path.write_bytes(text.replace(old, new).encode("utf-8"))

    def edit_spend_list(self, fn) -> None:
        path = self.ws.cwd / "inputs" / "spend-only.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        fn(data)
        path.write_text(json.dumps(data), encoding="utf-8")


class ApiKeyTest(AuthorCase):
    def test_missing_key_exits_2_before_any_other_work(self):  # T33, FR-C1
        # No ledgers, no committed inputs and an invalid config: the key check still comes first.
        self.ws.write_config("not_a_key = 1\n")
        for env in ({}, {"OPENROUTER_API_KEY": ""}, {"OPENROUTER_API_KEY": "  "}):
            with self.subTest(env=env):
                r = self.author(env=env)
                self.assert_exit_2(r, "OPENROUTER_API_KEY")
                self.assertNotIn("not_a_key", r.stderr)
                self.assertFalse((self.ws.cwd / ".txns").exists())

    def test_key_is_never_written(self):  # FR-C1
        self.ws.install_author_inputs()
        self.assertEqual(self.author().code, 0)
        for path in (self.ws.cwd / ".txns").rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY["OPENROUTER_API_KEY"], path.read_text(encoding="utf-8"))


class ReferenceTest(AuthorCase):
    def setUp(self):
        super().setUp()
        self.ws.install_author_inputs()
        self.r = self.author()
        self.assertEqual(self.r.code, 0, self.r.stdout + self.r.stderr)
        self.ref = self.reference()

    def test_writes_reference_and_ledger_hashes_to_temp_folder(self):  # FR-B4
        self.assertIn(f"wrote {REFERENCE}", self.r.stdout)
        self.assertIn("57 spend rows kept, 8 left out", self.r.stdout)
        hashes = json.loads((self.ws.cwd / LEDGERS).read_text(encoding="utf-8"))["ledger_hashes"]
        expected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(FIXTURE_LEDGERS.glob("*.csv"))}
        self.assertEqual(hashes, expected)
        self.assertFalse((self.ws.cwd / "bundles").exists())

    def test_category_headings_become_category_labels(self):  # FR-B2
        self.assertEqual(sorted(self.ref["category_totals"]), KEPT_CATEGORIES)
        self.assertEqual(sorted(self.ref["terse_share"]), KEPT_CATEGORIES)
        self.assertEqual(sorted(self.ref["category_amount_percentiles"]), KEPT_CATEGORIES)
        self.assertIn("categories: 8", self.r.stdout)

    def test_spend_only_rule(self):  # FR-B3
        text = json.dumps(self.ref)
        for cat in EXCLUDED_CATEGORIES:
            self.assertNotIn(cat, text)
        self.assertIn("Repairs and Maintenance-Labor", self.ref["category_totals"])  # labour repairs stay
        totals = self.ref["category_totals"]
        self.assertEqual(sum(t["rows"] for t in totals.values()), 57)
        # Credit lines, refunds and rebilled costs never count as spend.
        self.assertEqual(totals["Delivery Fee"], {"rows": 9, "spend": 133100})
        self.assertEqual(totals["Office Supplies"], {"rows": 2, "spend": 173000})
        self.assertEqual(totals["Conferences and Events - Travel"], {"rows": 1, "spend": 3850000})
        self.assertEqual(totals["Sound System and Equipment"], {"rows": 1, "spend": 4500000})
        for reason in ("category Consulting & Accounting", "category Office Expense - Utility/Labor",
                       "credit line", "source (blank)", "source Receive Money",
                       "source Receivable Invoice", "source Receivable Credit Note"):
            self.assertIn(reason, self.r.stdout)

    def test_fr_b4_statistics(self):
        ref = self.ref
        self.assertEqual(
            sorted(ref),
            sorted([
                "anchor_prices", "category_amount_percentiles", "category_totals", "distinct_per_item",
                "duplicate_group_rate", "holiday_share", "month_end_share", "monthly_spread", "pack_sizes",
                "quarter_totals", "round_amount_share", "row_amount_percentiles", "terse_share",
                "weekday_shares", "whole_peso_share",
            ]),
        )
        self.assertEqual(ref["holiday_share"], round(2 / 27, 4))
        self.assertEqual(ref["duplicate_group_rate"], round(2 / 57, 4))
        self.assertEqual(ref["whole_peso_share"], round(55 / 57, 4))  # two ₱562.50 rows
        self.assertEqual(ref["round_amount_share"], round(8 / 57, 4))
        self.assertEqual(ref["row_amount_percentiles"]["p50"], 264200)
        self.assertAlmostEqual(sum(ref["weekday_shares"].values()), 1.0, places=3)
        self.assertEqual(set(ref["monthly_spread"]), {"rows", "spend"})
        self.assertEqual(ref["terse_share"]["Delivery Fee"], round(2 / 9, 4))
        self.assertEqual(ref["terse_share"]["Office Expense - Meals"], 0.4)
        self.assertEqual(ref["terse_share"]["Software Subscriptions"], 0.2)
        self.assertEqual(ref["category_amount_percentiles"]["Delivery Fee"]["p10"], 9500)
        self.assertEqual(
            ref["pack_sizes"],
            {
                "Office Supplies": [{"qty": 5, "rows": 1}],
                "Sound System and Equipment": [{"qty": 2, "rows": 1}],
                "Storage Devices": [{"qty": 4, "rows": 1}, {"qty": 15, "rows": 1}],
            },
        )
        self.assertEqual(
            ref["anchor_prices"]["Software Subscriptions"],
            [{"unit_price": 34900, "rows": 6}, {"unit_price": 264200, "rows": 12}, {"unit_price": 289100, "rows": 12}],
        )
        # "4 pcs" at ₱28,900 is ₱7,225 a unit, the same as the single 4TB drive.
        self.assertEqual(ref["anchor_prices"]["Storage Devices"], [{"unit_price": 722500, "rows": 2}])
        quarters = ref["quarter_totals"]
        self.assertEqual(sorted(quarters), [f"{y}Q{q}" for y in (2024, 2025) for q in (1, 2, 3, 4)])
        self.assertEqual(sum(q["rows"] for q in quarters.values()), 57)
        self.assertEqual(
            sum(q["spend"] for q in quarters.values()), sum(t["spend"] for t in ref["category_totals"].values())
        )

    def test_reference_holds_no_ledger_text(self):  # privacy: aggregates and category labels only
        text = (self.ws.cwd / REFERENCE).read_text(encoding="utf-8")
        for name in FIXTURE_NAMES:
            self.assertNotIn(name, text)
            self.assertNotIn(name.casefold(), text.casefold())

    def test_generate_accepts_the_derived_reference(self):
        ref = self.ref
        self.ws.install_bundle(mutate=lambda files: files.__setitem__("reference", ref))
        r = self.ws.run("generate", "--seed", "42")
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        checks = {c["name"]: c for c in r.run_json["scorecard"]["checks"]}
        self.assertEqual(checks["weekday_shares.mon"]["reference"], ref["weekday_shares"]["mon"])
        self.assertEqual(checks["round_amount_share"]["reference"], ref["round_amount_share"])


class DeterminismTest(AuthorCase):
    def test_same_ledgers_give_identical_reference_bytes(self):  # T40
        self.ws.install_author_inputs()
        self.assertEqual(self.author().code, 0)
        first = (self.ws.cwd / REFERENCE).read_bytes()
        first_hashes = (self.ws.cwd / LEDGERS).read_bytes()
        self.assertEqual(self.author("--label", "again").code, 0)
        self.assertEqual((self.ws.cwd / REFERENCE).read_bytes(), first)

        # Another working folder, another ledgers folder name: same bytes.
        other = Workspace(self)
        other.install_author_inputs(ledgers=None)
        shutil.copytree(FIXTURE_LEDGERS, other.cwd / "exports" / "xero")
        other.write_config('[author]\nledgers_dir = "exports/xero"\n')
        r = other.run("author", env=KEY)
        self.assertEqual(r.code, 0, r.stderr)
        self.assertEqual((other.cwd / REFERENCE).read_bytes(), first)
        self.assertEqual((other.cwd / LEDGERS).read_bytes(), first_hashes)
        self.assertEqual(first.decode("utf-8"), pretty_json(json.loads(first)))


class MissingLedgersTest(AuthorCase):
    def test_missing_ledgers_folder(self):  # T33
        self.ws.install_author_inputs(ledgers=None)
        self.assert_exit_2(self.author(), "ledgers folder not found")

    def test_configured_ledgers_folder_missing(self):
        self.ws.install_author_inputs()
        self.ws.write_config('[author]\nledgers_dir = "elsewhere"\n')
        self.assert_exit_2(self.author(), "elsewhere")

    def test_no_exports_in_folder(self):
        (self.ws.install_author_inputs(ledgers=None)).mkdir()
        self.assert_exit_2(self.author(), "no ledger exports")

    def test_unreadable_ledger(self):
        ledgers = self.ws.install_author_inputs()
        (ledgers / "account-transactions-2023.csv").write_bytes(b"Account Transactions\xff\xfe,,,\r\n")
        self.assert_exit_2(self.author(), "account-transactions-2023.csv", "unreadable")

    def test_overlapping_exports(self):
        ledgers = self.ws.install_author_inputs()
        shutil.copyfile(ledgers / LEDGER_2024, ledgers / "copy-of-2024.csv")
        self.assert_exit_2(self.author(), "overlap")

    def test_missing_spend_only_list(self):
        self.ws.install_author_inputs()
        (self.ws.cwd / "inputs" / "spend-only.json").unlink()
        self.assert_exit_2(self.author(), "spend-only.json")


class StrictLayoutTest(AuthorCase):
    def setUp(self):
        super().setUp()
        self.ws.install_author_inputs()

    def test_unrecognised_layouts_exit_2(self):  # FR-B1, T33
        cases = [
            ("missing title", "Account Transactions,,,,,,,,\r\n", ""),
            ("bad period line", "For the period 1 January 2024", "Period 1 January 2024"),
            ("header", "Running Balance,Gross,Tax", "Balance,Gross,Tax"),
            ("extra column", "Delivery Fee,,,,,,,,\r\n", "Delivery Fee,,,,,,,,,\r\n"),
            ("row outside a section", "Delivery Fee,,,,,,,,\r\n", ""),
            ("unknown row", "Total Delivery Fee,", "Subtotal,,,,,,,,\r\nTotal Delivery Fee,"),
            ("bad amount", ",150.00,0.00,", ",150.0,0.00,"),
            ("bad date", "03 May 2024", "3 May 2024"),
            ("date outside the export period", "14 Aug 2024", "14 Aug 2023"),
            ("total names another section", "Total Delivery Fee,", "Total Delivery Fees,"),
            ("missing grand total", "\r\nTotal,,,,", "\r\n"),
            ("opening balance without closing", "Closing Balance,", "Closing Balances,"),
        ]
        for label, old, new in cases:
            with self.subTest(label):
                self.ws.install_author_inputs()  # fresh copy
                self.edit_ledger(old, new)
                self.assert_exit_2(self.author(), LEDGER_2024)

    def test_missing_section_total(self):
        self.edit_ledger("Total Storage Devices,,,,", "Storage Totals,,,,")
        self.assert_exit_2(self.author(), "unrecognised row")

    def test_non_reconciling_totals_exit_2(self):  # FR-B1, T33
        cases = [
            ("section debit", "Total Delivery Fee,,,,555.00,", "Total Delivery Fee,,,,565.00,"),
            ("row edited, total not", ",Swiftlane Couriers - Document pickup,,150.00,", ",Swiftlane Couriers - Document pickup,,160.00,"),
            ("grand total", 'Total,,,,"135,203.00"', 'Total,,,,"135,204.00"'),
            ("closing balance", 'Closing Balance,,,,"164,000.00",0.00,"164,000.00"',
             'Closing Balance,,,,"164,100.00",0.00,"164,100.00"'),
            ("opening balance", 'Opening Balance,,,,"120,000.00",0.00,"120,000.00"',
             'Opening Balance,,,,"120,100.00",0.00,"120,100.00"'),
        ]
        for label, old, new in cases:
            with self.subTest(label):
                self.ws.install_author_inputs()
                self.edit_ledger(old, new)
                r = self.author()
                self.assert_exit_2(r, LEDGER_2024, "reconcile")

    def test_multi_line_descriptions_are_read(self):
        # The fixture's "Lunch for\nonline edit team" row spans two physical lines.
        self.assertIn('"Lutong Bahay Kitchen - Lunch for\nonline edit team"',
                      (self.ws.cwd / "inputs" / "ledgers" / LEDGER_2024).read_text(encoding="utf-8"))
        self.assertEqual(self.author().code, 0)


class SpendListTest(AuthorCase):
    def setUp(self):
        super().setUp()
        self.ws.install_author_inputs()

    def test_the_committed_list_drives_the_rule(self):  # FR-B3
        self.edit_spend_list(lambda d: d["excluded_categories"].pop("Office Expense - Utility/Labor"))
        r = self.author()
        self.assertEqual(r.code, 0, r.stderr)
        self.assertIn("Office Expense - Utility/Labor", self.reference()["category_totals"])

    def test_source_in_neither_list_exits_2(self):
        self.edit_spend_list(lambda d: d["excluded_sources"].pop("Receive Money"))
        self.assert_exit_2(self.author(), "Receive Money", "spend-only.json")


if __name__ == "__main__":
    unittest.main()
