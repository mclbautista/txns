"""`txns approve [BUNDLE]` (ticket 17: FR-D4, FR-D5, FR-C1, FR-H8, T41, ADR 0002).

Bundles come from a real `author` run on the fabricated fixture ledgers with the
scripted LLM fake (`tests/test_author_bundle.BundleCase`), so they pass every
offline gate as promoted. Hand edits are made in place in the promoted folder,
as the owner would.
"""

import argparse
import io
import json
import socket
import unittest
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

from tests.helpers import DEFAULT_TODAY, Workspace
from tests.test_author_bundle import BundleCase
from txns.bundle import hashing, store
from txns.commands import Runtime
from txns.commands import approve as approve_cmd


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root` (relative path -> bytes)."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class ApproveCase(BundleCase):
    def approve(self, *args, env=None):
        return self.ws.run("approve", *args, env=env if env is not None else {})

    def approved(self, *args):
        r = self.approve(*args)
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        return r

    @property
    def root(self) -> Path:
        return self.ws.cwd / "bundles"

    def latest(self) -> Path:
        return store.find(self.root, "latest")

    def manifest(self, folder: Path) -> dict:
        return self.read(folder, "manifest")

    def edit(self, folder: Path, stem: str, fn) -> None:
        """Hand-edit one bundle file in place."""
        data = self.read(folder, stem)
        fn(data)
        (folder / f"{stem}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class NoBundleTest(unittest.TestCase):
    def test_no_bundle_exits_2(self):  # FR-D4
        ws = Workspace(self)
        r = ws.run("approve")
        self.assertEqual(r.code, 2, r.stderr)
        self.assertIn("no bundle found", r.stderr)
        self.assertFalse((ws.cwd / "bundles").exists())


class UnreadableBundleTest(ApproveCase):
    def test_other_bundle_still_approved_and_broken_one_named_exits_4(self):  # review 4
        self.ok("--label", "v1")
        v1 = self.only_bundle()
        broken = self.root / "v0-000000000000"
        broken.mkdir()
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")
        r = self.approved()
        self.assertIn(f"warning: skipping bundles/{broken.name}/", r.stderr)
        self.assertIs(self.manifest(v1)["reviewed"], True)
        r = self.approve(broken.name)
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn("unreadable manifest", r.stderr)


class UneditedTest(ApproveCase):
    def setUp(self):
        super().setUp()
        self.ok("--label", "v1")
        self.folder = self.only_bundle()

    def test_sets_reviewed_without_changing_the_hash(self):  # T41
        before = self.manifest(self.folder)
        r = self.approved()
        after = self.manifest(self.folder)
        self.assertIs(after["reviewed"], True)
        self.assertEqual(after["hash"], before["hash"])
        self.assertEqual(hashing.content_hash(self.folder), before["hash"])
        self.assertEqual(self.bundles(), [self.folder.name])  # no new folder
        self.assertEqual({k: v for k, v in after.items() if k != "reviewed"},
                         {k: v for k, v in before.items() if k != "reviewed"})
        self.assertIn("content matches its hash", r.stdout)
        self.assertRegex(r.stdout, r"PASS  gate 8 ")
        self.assertIn(f"approved {self.folder.name}: reviewed: true", r.stdout)
        self.assertTrue(store.load(self.folder).reviewed)
        self.assertFalse(list(self.root.glob(".*")))  # no temp file left next to the bundle

    def test_generate_no_longer_warns_about_review(self):  # FR-H8
        r = self.ws.run("generate", "--seed", "7")
        self.assertIn("is unreviewed", r.stderr)
        self.approved()
        r = self.ws.run("generate", "--seed", "7")
        self.assertEqual(r.code, 0, r.stdout + r.stderr)
        self.assertNotIn("unreviewed", r.stderr)
        self.assertIs(r.run_json["bundle"]["reviewed"], True)

    def test_already_reviewed_changes_nothing(self):
        self.approved()
        before = snapshot(self.root)
        r = self.approved()
        self.assertIn("already reviewed, nothing changed", r.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_named_bundle(self):  # FR-D4: a named bundle instead of the latest
        self.ok("--label", "v2")
        v2 = self.latest()
        self.assertNotEqual(v2, self.folder)
        self.approved(self.folder.name)
        self.assertIs(self.manifest(self.folder)["reviewed"], True)
        self.assertIs(self.manifest(v2)["reviewed"], False)
        self.approved()  # default: the latest
        self.assertIs(self.manifest(v2)["reviewed"], True)

    def test_unknown_name_exits_2(self):
        before = snapshot(self.root)
        r = self.approve("v9-000000000000")
        self.assertEqual(r.code, 2, r.stderr)
        self.assertIn("unknown bundle", r.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_missing_ledgers_exit_2(self):  # the leak gate needs the ledger names
        self.ws.remove("inputs/ledgers")
        before = snapshot(self.root)
        r = self.approve()
        self.assertEqual(r.code, 2, r.stderr)
        self.assertIn("ledgers folder not found", r.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_offline_without_api_key_and_no_llm(self):  # FR-C1
        def refuse(*a, **k):
            raise AssertionError("network access attempted")

        with mock.patch.object(socket, "socket", refuse), mock.patch.object(
            socket, "create_connection", refuse
        ), mock.patch.object(socket, "getaddrinfo", refuse):
            calls = len(self.ws.llm.requests)
            self.approved()
        self.assertEqual(len(self.ws.llm.requests), calls)

    def test_never_reads_the_api_key(self):  # FR-C1
        class GuardedEnv(Mapping):
            def __getitem__(self, key):
                if key == "OPENROUTER_API_KEY":
                    raise AssertionError("approve read OPENROUTER_API_KEY")
                raise KeyError(key)

            def get(self, key, default=None):
                return self[key] if key in self else default

            def __contains__(self, key):
                if key == "OPENROUTER_API_KEY":
                    raise AssertionError("approve read OPENROUTER_API_KEY")
                return False

            def __iter__(self):
                raise AssertionError("approve listed the environment")

            def __len__(self):
                return 0

        out, err = io.StringIO(), io.StringIO()
        rt = Runtime(today=DEFAULT_TODAY, env=GuardedEnv(), cwd=self.ws.cwd, stdout=out, stderr=err)
        code = approve_cmd.run(argparse.Namespace(bundle=None, config=None), rt)
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        self.assertIs(self.manifest(self.folder)["reviewed"], True)


class HandEditedTest(ApproveCase):
    def setUp(self):
        super().setUp()
        self.ok("--label", "v1")
        self.folder = self.only_bundle()
        self.original_manifest = self.manifest(self.folder)

    def test_writes_a_new_reviewed_folder_and_leaves_the_original(self):  # T41, ADR 0002
        self.edit(self.folder, "storylines",
                  lambda s: s["errands"].update(description="Small errands run by the office staff"))
        edited = snapshot(self.folder)
        r = self.approved()
        self.assertIn("content differs from its hash (hand-edited)", r.stdout)

        self.assertEqual(snapshot(self.folder), edited)  # original folder exactly as it was
        self.assertEqual(len(self.bundles()), 2)
        new = self.latest()
        self.assertNotEqual(new, self.folder)
        manifest = self.manifest(new)
        self.assertEqual(new.name, f"v1-{manifest['hash'][:12]}")
        self.assertEqual(hashing.content_hash(new), manifest["hash"])
        self.assertNotEqual(manifest["hash"], self.original_manifest["hash"])
        self.assertIs(manifest["reviewed"], True)
        self.assertEqual(manifest["promotion"], self.original_manifest["promotion"] + 1)
        self.assertEqual(self.read(new, "storylines")["errands"]["description"],
                         "Small errands run by the office staff")
        self.assertEqual({p: b for p, b in snapshot(new).items() if p != "manifest.json"},
                         {p: b for p, b in edited.items() if p != "manifest.json"})
        self.assertIn(f"approved {new.name}: wrote bundles/{new.name}/ (reviewed: true), now the latest", r.stdout)
        self.assertFalse((self.ws.cwd / approve_cmd.STAGING_DIR).exists())  # consumed by promotion

        # generate takes the new one without the review warning; the edited original is refused.
        g = self.ws.run("generate", "--seed", "7")
        self.assertEqual(g.code, 0, g.stdout + g.stderr)
        self.assertNotIn("unreviewed", g.stderr)
        self.assertEqual(g.run_json["bundle"]["id"], new.name)
        g = self.ws.run("generate", "--seed", "7", "--bundle", self.folder.name)
        self.assertEqual(g.code, 4, g.stderr)

    def test_same_edit_approved_twice_writes_one_folder(self):
        self.edit(self.folder, "storylines",
                  lambda s: s["errands"].update(description="Small errands run by the office staff"))
        self.approved()
        before = snapshot(self.root)
        r = self.approved(self.folder.name)
        self.assertIn("already promoted as", r.stdout)
        self.assertIn("already reviewed, nothing changed", r.stdout)
        self.assertEqual(snapshot(self.root), before)

    def assert_rejected(self, r, gate: str):
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertRegex(r.stdout, rf"FAIL  gate {gate} ")
        self.assertIn("not approved and bundles/ unchanged", r.stderr)

    def test_over_long_variant_exits_4_and_changes_nothing(self):  # T41 invalid edit, T27
        self.edit(self.folder, "text", lambda t: t["delivery_fee_a"]["descriptive"].append("x" * 101))
        before = snapshot(self.root)
        r = self.approve()
        self.assert_rejected(r, "4-6")
        self.assertIn("longer than 100 characters", r.stdout)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.ws.cwd / approve_cmd.STAGING_DIR).exists())

    def test_real_ledger_name_exits_4(self):  # gate 3, reported by location only
        self.edit(self.folder, "text", lambda t: t["delivery_fee_a"]["terse"].append("Swiftlane Couriers"))
        before = snapshot(self.root)
        r = self.approve()
        self.assert_rejected(r, "3")
        self.assertIn("text.json: $.delivery_fee_a.terse[2]", r.stdout)
        self.assertNotIn("Swiftlane", r.stdout + r.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_approve_command_fails_on_anomaly(self):
        # Non-sensical fee text: the text gate (and the smoke run's anomaly check) reject it.
        self.edit(self.folder, "text", lambda t: t["delivery_fee_a"]["descriptive"].append("Out fee"))
        before = snapshot(self.root)
        r = self.approve()
        self.assert_rejected(r, "4-6")
        self.assertIn("variant 'Out fee' matches rules.json `denied_item_patterns`", r.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_approve_command_fails_on_rows_under_the_floor(self):
        # Soft in `generate`, the anomaly check fails the smoke gate here: the delivery fees
        # of the fixture ledgers draw rows well under a ₱500 floor.
        self.edit(self.folder, "rules", lambda rules: rules.setdefault("scorecard", {}).update(min_transaction_amount=500))
        before = snapshot(self.root)
        r = self.approve()
        self.assert_rejected(r, "8")
        self.assertIn("anomaly on seed 1", r.stdout)
        self.assertIn("is under the ₱500.00 floor", r.stdout)
        self.assertEqual(snapshot(self.root), before)

    def test_edit_that_breaks_loading_exits_4(self):  # gate 7
        self.edit(self.folder, "rate_cards", lambda c: c["delivery_fee_a"]["points"][0].update(unit_price=9512))
        before = snapshot(self.root)
        r = self.approve()
        self.assert_rejected(r, "7")
        self.assertEqual(snapshot(self.root), before)

    def test_bad_label_in_the_manifest_exits_4(self):
        self.edit(self.folder, "manifest", lambda m: m.update(label="../escape"))
        before = snapshot(self.root)
        r = self.approve()
        self.assertEqual(r.code, 4, r.stderr)
        self.assertIn("not a valid bundle label", r.stderr)
        self.assertEqual(snapshot(self.root), before)

    def test_missing_or_non_string_label_exits_4(self):
        for label in (None, 7):
            with self.subTest(label=label):
                self.edit(self.folder, "manifest",
                          lambda m: m.pop("label") if label is None else m.update(label=label))
                before = snapshot(self.root)
                r = self.approve()
                self.assertEqual(r.code, 4, r.stdout + r.stderr)
                self.assertIn("not a valid bundle label", r.stderr)
                self.assertEqual(snapshot(self.root), before)

    def test_new_name_taken_by_a_hand_edited_folder_exits_4_cleanly(self):
        # The original's edit is approved as v1-<X>; v1-<X> is then edited in place too.
        self.edit(self.folder, "storylines",
                  lambda s: s["errands"].update(description="Small errands run by the office staff"))
        self.approved()
        new = self.latest()
        self.edit(new, "storylines", lambda s: s["errands"].update(description="Edited again"))
        before = snapshot(self.root)
        r = self.approve(self.folder.name)  # its content still hashes to v1-<X>
        self.assertEqual(r.code, 4, r.stdout + r.stderr)
        self.assertIn(f"{new.name} already exists", r.stderr)
        self.assertIn("hand-edited", r.stderr)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.ws.cwd / approve_cmd.STAGING_DIR).exists())


if __name__ == "__main__":
    unittest.main()
