"""Defects found by the V1.5 review in state handling: recovery, import, restore, locks, and backups (Phase 10)."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import lockfile, registers, state  # noqa: E402
from test_state import run_cli  # noqa: E402

T1, T2, T3 = "2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z", "2026-10-13T12:00:00Z"


class StateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flight-check-harden-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sd = os.path.join(self.tmp, "state")
        state.init(self.sd, None, T1, "p")

    def add_req(self, title, now):
        with state.transaction(self.sd, "user", "req add", now) as tx:
            return registers.add("requirement", tx.project, tx.registers, tx.ledger,
                                 {"title": title, "description": "A requirement for this test.", "category": "security",
                                  "criticality": "normal"}, "user", now)["id"]

    def plant_journal(self, journal, copies=()):
        pending = os.path.join(self.sd, "pending")
        os.makedirs(pending)
        for name, text in copies:
            with open(os.path.join(pending, name), "w") as fh:
                fh.write(text)
        with open(os.path.join(pending, "journal.json"), "w") as fh:
            json.dump(journal, fh)


class RecoverTests(StateCase):
    def test_planted_journal_cannot_reach_files_outside_the_state(self):
        victim = os.path.join(self.tmp, "victim.txt")
        with open(victim, "w") as fh:
            fh.write("keep me")
        self.plant_journal({"files": ["../victim.txt", "/etc/hostname"], "existed": [], "new_head_seq": 99})
        with self.assertRaises(state.StateError):
            state.recover(self.sd)
        self.assertTrue(os.path.exists(victim))
        self.assertTrue(os.path.isdir(os.path.join(self.sd, "pending")))  # left for the user to inspect

    def test_matching_seq_with_a_different_head_is_rolled_back(self):
        head = state.load(self.sd).events[-1]
        with open(os.path.join(self.sd, "project.json")) as fh:
            before = fh.read()
        self.plant_journal({"files": ["project.json"], "existed": ["project.json"], "new_head_seq": head["seq"],
                            "new_head_hash": "0" * 64}, copies=[("project.json", before)])
        self.assertEqual(state.recover(self.sd)["action"], "rolled_back")


class ImportRestoreTests(StateCase):
    def test_edited_summary_fields_are_refused(self):
        out = os.path.join(self.tmp, "bundle.json")
        state.export_bundle(self.sd, out, T2)
        with open(out) as fh:
            bundle = json.load(fh)
        bundle["project_id"] = "f" * 32
        with open(out, "w") as fh:
            json.dump(bundle, fh)
        other = os.path.join(self.tmp, "other")
        with self.assertRaises(state.StateError):
            state.import_bundle(other, out, now_iso=T3)

    def test_restore_never_reuses_ids_and_adopt_restores_a_foreign_backup(self):
        where = state.backup(state.load(self.sd), "test", T1)
        self.assertEqual(self.add_req("Encrypt backups", T2), "REQ-0001")
        state.restore(self.sd, where, now_iso=T2)
        self.assertEqual(self.add_req("Rate limit login", T3), "REQ-0002")
        foreign = os.path.join(self.tmp, "foreign")
        state.init(foreign, None, T1, "q")
        theirs = state.backup(state.load(foreign), "test", T1)
        mine = os.path.join(self.sd, "backups", os.path.basename(theirs) + "-x")
        shutil.copytree(theirs, mine)
        with self.assertRaises(state.StateError):
            state.restore(self.sd, mine, now_iso=T3)
        state.restore(self.sd, mine, now_iso=T3, adopt_other=True)

    def test_damaged_backups_are_refused_without_a_traceback(self):
        where = state.backup(state.load(self.sd), "test", T1)
        os.unlink(os.path.join(where, "registers.json"))
        code, out = run_cli("state", "restore", "--state-dir", self.sd, "--from", where)
        self.assertEqual(code, 1)
        self.assertIn("missing", out["error"])
        where = state.backup(state.load(self.sd), "test2", T2)
        with open(os.path.join(where, "extra.json"), "w") as fh:
            fh.write("{}")
        with self.assertRaises(state.StateError):
            state.restore(self.sd, where, now_iso=T3)


class LockAndLinkTests(StateCase):
    def test_dangling_lock_link_fails_fast(self):
        os.symlink(os.path.join(self.tmp, "nowhere"), lockfile.lock_path(self.sd))
        started = time.monotonic()
        with self.assertRaises(lockfile.LockError):
            with lockfile.locked(self.sd, wait=1):
                pass
        self.assertLess(time.monotonic() - started, 5)

    def test_backups_are_never_written_through_a_link(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        os.symlink(elsewhere, os.path.join(self.sd, "backups"))
        with self.assertRaises(state.StateError):
            state.backup(state.load(self.sd), "test", T2)
        self.assertEqual(os.listdir(elsewhere), [])


class FirstAuditTests(unittest.TestCase):
    def test_new_project_is_not_logged_as_a_migration(self):
        tmp = tempfile.mkdtemp(prefix="flight-check-first-")
        self.addCleanup(shutil.rmtree, tmp, True)
        led = os.path.join(tmp, ".flight-check", "ledger.json")
        run_dir = helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())], base=tmp)
        code, out = run_cli("finalize", run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", led, "--now", T1)
        self.assertEqual(code, 0, out)
        first = state.load(os.path.dirname(led)).events[0]
        self.assertEqual(first["reason"], "Created project state.")
        self.assertFalse(os.path.exists(os.path.join(tmp, ".flight-check", "backups")))


if __name__ == "__main__":
    unittest.main()
