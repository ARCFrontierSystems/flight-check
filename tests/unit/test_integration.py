"""End-to-end 1.5 workflow through the command line (Phase 10, plan section 39):
baseline audit -> records -> change -> reassessment of the affected areas -> history -> updated readiness."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import catalog, constants  # noqa: E402
from test_state import run_cli  # noqa: E402

T = ["2026-10-13T%02d:00:00Z" % h for h in range(8, 20)]
POLICY = ("# Privacy\n\nWhen you delete your account we mark it deleted; an administrator removes the data on request.\n"
          "We do not share personal data with third parties.\n")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flight-check-flow-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "project")
        shutil.copytree(helpers.FIXTURE_ROOT, self.root)
        self.sd = os.path.join(self.root, ".flight-check")
        self.base = os.path.join(self.sd, "runs")
        self.led = os.path.join(self.sd, "ledger.json")

    def ok(self, *argv):
        code, out = run_cli(*argv)
        self.assertEqual(code, 0, (argv, out))
        return out

    def audit(self, now, parts, changed=False):
        argv = ["init-run", "--base", self.base, "--root", self.root, "--now", now] + (["--changed"] if changed else [])
        start = self.ok(*argv)
        helpers.write_run(parts=parts, base=self.base, run=dict(helpers.base_run(), run_id=start["run_id"], started_at=now))
        code, res = run_cli("finalize", start["run_dir"], "--root", self.root, "--ledger", self.led, "--now", now)
        self.assertIn(code, (0, 2, 3, 4, 5), res)
        with open(os.path.join(start["run_dir"], "audit.final.json")) as fh:
            return start, res, json.load(fh)

    def test_baseline_records_change_reassess_report(self):
        # 1-5. Baseline audit: profile, applicability, findings, state.
        start, res, final = self.audit(T[0], [helpers.all_controls_resolved(helpers.base_part())])
        self.assertNotIn("scope", start)
        profile = self.ok("report", "--view", "profile", "--root", self.root, "--format", "json")["data"]
        self.assertTrue(any(f["aspect"] == "language" for f in profile["facts"]))
        apps = {a["domain"]: a["effective"] for a in profile["applicability"]}
        self.assertEqual((apps["application-security"], apps["payments"]), ("applicable", "not_applicable"))
        ids = {f["rule"]: f["id"] for f in final["findings"]}
        sql, deletion = ids["application-security.sql-injection"], ids["privacy.deletion-contradicts-policy"]

        # 6-9. Records: a requirement, a decision, a risk, and a legal question.
        sd = ("--root", self.root)
        req = self.ok("req", "add", *sd, "--title", "No credentials in source", "--description", "Secrets stay out of the repository.",
                      "--category", "security", "--criticality", "release-critical", "--link", "control:APPSEC-SECRETS", "--now", T[1])
        dec = self.ok("decision", "add", *sd, "--title", "Privacy policy matches deletion", "--context", "Policy promises deletion",
                      "--decision", "The policy describes what deletion actually does.", "--review-trigger", "docs/",
                      "--assertion", "The privacy policy describes deletion as implemented.", "--now", T[1])
        risk = self.ok("risk", "add", *sd, "--from-finding", sql, "--now", T[1])
        lr = self.ok("legal", "add", *sd, "--title", "Data sharing statement", "--category", "Privacy",
                     "--description", "The policy says no data is shared; confirm processors are covered.",
                     "--trigger", "owner request", "--question", "Does the no-sharing statement need to name processors?", "--now", T[1])
        self.assertEqual((req["id"], dec["id"], risk["id"], lr["id"]), ("REQ-0001", "DEC-0001", "RISK-0001", "LR-0002"))
        # Negative: only evidence verifies a requirement; only the user closes a legal question.
        self.assertEqual(run_cli("req", "update", *sd, "--id", "REQ-0001", "--status", "VERIFIED", "--now", T[1])[0], 1)
        self.assertEqual(run_cli("legal", "update", *sd, "--id", "LR-0002", "--status", "RESOLVED", "--source", "flight-check",
                                 "--note", "automatic", "--now", T[1])[0], 1)

        # Nothing changed yet: an audit of changes has nothing to do.
        self.assertTrue(self.ok("init-run", "--base", self.base, "--root", self.root, "--changed", "--now", T[2])["nothing_to_reassess"])

        # 10. Change the privacy policy.
        with open(os.path.join(self.root, "docs", "PRIVACY.md"), "w") as fh:
            fh.write(POLICY)
        ch = self.ok("changes", *sd)
        self.assertEqual(ch["files"], [{"path": "docs/PRIVACY.md", "change": "modified"}])
        self.assertEqual((ch["reassess_domains"], ch["carried_forward_domains"]), (["privacy"], ["application-security"]))
        self.assertEqual(ch["decisions_to_review"][0]["decision"], "DEC-0001")
        self.assertTrue(self.ok("status", *sd, "--format", "json")["data"]["readiness"]["historical"])

        # 11-12. Reassess only the affected domain; the policy now matches the code, so the deletion finding is gone.
        part = helpers.base_part(findings=[])
        part.update(agent="data", positive_controls=[], coverage=[c for c in part["coverage"] if c["domain"] == "privacy"])
        part["controls"] = [
            {"id": "PRIV-DELETION", "state": "UNVERIFIED", "missing_evidence": "Data is removed only on request; no automatic purge."},
            {"id": "PRIV-DISCLOSURE-CONSISTENCY", "state": "VERIFIED", "rationale": "The policy describes the implemented deletion.",
             "evidence": [{"kind": "documentation", "path": "docs/PRIVACY.md", "start_line": 3, "end_line": 3,
                           "quote": POLICY.splitlines()[2]}]}]
        for c in catalog.controls():
            if c["domain"] == "privacy" and c["id"] not in ("PRIV-DELETION", "PRIV-DISCLOSURE-CONSISTENCY"):
                part["controls"].append({"id": c["id"], "state": "NOT_APPLICABLE", "rationale": "Fixture has no such feature.",
                                         "evidence": [{"kind": "absence", "searched": "Glob ** across the fixture"}]})
        start, res, final = self.audit(T[3], [part], changed=True)
        self.assertEqual(start["scope"]["agents"], ["data"])
        cov = {c["domain"]: c["status"] for c in final["coverage"]}
        self.assertEqual((cov["privacy"], cov["application-security"]), ("ASSESSED", constants.CARRIED_FORWARD))
        self.assertIn(deletion, final["not_reproduced"])
        self.assertEqual([f["id"] for f in final["carried_forward_findings"]], [sql])
        gate = final["gate"]
        self.assertEqual(gate["decision"], constants.GATE_INSUFFICIENT)
        self.assertIn(sql, gate["blocking_findings"])
        reasons = " ".join(gate["reasons"])
        self.assertIn("PRIV-DELETION", reasons)
        self.assertIn("REQ-0001", reasons)  # a carried-forward control never verifies a requirement for the first time

        # 13. Evidence is current again against the new baseline.
        ch = self.ok("changes", *sd)
        self.assertEqual((ch["changed_files"], ch["reassess_domains"]), (0, []))
        # The finding that is gone no longer counts as stale; the open legal question it raised still cites the old text.
        self.assertEqual(ch["stale_evidence"], [{"kind": "legal", "id": "LR-0001", "state": "stale"}])

        # 14. History is preserved: nothing was rewritten, and the legal question raised by the finding stays open.
        hist = self.ok("report", "--view", "findings", "--id", deletion, *sd, "--format", "json")["data"]["history"]
        self.assertEqual([h["status"] for h in hist][-1], "NOT_REPRODUCED")
        self.assertEqual(hist[0]["status"], "OPEN")
        legal = {r["id"]: r["status"] for r in self.ok("report", "--view", "legal", *sd, "--format", "json")["data"]["records"]}
        self.assertEqual(legal, {"LR-0001": "NEEDS_REVIEW", "LR-0002": "NEEDS_REVIEW"})
        self.assertEqual(self.ok("risk", "show", *sd, "--id", "RISK-0001")["record"]["status"], "OPEN")
        self.assertTrue(self.ok("state", "check", "--state-dir", self.sd)["ok"])

        # 15. Updated readiness: the latest decision is current, and both decisions are on record.
        status = self.ok("status", *sd, "--format", "json")["data"]
        self.assertEqual((status["readiness"]["latest"]["run_id"], status["readiness"]["historical"]), (start["run_id"], False))
        self.assertEqual(status["readiness"]["latest"]["decision"], constants.GATE_INSUFFICIENT)
        history = self.ok("report", "--view", "readiness", *sd, "--format", "json")["data"]["history"]
        self.assertEqual([h["decision"] for h in history], [constants.GATE_NOT_READY, constants.GATE_INSUFFICIENT])

        # Failure cases: a finalized run cannot be finalized again, and its state is untouched.
        self.assertEqual(run_cli("finalize", start["run_dir"], "--root", self.root, "--ledger", self.led, "--now", T[4])[0], 1)
        self.assertEqual(len(self.ok("report", "--view", "readiness", *sd, "--format", "json")["data"]["history"]), 2)


if __name__ == "__main__":
    unittest.main()
