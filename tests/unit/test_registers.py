"""Requirements, decisions, and risks (Phase 5)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import catalog, minischema, registers, state  # noqa: E402
from test_state import run_cli  # noqa: E402

T1, T2, T3, T4 = "2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z", "2026-10-13T12:00:00Z", "2026-10-13T13:00:00Z"


def fresh():
    proj = state.new_project(state.new_identity(None, "p", T1))
    regs = state.new_registers()
    led = {"entries": {"fp-abc": {"id": "FC-0001", "status": "OPEN"}}}
    return proj, regs, led


def valid(regs):
    errs = minischema.validate_def(json.loads(json.dumps(regs)), catalog.schema(), "registers")
    return errs + state.semantic_errors(None, regs)


REQ = {"title": "Account deletion", "description": "Deleting an account removes personal data within 30 days.",
       "category": "data-lifecycle", "criticality": "release-critical"}
DEC = {"title": "Hosted checkout", "context": "Card data must stay off our servers.", "decision": "Use a hosted page.",
       "assertions": ["No card number input is rendered by the application."]}
RISK = {"title": "Webhook replay", "description": "Replayed events could issue duplicate tickets.",
        "domain": "payments", "severity": "CRITICAL"}


class RecordRuleTests(unittest.TestCase):
    def test_user_records_versus_flight_check_suggestions(self):
        p, r, led = fresh()
        mine = registers.add("requirement", p, r, led, REQ, "user", T1, by="owner")
        suggested = registers.add("requirement", p, r, led, dict(REQ, title="Export data"), "flight-check", T1)
        self.assertEqual((mine["id"], mine["status"], mine["origin"]), ("REQ-0001", "ACCEPTED", "declared"))
        self.assertEqual((suggested["status"], suggested["origin"]), ("PROPOSED", "inferred"))
        self.assertEqual(valid(r), [])
        with self.assertRaises(registers.RegisterError):
            registers.add("requirement", p, r, led, {"title": "x"}, "user", T1)

    def test_who_may_change_what(self):
        p, r, led = fresh()
        req = registers.add("requirement", p, r, led, REQ, "flight-check", T1)
        with self.assertRaisesRegex(registers.RegisterError, "only user"):
            registers.update(p, r, led, req["id"], "flight-check", T2, status="ACCEPTED")
        with self.assertRaisesRegex(registers.RegisterError, "evidence"):
            registers.update(p, r, led, req["id"], "user", T2, status="VERIFIED")
        registers.update(p, r, led, req["id"], "user", T2, status="ACCEPTED", note="agreed")
        registers.update(p, r, led, req["id"], "flight-check", T3, status="IMPLEMENTED")
        self.assertEqual(req["verification"], {"state": "unverified"})
        self.assertEqual([h["status"] for h in req["history"]], ["PROPOSED", "ACCEPTED", "IMPLEMENTED"])
        dec = registers.add("decision", p, r, led, DEC, "user", T1)
        with self.assertRaisesRegex(registers.RegisterError, "superseded-by"):
            registers.update(p, r, led, dec["id"], "user", T2, status="SUPERSEDED")
        new = registers.add("decision", p, r, led, dict(DEC, title="Embedded checkout"), "user", T2)
        registers.update(p, r, led, dec["id"], "user", T2, status="SUPERSEDED", superseded_by=new["id"])
        self.assertEqual(dec["superseded_by"], new["id"])
        registers.update(p, r, led, new["id"], "flight-check", T3, status="NEEDS_REVIEW", note="trigger changed")
        with self.assertRaises(registers.RegisterError):
            registers.update(p, r, led, new["id"], "flight-check", T3, status="ACCEPTED")
        self.assertEqual(valid(r), [])

    def test_risk_acceptance_keeps_severity_and_needs_a_full_record(self):
        p, r, led = fresh()
        risk = registers.add("risk", p, r, led, RISK, "user", T1)
        with self.assertRaisesRegex(registers.RegisterError, "risk accept"):
            registers.update(p, r, led, risk["id"], "user", T2, status="ACCEPTED")
        with self.assertRaisesRegex(registers.RegisterError, "owner"):
            registers.accept_risk(r, risk["id"], {"reason": "provider dedupes", "owner": " ", "date": "2026-10-13",
                                                  "scope": "release", "compensating_controls": "reconciliation"}, T2)
        registers.accept_risk(r, risk["id"], {"reason": "provider dedupes", "owner": "owner", "date": "2026-10-13",
                                              "scope": "release", "compensating_controls": "reconciliation"}, T2)
        self.assertEqual((risk["status"], risk["severity"]), ("ACCEPTED", "CRITICAL"))
        with self.assertRaisesRegex(registers.RegisterError, "note"):
            registers.update(p, r, led, risk["id"], "user", T3, status="RESOLVED")
        self.assertEqual(valid(r), [])

    def test_links_must_point_at_real_records(self):
        p, r, led = fresh()
        dec = registers.add("decision", p, r, led, DEC, "user", T1)
        req = registers.add("requirement", p, r, led, REQ, "user", T1, links=["FC-0001,%s" % dec["id"], "control:PRIV-DELETION"])
        self.assertEqual(req["links"], {"findings": ["FC-0001"], "decisions": [dec["id"]]})
        self.assertEqual(req["controls"], ["PRIV-DELETION"])
        self.assertEqual(registers.reverse_links(r, dec["id"]), [req["id"]])
        for bad in (["FC-0099"], ["DEC-0042"], ["control:NOPE"], ["../x"]):
            with self.assertRaises(registers.RegisterError):
                registers.add("requirement", p, r, led, REQ, "user", T1, links=bad)

    def test_text_is_masked(self):
        p, r, led = fresh()
        rec = registers.add("decision", p, r, led, dict(DEC, rationale="token ghp_" + "a1B2" * 9), "user", T1)
        self.assertNotIn("a1B2a1B2", rec["rationale"])


class AssessmentTests(unittest.TestCase):
    def test_requirement_verified_only_by_evidence_and_risks_follow_findings(self):
        p, r, led = fresh()
        ok = registers.add("requirement", p, r, led, REQ, "user", T1, links=["control:APPSEC-SECRETS"])
        bad = registers.add("requirement", p, r, led, dict(REQ, title="No injection"), "user", T1, links=["control:APPSEC-INJECTION"])
        risk = registers.add("risk", p, r, led, RISK, "user", T1, links=["FC-0001"])
        final = {"controls": [{"id": "APPSEC-SECRETS", "state": "VERIFIED", "evidence_check": "PASSED"},
                              {"id": "APPSEC-INJECTION", "state": "NOT_MET"}]}
        changes = registers.apply_assessment(r, final, registers.entries_by_id(led), T2, "run1")
        self.assertEqual((ok["status"], ok["verification"]["state"]), ("VERIFIED", "current"))
        self.assertEqual((bad["status"], bad["verification"]["state"]), ("ACCEPTED", "unverified"))
        self.assertIn((ok["id"], {"status": ["ACCEPTED", "VERIFIED"]}), [(c[0], c[1]) for c in changes])
        self.assertEqual(risk["status"], "OPEN")
        # The control fails its evidence check later: the requirement is no longer verified.
        final["controls"][0]["evidence_check"] = "FAILED"
        registers.apply_assessment(r, final, registers.entries_by_id(led), T3, "run2")
        self.assertEqual(ok["status"], "IMPLEMENTED")
        # Linked findings verified fixed mitigate the risk; a regression reopens it.
        led["entries"]["fp-abc"]["status"] = "VERIFIED"
        registers.apply_assessment(r, final, registers.entries_by_id(led), T3, "run2")
        self.assertEqual(risk["status"], "MITIGATED")
        led["entries"]["fp-abc"]["status"] = "REGRESSION"
        registers.apply_assessment(r, final, registers.entries_by_id(led), T4, "run3")
        self.assertEqual(risk["status"], "REOPENED")
        self.assertEqual(valid(r), [])

    def test_accepted_risk_is_not_auto_mitigated(self):
        p, r, led = fresh()
        risk = registers.add("risk", p, r, led, RISK, "user", T1, links=["FC-0001"])
        registers.accept_risk(r, risk["id"], {"reason": "x" * 6, "owner": "owner", "date": "2026-10-13",
                                              "scope": "release", "compensating_controls": "monitoring"}, T2)
        led["entries"]["fp-abc"]["status"] = "VERIFIED"
        registers.apply_assessment(r, {"controls": []}, registers.entries_by_id(led), T3, "run")
        self.assertEqual(risk["status"], "ACCEPTED")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flight-check-reg-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.led = os.path.join(self.tmp, "ledger.json")
        run_dir = helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())], base=self.tmp)
        code, out = run_cli("finalize", run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", self.led, "--now", T1)
        self.assertEqual(code, 0, out)

    def cli(self, *argv):
        try:
            return run_cli(*argv, "--state-dir", self.tmp)
        except SystemExit as exc:  # argparse rejected the arguments
            return exc.code, None

    def test_full_cycle(self):
        code, out = self.cli("req", "add", "--title", "No secrets in source", "--description", "Credentials stay out of the repository.",
                             "--category", "security", "--criticality", "release-critical", "--link", "control:APPSEC-SECRETS",
                             "--by", "owner", "--now", T2)
        self.assertEqual((code, out["id"], out["status"]), (0, "REQ-0001", "ACCEPTED"), out)
        code, out = self.cli("decision", "add", "--title", "Parameterized queries only", "--context", "SQL injection risk",
                             "--decision", "All SQL uses bound parameters.", "--assertion", "No SQL is built by string concatenation.",
                             "--now", T2)
        self.assertEqual(code, 0, out)
        code, out = self.cli("decision", "assertions")
        self.assertEqual([a["decision"] for a in out["assertions"]], ["DEC-0001"])
        code, out = self.cli("risk", "add", "--from-finding", "FC-0001", "--now", T2)
        self.assertEqual(code, 0, out)
        code, out = self.cli("risk", "show", "--id", "RISK-0001")
        self.assertEqual((out["record"]["severity"], out["record"]["links"]["findings"]), ("HIGH", ["FC-0001"]))
        code, out = self.cli("risk", "accept", "--id", "RISK-0001", "--reason", "internal tool only", "--owner", "owner",
                             "--date", "2026-10-13", "--scope", "this release", "--compensating-controls", "network isolation",
                             "--now", T3)
        self.assertEqual((code, out["severity"]), (0, "HIGH"), out)
        code, out = self.cli("req", "update", "--id", "REQ-0001", "--status", "VERIFIED", "--now", T3)
        self.assertEqual(code, 1)
        self.assertIn("evidence", out["error"])
        # The next audit verifies the linked control with checked evidence.
        run_dir = helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())], base=self.tmp,
                                    run=dict(helpers.base_run(), run_id="20261013T140000Z"))
        code, out = run_cli("finalize", run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", self.led, "--now", T4)
        self.assertEqual(code, 0, out)
        code, out = self.cli("req", "list")
        self.assertEqual((out["requirements"][0]["status"], out["requirements"][0]["verification"]), ("VERIFIED", "current"))
        st = state.load(self.tmp)
        verified = [e for e in st.events if e["record"] == "REQ-0001" and e["actor"] == "flight-check"]
        self.assertEqual(verified[0]["change"], {"status": ["ACCEPTED", "VERIFIED"]})
        # 1.0 ledger commands on a finding log the change they made.
        code, out = run_cli("ledger", "legal", "--ledger", self.led, "--id", "FC-0002", "--status", "LEGAL_REVIEW_REQUESTED",
                            "--source", "user", "--note", "sent to counsel", "--now", T4)
        self.assertEqual(code, 0, out)
        last = state.load(self.tmp).events[-1]
        self.assertEqual((last["record"], last["change"]), ("LR-0001", {"status": ["NEEDS_REVIEW", "AWAITING_COUNSEL"]}))
        self.assertEqual(run_cli("state", "check", "--state-dir", self.tmp)[0], 0)

    def test_bad_input_is_refused_without_writing(self):
        before = state.load(self.tmp).project["revision"]
        for argv in (("req", "add", "--title", "x"), ("risk", "add", "--title", "t", "--description", "d", "--domain", "nope",
                                                      "--severity", "HIGH"),
                     ("req", "add", "--title", "Title", "--description", "Description", "--category", "security",
                      "--criticality", "high", "--link", "FC-0999")):
            code, out = self.cli(*argv)
            self.assertNotEqual(code, 0, argv)
        self.assertEqual(state.load(self.tmp).project["revision"], before)


if __name__ == "__main__":
    unittest.main()
