"""`txns author`: name scrubbing, the LLM payload and the leak check (ticket 13: FR-C2, T37).

Uses the fabricated fixture ledgers and the fixture brand allowlist
(tests/fixtures/brands-allowlist.txt: Streamwave, Pixelforge Software). Every
other name in the fixture ledgers is "real" for these tests and must never
reach the payload:
- vendor parts of descriptions, kept or left out by the spend-only rule;
- the Reference column ("Harvest Moon");
- the organisation line ("Fixture Post Studio").
"""

import json
import re
import shutil
import unittest
from unittest import mock

from tests.helpers import FIXTURE_LEDGERS, REPO_ROOT, Workspace
from tests.test_author_ledger import KEPT_CATEGORIES, KEY, LEDGER_2024
from txns import ledger, privacy
from txns.privacy.names import NameIndex

AUTHOR_DIR = ".txns/author"
PAYLOAD = f"{AUTHOR_DIR}/payload.json"
NAME_MAP = f"{AUTHOR_DIR}/name-map.json"
BLOCKED = [
    "Fixture Post Studio",  # organisation line
    "Harvest Moon",  # reference column
    "Ledgerline Bookkeeping",  # left out (Consulting & Accounting)
    "Juan Dela Cruz",  # left out (pays a person)
    "Clientele Pictures",  # left out (sold services)
    "Swiftlane Couriers",
    "Bean Harbor Cafe",
    "Lutong Bahay Kitchen",
    "Northgate Hardware",
    "Northgate Digital",
    "Coolbreeze Aircon Services",
    "Resonance Audio",
    "Skyway Travel",
]
DISTINCTIVE_WORDS = ["Swiftlane", "Harbor", "Lutong", "Northgate", "Coolbreeze", "Resonance", "Ledgerline",
                     "Clientele", "Skyway", "Harvest", "Juan", "Fixture Post"]
ALLOWLISTED = ["Streamwave", "Pixelforge Software"]
CATEGORY_KEYS = {"category", "subscription", "rows", "spend", "amount_percentiles", "textless_rows",
                 "item_texts", "vendors"}


class ScrubCase(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)

    def author(self, ws=None):
        return (ws or self.ws).run("author", env=KEY)

    def ok(self, ws=None):
        r = self.author(ws)
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        return r

    def payload_text(self, ws=None) -> str:
        return ((ws or self.ws).cwd / PAYLOAD).read_text(encoding="utf-8")

    def payload(self, ws=None) -> dict:
        return json.loads(self.payload_text(ws))

    def category(self, name: str) -> dict:
        return next(c for c in self.payload()["categories"] if c["category"] == name)

    def fake(self, real: str) -> str:
        return json.loads((self.ws.cwd / NAME_MAP).read_text(encoding="utf-8"))["names"][real]["fake"]

    def edit_ledger(self, old: str, new: str, name: str = LEDGER_2024, count: int = 1) -> None:
        path = self.ws.cwd / "inputs" / "ledgers" / name
        text = path.read_bytes().decode("utf-8")
        self.assertEqual(text.count(old), count, f"fixture text {old!r} not found as expected")
        path.write_bytes(text.replace(old, new).encode("utf-8"))

    def assert_no_blocked_name(self, text: str) -> None:
        folded = text.casefold()
        for name in BLOCKED + DISTINCTIVE_WORDS:
            self.assertNotIn(name.casefold(), folded, name)


