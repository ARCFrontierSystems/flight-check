"""Hardening checks for the 1.5 additions: large repositories, tampered run files, and broken file lists (Phase 9)."""

import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import constants, freshness, state  # noqa: E402
from test_freshness import T2, T3, ProjectTestCase, privacy_part  # noqa: E402
from test_state import run_cli  # noqa: E402


class LargeFileTests(ProjectTestCase):
    def setUp(self):
        patcher = mock.patch.object(constants, "MAX_HASHED_FILE_BYTES", 20)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_large_files_compare_by_size_alike_everywhere(self):
        st = state.load(self.sd, root=self.root)
        entry = next(e for e in st.ledger["entries"].values() if e["rule"] == "application-security.sql-injection")
        self.assertTrue(entry["evidence_files"]["src/store.py"].startswith("size:"))
        out = self.changes()
        self.assertEqual((out["changed_files"], out["stale_evidence"]), (0, []))
        self.assertIn("compared by size only", " ".join(out["reasons"]))
        self.edit("src/store.py")
        out = self.changes()
        self.assertIn({"kind": "control", "id": "APPSEC-SECRETS", "state": "stale"}, out["stale_evidence"])


class BrokenInputTests(ProjectTestCase):
    def start_changed(self, now=T2):
        code, out = run_cli("init-run", "--base", os.path.join(self.sd, "runs"), "--root", self.root, "--changed", "--now", now)
        self.assertEqual(code, 0, out)
        helpers.write_run(parts=[privacy_part()], base=os.path.join(self.sd, "runs"), run=dict(helpers.base_run(), run_id=out["run_id"]))
        return out["run_dir"]

    def finalize_run(self, run_dir, now=T2):
        return run_cli("finalize", run_dir, "--root", self.root, "--ledger", self.led, "--now", now)

    def test_malformed_scope_stops_finalize_and_writes_nothing(self):
        self.edit("docs/PRIVACY.md")
        run_dir = self.start_changed()
        with open(os.path.join(run_dir, "scope.json"), "w") as fh:
            json.dump({"mode": "changed", "carried_forward_domains": ["no-such-domain"]}, fh)
        revision = state.load(self.sd).project["revision"]
        code, out = self.finalize_run(run_dir)
        self.assertEqual(code, 1)
        self.assertIn("not a valid run scope", out["error"])
        self.assertFalse(os.path.exists(os.path.join(run_dir, "audit.final.json")))
        self.assertEqual(state.load(self.sd).project["revision"], revision)

    def test_tampered_scope_cannot_carry_an_affected_domain(self):
        self.edit("src/store.py")  # affects application-security
        run_dir = self.start_changed()
        path = os.path.join(run_dir, "scope.json")
        with open(path) as fh:
            scope = json.load(fh)
        scope["carried_forward_domains"] = ["application-security"]
        with open(path, "w") as fh:
            json.dump(scope, fh)
        with open(os.path.join(run_dir, "files.json"), "w") as fh:
            json.dump({"files": ["not", "a", "map"]}, fh)  # ignored: finalize scans instead
        code, out = self.finalize_run(run_dir)
        self.assertNotEqual(code, 1, out)
        with open(os.path.join(run_dir, "audit.final.json")) as fh:
            cov = {c["domain"]: c["status"] for c in json.load(fh)["coverage"]}
        self.assertEqual(cov["application-security"], "NOT_ASSESSED")
        self.assertEqual(self.changes()["changed_files"], 0)

    def test_corrupt_or_linked_file_list_widens(self):
        manifest = os.path.join(self.sd, "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("{not json")
        out = self.changes()
        self.assertTrue(out["widened"])
        self.assertEqual(out["baseline"]["kind"], "cited files only")
        os.unlink(manifest)
        os.symlink(os.path.join(self.tmp, "elsewhere.json"), manifest)
        self.assertTrue(self.changes()["widened"])

    def test_stopped_scan_widens(self):
        st = state.load(self.sd, root=self.root)
        out = freshness.changes(self.root, st, max_files=1)
        self.assertTrue(out["widened"])
        self.assertIn("stopped after 1 files", out["reasons"][0])


if __name__ == "__main__":
    unittest.main()
