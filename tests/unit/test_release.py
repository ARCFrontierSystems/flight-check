"""Release check for CI: the latest ship decision must be ready and still describe the tracked files."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import release  # noqa: E402
from test_freshness import T1, T2, T3  # noqa: E402
from test_state import run_cli  # noqa: E402

GIT = shutil.which("git")


def git(root, *argv):
    subprocess.run([GIT, "-c", "core.autocrlf=false", "-C", root] + list(argv), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@unittest.skipUnless(GIT, "git is not installed")
class GitIndexTests(unittest.TestCase):
    def test_reads_index_versions_2_3_and_4(self):
        root = tempfile.mkdtemp(prefix="flight-check-index-")
        self.addCleanup(shutil.rmtree, root, True)
        for rel in ("a.txt", "src/b.py", "src/deep/c.md"):
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            with open(os.path.join(root, rel), "w") as fh:
                fh.write(rel)
        git(root, "init", "-q")
        git(root, "add", "-A")
        expected = {"a.txt", "src/b.py", "src/deep/c.md"}
        self.assertEqual(release.tracked_paths(root), expected)
        with open(os.path.join(root, "new.txt"), "w") as fh:
            fh.write("x")
        git(root, "add", "-N", "new.txt")  # intent-to-add sets extended flags (index version 3)
        self.assertEqual(release.tracked_paths(root), expected | {"new.txt"})
        git(root, "update-index", "--index-version", "4")
        self.assertEqual(release.tracked_paths(root), expected | {"new.txt"})
        self.assertEqual(release.tracked_paths(os.path.join(root, "src")), {"b.py", "deep/c.md"})  # audit root in a subfolder
        self.assertIsNone(release.tracked_paths(tempfile.gettempdir()))  # no repository


@unittest.skipUnless(GIT, "git is not installed")
class ReleaseCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flight-check-release-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "proj")
        shutil.copytree(helpers.FIXTURE_ROOT, self.root)
        self.sd = os.path.join(self.root, ".flight-check")
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")

    def finalize(self, run_id, now, findings=True, started=None):
        part = helpers.all_controls_resolved(helpers.base_part())
        if not findings:
            part["findings"] = []
            proof = next(c for c in part["controls"] if c["id"] == "APPSEC-SECRETS")
            for c in part["controls"]:
                if c["state"] == "NOT_MET":
                    c.update(state="VERIFIED", related_findings=[], rationale="Fixed.", evidence=proof["evidence"])
        run_dir = helpers.write_run(parts=[part], base=os.path.join(self.tmp, "runs"), run=dict(helpers.base_run(), run_id=run_id, **({"started_at": started} if started else {})))
        code, out = run_cli("finalize", run_dir, "--root", self.root, "--ledger", os.path.join(self.sd, "ledger.json"), "--now", now)
        self.assertEqual(code, 0, out)
        return out

    def check(self, *extra):
        return run_cli("release-check", "--root", self.root, "--json", "--now", T3, *extra)

    def write(self, rel, data):
        with open(os.path.join(self.root, rel), "wb") as fh:
            fh.write(data)

    def test_ready_decision_passes_until_tracked_files_differ(self):
        self.assertEqual(self.finalize("20261013T100000Z", T1, findings=False)["gate"], "READY FOR RELEASE")
        code, out = self.check()
        self.assertEqual((code, out["ok"]), (0, True), out)
        # Local files git does not track, and line-end conversion of a checkout, do not count as changes.
        self.write("notes.local", b"scratch")
        with open(os.path.join(self.root, "src/store.py"), "rb") as fh:
            original = fh.read()
        self.write("src/store.py", original.replace(b"\n", b"\r\n"))
        self.assertEqual(self.check()[0], 0)
        self.write("src/store.py", original + b"\n# edited\n")
        os.remove(os.path.join(self.root, "README.md"))
        self.write("src/new.py", b"x = 1\n")
        git(self.root, "add", "src/new.py")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(out["files"], {"added": ["src/new.py"], "changed": ["src/store.py"], "deleted": ["README.md"]})
        self.assertEqual(self.check("--mode", "warn")[0], 0)  # reported, not blocking

    def test_decision_records_and_file_record_must_hold(self):
        self.finalize("20261013T100000Z", T1)
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", " ".join(out["problems"]))
        for fid in ("FC-0001", "FC-0002", "APPSEC-INJECTION", "PRIV-DELETION"):
            code, out = run_cli("ledger", "accept", "--ledger", os.path.join(self.sd, "ledger.json"), "--ref", fid,
                                "--risk", "Known issue", "--reason", "Internal tool behind a VPN", "--owner", "owner",
                                "--date", "2026-10-13", "--scope", "this release", "--compensating-controls", "VPN", "--now", T1)
            self.assertEqual(code, 0, out)
        self.assertEqual(self.finalize("20261013T110000Z", T2, started=T2)["gate"], "READY WITH ACCEPTED RISKS")
        self.assertEqual(self.check()[0], 1)
        self.assertEqual(self.check("--allow-accepted-risks")[0], 0)
        # A record changed after the decision makes it out of date.
        code, out = run_cli("req", "add", "--root", self.root, "--title", "Sessions expire", "--description",
                            "Sessions expire after 30 minutes idle.", "--category", "security", "--criticality", "normal", "--now", T2)
        self.assertEqual(code, 0, out)
        code, out = self.check("--allow-accepted-risks")
        self.assertEqual(code, 1)
        self.assertIn("records changed after the latest decision", " ".join(out["problems"]))
        os.remove(os.path.join(self.sd, release.AUDITED_FILES))
        self.assertIn("no record of the audited files", " ".join(self.check("--allow-accepted-risks")[1]["problems"]))

    def test_run_record_is_taken_as_the_run_starts(self):
        out = run_cli("init-run", "--base", os.path.join(self.sd, "runs"), "--root", self.root, "--now", T1)[1]
        rec = release._load(os.path.join(out["run_dir"], release.AUDITED_FILES))
        self.assertTrue(rec["available"])
        self.assertIn("src/store.py", rec["files"])
        self.assertFalse(any(p.startswith(".flight-check/") for p in rec["files"]))


if __name__ == "__main__":
    unittest.main()