class PayloadTest(ScrubCase):
    def setUp(self):
        super().setUp()
        self.ws.install_author_inputs()
        self.r = self.ok()

    def test_payload_is_written_to_the_temp_folder(self):  # T37
        self.assertIn(f"wrote {PAYLOAD}", self.r.stdout)
        self.assertIn("leak check passed", self.r.stdout)
        self.assertEqual(self.payload_text(), json.dumps(self.payload(), sort_keys=True, indent=2, ensure_ascii=False) + "\n")

    def test_aggregates_and_item_text_patterns_only(self):  # FR-C2
        p = self.payload()
        self.assertEqual(set(p), {"format", "currency", "quarters", "categories"})
        self.assertEqual([c["category"] for c in p["categories"]], KEPT_CATEGORIES)
        for c in p["categories"]:
            self.assertEqual(set(c), CATEGORY_KEYS)
        # No raw rows: no dates, no row-level lines, nothing from rows the spend-only rule left out.
        text = self.payload_text()
        self.assertIsNone(re.search(r"\d{1,2} [A-Z][a-z]{2} \d{4}|\d{4}-\d{2}-\d{2}", text))
        for left_out in ("Refund of double charge", "Returned item", "Monthly bookkeeping", "Utility work",
                         "Airfare rebilled", "Depreciation"):
            self.assertNotIn(left_out, text)
        delivery = self.category("Delivery Fee")
        self.assertEqual(delivery["rows"], 9)
        self.assertEqual(delivery["spend"], 133100)
        self.assertEqual(delivery["textless_rows"], 2)
        self.assertEqual(delivery["item_texts"], [{"text": "Drive delivery to client", "rows": 5},
                                                  {"text": "Document pickup", "rows": 2}])
        meals = self.category("Office Expense - Meals")
        self.assertIn({"text": "Lunch for online edit team", "rows": 1}, meals["item_texts"])
        reference = json.loads((self.ws.cwd / AUTHOR_DIR / "reference.json").read_text(encoding="utf-8"))
        self.assertEqual(p["quarters"], reference["quarter_totals"])
        self.assertTrue(self.category("Software Subscriptions")["subscription"])

    def test_no_real_name_in_the_payload(self):  # FR-C2, T37
        self.assert_no_blocked_name(self.payload_text())
        # The reusable check agrees, over everything author writes except the local name map.
        index = NameIndex(ledger.read_ledgers(self.ws.cwd / "inputs" / "ledgers")[0], privacy.load_allowlist(self.ws.cwd))
        files = [p for p in (self.ws.cwd / AUTHOR_DIR).iterdir() if p.name != "name-map.json"]
        self.assertEqual(privacy.find_in_files(files, index, root=self.ws.cwd), [])

    def test_vendors_are_allowlisted_brands_or_fabricated(self):  # FR-C2
        subs = self.category("Software Subscriptions")
        self.assertEqual(subs["vendors"], [{"name": "Pixelforge Software", "rows": 24},
                                           {"name": "Streamwave", "rows": 6}])
        delivery = self.category("Delivery Fee")
        self.assertEqual(delivery["vendors"], [{"name": self.fake("Swiftlane Couriers"), "rows": 9}])
        storage = self.category("Storage Devices")["vendors"]
        self.assertEqual(storage, [{"name": self.fake("Northgate Digital"), "rows": 3}])
        self.assertNotEqual(self.fake("Northgate Digital"), self.fake("Northgate Hardware"))
        self.assertRegex(self.fake("Harvest Moon"), r"^Project [A-Z][a-z]+$")  # a reference

    def test_real_to_fake_map_stays_local(self):
        names = json.loads((self.ws.cwd / NAME_MAP).read_text(encoding="utf-8"))["names"]
        self.assertEqual(set(BLOCKED) - set(names), set())
        self.assertFalse(set(ALLOWLISTED) & set(names))
        self.assertEqual(len({v["fake"] for v in names.values()}), len(names))  # one fake per real name
        text = self.payload_text()
        self.assertNotIn("name-map", text)
        for real in names:
            self.assertNotIn(real, text)
        # The temp folder is ignored by git, so the map is never committed.
        self.assertIn("/.txns/", (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertFalse((self.ws.cwd / "bundles").exists())


class StableFakesTest(ScrubCase):
    def setUp(self):
        super().setUp()
        self.ws.install_author_inputs()

    def test_same_real_name_always_maps_to_the_same_fake(self):  # FR-C2
        # A spelling variant of the same vendor, and names inside item text.
        self.edit_ledger("20 Feb 2024,Spend Money,Swiftlane Couriers,", "20 Feb 2024,Spend Money,SWIFTLANE COURIERS INC.,")
        self.edit_ledger("Dinner for sound mix", "Dinner for Juan Dela Cruz and the Harvest Moon crew")
        self.edit_ledger("Swiftlane Couriers - Document pickup", "Swiftlane Couriers - Document pickup at bean harbor cafe")
        self.ok()
        delivery = self.category("Delivery Fee")
        self.assertEqual(delivery["vendors"], [{"name": self.fake("Swiftlane Couriers"), "rows": 9}])
        meals = self.category("Office Expense - Meals")
        self.assertIn(
            {"text": f"Dinner for {self.fake('Juan Dela Cruz')} and the {self.fake('Harvest Moon')} crew", "rows": 1},
            meals["item_texts"],
        )
        self.assertIn({"name": self.fake("Bean Harbor Cafe"), "rows": 7}, meals["vendors"])
        self.assertIn({"text": f"Document pickup at {self.fake('Bean Harbor Cafe')}", "rows": 1}, delivery["item_texts"])
        self.assert_no_blocked_name(self.payload_text())

    def test_runs_and_workspaces_give_identical_payloads(self):
        self.ok()
        first = (self.ws.cwd / PAYLOAD).read_bytes()
        first_map = (self.ws.cwd / NAME_MAP).read_bytes()
        self.ok()
        self.assertEqual((self.ws.cwd / PAYLOAD).read_bytes(), first)
        other = Workspace(self)
        other.install_author_inputs(ledgers=None)
        shutil.copytree(FIXTURE_LEDGERS, other.cwd / "exports")
        other.write_config('[author]\nledgers_dir = "exports"\n')
        self.ok(other)
        self.assertEqual((other.cwd / PAYLOAD).read_bytes(), first)
        self.assertEqual((other.cwd / NAME_MAP).read_bytes(), first_map)

    def test_item_text_patterns_mask_numbers_and_emails(self):
        self.edit_ledger("Swiftlane Couriers - Document pickup", "Swiftlane Couriers - Document pickup 09171234567 ops@example.com")
        self.ok()
        texts = [t["text"] for t in self.category("Delivery Fee")["item_texts"]]
        self.assertIn("Document pickup ########### [email]", texts)
        self.assertNotIn("09171234567", self.payload_text())
        self.assertNotIn("example.com", self.payload_text())

    def test_account_headings_shield_names_inside_them(self):
        # A reference called "Delivery": the heading "Delivery Fee" stays, item text is scrubbed.
        self.edit_ledger("Swiftlane Couriers - Document pickup,,", "Swiftlane Couriers - Document pickup,Delivery,")
        self.ok()
        p = self.payload()
        self.assertEqual([c["category"] for c in p["categories"]], KEPT_CATEGORIES)
        texts = [t["text"] for t in self.category("Delivery Fee")["item_texts"]]
        self.assertIn(f"Drive {self.fake('Delivery')} to client", texts)


class AllowlistTest(ScrubCase):
    def write_allowlist(self, text: str) -> None:
        (self.ws.cwd / "inputs" / "brands-allowlist.txt").write_text(text, encoding="utf-8")

    def test_missing_allowlist_warns_and_scrubs_every_name(self):
        self.ws.install_author_inputs(allowlist=None)
        r = self.ok()
        self.assertIn("warning: inputs/brands-allowlist.txt not found", r.stderr)
        self.assertIn("every ledger name is replaced", r.stderr)
        text = self.payload_text()
        self.assert_no_blocked_name(text)
        for brand in ALLOWLISTED:
            self.assertNotIn(brand, text)

    def test_matching_ignores_case_spacing_and_legal_suffixes(self):
        self.ws.install_author_inputs()
        self.write_allowlist("# brands\nSTREAMWAVE\n  pixelforge   software, inc.  \n")
        r = self.ok()
        self.assertEqual(r.stderr, "")
        self.assertEqual(self.category("Software Subscriptions")["vendors"],
                         [{"name": "pixelforge software, inc.", "rows": 24}, {"name": "STREAMWAVE", "rows": 6}])
        self.assertIn("brand allowlist: 2 entries; 2 ledger names kept", r.stdout)

    def test_allowlisted_vendor_is_kept_and_others_scrubbed(self):
        self.ws.install_author_inputs()
        self.write_allowlist("Swiftlane Couriers\n")
        self.ok()
        self.assertEqual(self.category("Delivery Fee")["vendors"], [{"name": "Swiftlane Couriers", "rows": 9}])
        self.assertNotIn("Streamwave", self.payload_text())


class LeakCheckTest(ScrubCase):
    def setUp(self):
        super().setUp()
        self.ws.install_author_inputs()
        self.edit_ledger("Dinner for sound mix", "Dinner for Juan Dela Cruz")  # a name inside item text
        self.ok()  # leaves a good payload.json behind: a failed run must remove it

    def assert_blocked(self, r):
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn("leak check failed", r.stderr)
        self.assertIn("no LLM call made", r.stderr)
        self.assertFalse((self.ws.cwd / PAYLOAD).exists())
        self.assert_no_blocked_name(r.stdout + r.stderr)  # the report says where, not what

    def test_scrubber_gap_in_text_blocks_the_call(self):  # T37
        with mock.patch.object(NameIndex, "scrub", lambda self, text: text):
            r = self.author()
        self.assert_blocked(r)
        self.assertIn(".item_texts[", r.stderr)

    def test_scrubber_gap_in_vendor_names_blocks_the_call(self):  # T37
        with mock.patch.object(NameIndex, "label", lambda self, raw: raw), \
             mock.patch.object(NameIndex, "scrub_obj", lambda self, value: value):
            r = self.author()
        self.assert_blocked(r)
        self.assertIn(".vendors[0].name", r.stderr)


class FilesCheckTest(ScrubCase):
    """The reusable check the promotion gates will run over a bundle (FR-D2 gate 3)."""

    def test_reports_where_real_names_are(self):
        self.ws.install_author_inputs()
        index = NameIndex(ledger.read_ledgers(FIXTURE_LEDGERS)[0], privacy.load_allowlist(self.ws.cwd))
        d = self.ws.cwd / "staged"
        d.mkdir()
        (d / "clean.json").write_text(json.dumps({"coffee": ["Streamwave plan", "Delivery Fee", "Pinebrook Trading"]}))
        # Escaped JSON text is parsed, so "Bean" still reads "Bean".
        (d / "text.json").write_text('{"coffee": {"descriptive": ["Latte", "Coffee at \\u0042ean  HARBOR cafe"]}}')
        (d / "notes.txt").write_text("fine\nordered from swiftlane couriers\n")
        (d / "Harvest Moon.txt").write_text("fine\n")
        found = privacy.find_in_files([d], index, root=self.ws.cwd)
        self.assertEqual(found, [
            "staged/Harvest Moon.txt: file name",
            "staged/notes.txt line 2",
            "staged/text.json: $.coffee.descriptive[1]",
        ])
        self.assertEqual(privacy.find_in_files([d / "clean.json"], index), [])

    def test_a_name_inside_a_longer_allowlisted_brand_passes(self):
        self.ws.install_author_inputs()
        (self.ws.cwd / "inputs" / "brands-allowlist.txt").write_text("Skyway Travel Rewards\n")
        index = NameIndex(ledger.read_ledgers(FIXTURE_LEDGERS)[0], privacy.load_allowlist(self.ws.cwd))
        f = self.ws.cwd / "a.txt"
        f.write_text("Skyway Travel Rewards points\n")
        self.assertEqual(privacy.find_in_files([f], index), [])
        f.write_text("Skyway Travel Rewards points, Skyway Travel desk\n")
        self.assertEqual(len(privacy.find_in_files([f], index)), 1)


if __name__ == "__main__":
    unittest.main()
