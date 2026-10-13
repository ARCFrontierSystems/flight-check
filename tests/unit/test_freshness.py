"""Change detection, evidence freshness, lifecycle additions, and new gate inputs (Phase 7a)."""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import assemble, catalog, constants, freshness, gate, ledger, registers, state  # noqa: E402
from test_state import run_cli  # noqa: E402

T1, T2, T3 = "2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z", "2026-10-13T12:00:00Z"


class ProjectTestCase(unittest.TestCase):
    """A writable copy of the fixture project, audited once."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flight-check-fresh-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "proj")
        shutil.copytree(helpers.FIXTURE_ROOT, self.root)
        self.sd = os.path.join(self.root, ".flight-check")
        self.led = os.path.join(self.sd, "ledger.json")
        self.finalize("20261013T100000Z", T1)

    def finalize(self, run_id, now):
        run_dir = helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())], base=os.path.join(self.tmp, "runs"),
                                    run=dict(helpers.base_run(), run_id=run_id))
        code, out = run_cli("finalize", run_dir, "--root", self.root, "--ledger", self.led, "--now", now)
        self.assertEqual(code, 0, out)
        return out

    def changes(self):
        code, out = run_cli("changes", "--root", self.root)
        self.assertEqual(code, 0, out)
        return out

    def edit(self, rel, text="\n# edited\n"):
        with open(os.path.join(self.root, rel), "a") as fh:
            fh.write(text)


class ChangeDetectionTests(ProjectTestCase):
    def test_finalize_records_hashes_and_stable_positive_controls(self):
        st = state.load(self.sd, root=self.root)
        self.assertTrue(os.path.isfile(os.path.join(self.sd, "manifest.json")))
        entry = next(e for e in st.ledger["entries"].values() if e["rule"] == "application-security.sql-injection")
        self.assertRegex(entry["evidence_files"]["src/store.py"], r"^[0-9a-f]{64}$")
        self.assertEqual([pc["id"] for pc in st.ledger["positive_controls"].values()], ["PC-0001"])
        self.assertEqual(st.project["controls"]["APPSEC-SECRETS"]["state"], "VERIFIED")
        self.finalize("20261013T110000Z", T2)
        st = state.load(self.sd, root=self.root)
        self.assertEqual([pc["id"] for pc in st.ledger["positive_controls"].values()], ["PC-0001"])

    def test_no_change(self):
        out = self.changes()
        self.assertEqual((out["changed_files"], out["reassess_domains"], out["readiness_is_historical"], out["widened"]),
                         (0, [], False, False))

    def test_changed_cited_source_file(self):
        self.edit("src/store.py")
        out = self.changes()
        self.assertEqual(out["files"], [{"path": "src/store.py", "change": "modified"}])
        kinds = {(a["kind"], a["id"]) for a in out["affected"]}
        self.assertIn(("control", "APPSEC-SECRETS"), kinds)
        self.assertTrue(any(k == "finding" for k, _ in kinds))
        self.assertIn("application-security", out["reassess_domains"])
        self.assertNotIn("accessibility", out["reassess_domains"])
        self.assertIn({"kind": "control", "id": "APPSEC-SECRETS", "state": "stale"}, out["stale_evidence"])
        self.assertFalse(out["widened"])
        self.assertTrue(out["readiness_is_historical"])
        self.assertTrue(set(out["carried_forward_domains"]).isdisjoint(out["reassess_domains"]))

    def test_unexplained_file_and_missing_manifest_widen(self):
        with open(os.path.join(self.root, "blob.xyz"), "w") as fh:
            fh.write("data")
        out = self.changes()
        self.assertTrue(out["widened"])
        self.assertIn("blob.xyz", " ".join(out["reasons"]))
        self.assertEqual(out["carried_forward_domains"], [])
        os.unlink(os.path.join(self.root, "blob.xyz"))
        os.unlink(os.path.join(self.sd, "manifest.json"))
        out = self.changes()
        self.assertTrue(out["widened"])
        self.assertEqual(out["baseline"]["kind"], "cited files only")

    def test_removed_file_makes_evidence_missing(self):
        os.unlink(os.path.join(self.root, "src", "store.py"))
        out = self.changes()
        self.assertIn({"path": "src/store.py", "change": "removed"}, out["files"])
        self.assertIn("missing", {s["state"] for s in out["stale_evidence"]})

    def test_decision_triggers_and_requirement_staleness(self):
        with state.transaction(self.sd, "user", "test", T2, root=self.root) as tx:
            registers.add("decision", tx.project, tx.registers, tx.ledger,
                          {"title": "Parameterized SQL only", "context": "Injection risk", "decision": "Bind all parameters.",
                           "review_triggers": ["src/"]}, "user", T2)
            req = registers.add("requirement", tx.project, tx.registers, tx.ledger,
                                {"title": "No secrets", "description": "Credentials stay out of source.", "category": "security",
                                 "criticality": "high"}, "user", T2, links=["control:APPSEC-SECRETS"])
            req["status"], req["verification"] = "VERIFIED", {"state": "current", "at": T2, "run_id": "r"}
        self.assertEqual(self.changes()["decisions_to_review"], [])
        self.edit("src/store.py")
        out = self.changes()
        self.assertEqual(out["decisions_to_review"][0]["decision"], "DEC-0001")
        self.assertIn({"kind": "requirement", "id": "REQ-0001", "state": "stale", "because": ["APPSEC-SECRETS"]}, out["stale_evidence"])

    def test_scan_skips_vcs_state_dependencies_and_symlinks(self):
        os.makedirs(os.path.join(self.root, "node_modules", "x"))
        with open(os.path.join(self.root, "node_modules", "x", "i.js"), "w") as fh:
            fh.write("x")
        os.symlink("/etc", os.path.join(self.root, "etc-link"))
        files, _ = freshness.scan(self.root)
        self.assertFalse(any(p.startswith((".flight-check/", "node_modules/", "etc-link")) for p in files))
        self.assertIn("src/store.py", files)

    def test_evidence_states_and_rules(self):
        cur = {"a.py": "1" * 64, "b.py": "2" * 64}
        self.assertEqual(freshness.evidence_state({}, cur), "unverified")
        self.assertEqual(freshness.evidence_state({"a.py": "1" * 64}, cur), "current")
        self.assertEqual(freshness.evidence_state({"a.py": "0" * 64}, cur), "stale")
        self.assertEqual(freshness.evidence_state({"c.py": "1" * 64, "a.py": "0" * 64}, cur), "missing")
        self.assertIn("supply-chain", freshness.domains_for_path("web/package-lock.json"))
        self.assertIn("accessibility", freshness.domains_for_path("app/templates/home.html"))
        self.assertIn("infrastructure", freshness.domains_for_path("deploy/nested/compose.yml"))
        self.assertIn("testing", freshness.domains_for_path("tests/unit/test_x.py"))
        self.assertEqual(freshness.domains_for_path("src/core/engine.zz"), set())


def privacy_part():
    """The data agent's part: privacy only, re-reporting the deletion finding."""
    part = helpers.base_part(findings=[helpers.deletion_finding()])
    part["agent"] = "data"
    part["coverage"] = [c for c in part["coverage"] if c["domain"] == "privacy"]
    part["controls"] = [c for c in part["controls"] if c["id"] == "PRIV-DELETION"]
    part["positive_controls"] = []
    for c in catalog.controls():
        if c["domain"] == "privacy" and c["id"] != "PRIV-DELETION":
            part["controls"].append({"id": c["id"], "state": "NOT_APPLICABLE", "rationale": "Fixture has no such feature.",
                                     "evidence": [{"kind": "absence", "searched": "Glob ** across the fixture"}]})
    return part


class IncrementalTests(ProjectTestCase):
    """init-run --changed and carrying forward the domains that no change affects (Phase 7b)."""

    def init_changed(self, now):
        code, out = run_cli("init-run", "--base", os.path.join(self.sd, "runs"), "--root", self.root, "--changed", "--now", now)
        self.assertEqual(code, 0, out)
        return out

    def run_changed(self, now, before_finalize=None):
        out = self.init_changed(now)
        helpers.write_run(parts=[privacy_part()], base=os.path.join(self.sd, "runs"), run=dict(helpers.base_run(), run_id=out["run_id"]))
        if before_finalize:
            before_finalize()
        code, res = run_cli("finalize", out["run_dir"], "--root", self.root, "--ledger", self.led, "--now", now)
        self.assertIn(code, (0, 2, 3, 4, 5), res)
        with open(os.path.join(out["run_dir"], "audit.final.json")) as fh:
            return out, res, json.load(fh)

    def test_nothing_changed_starts_no_run(self):
        out = self.init_changed(T2)
        self.assertTrue(out["nothing_to_reassess"])
        self.assertEqual(os.listdir(os.path.join(self.sd, "runs")) if os.path.isdir(os.path.join(self.sd, "runs")) else [], [])

    def test_unaffected_domain_is_carried_forward_and_still_counts(self):
        self.edit("docs/PRIVACY.md")
        scope = self.init_changed(T2)["scope"]
        self.assertEqual((scope["reassess_domains"], scope["carried_forward_domains"], scope["agents"]),
                         (["privacy"], ["application-security"], ["data"]))
        self.edit("docs/PRIVACY.md", "\n# edited again\n")  # the next init-run must see a fresh change
        out, res, final = self.run_changed(T3)
        cov = {c["domain"]: c for c in final["coverage"]}
        self.assertEqual(cov["application-security"]["status"], constants.CARRIED_FORWARD)
        self.assertEqual(cov["application-security"]["carried_forward"]["run_id"], "20261013T100000Z")
        self.assertEqual(cov["privacy"]["status"], "ASSESSED")
        secrets = next(c for c in final["controls"] if c["id"] == "APPSEC-SECRETS")
        self.assertEqual((secrets["state"], secrets["carried_forward"]["evidence"]), ("VERIFIED", "current"))
        carried = final["carried_forward_findings"]
        self.assertEqual([f["domain"] for f in carried], ["application-security"])
        self.assertEqual((carried[0]["severity"], carried[0]["effective_confidence"], carried[0]["status"]), ("HIGH", "CONFIRMED", "OPEN"))
        g = final["gate"]
        self.assertEqual(g["decision"], constants.GATE_NOT_READY)
        self.assertIn(carried[0]["id"], g["blocking_findings"])
        self.assertEqual(g["carried_forward_domains"], ["application-security"])
        self.assertFalse(any("application-security" in r for r in g["insufficient_evidence"]))
        self.assertIn(carried[0]["id"], final["lifecycle"]["carried_forward_open"])
        self.assertEqual(final["incremental"]["reassessed_domains"], ["privacy"])
        st = state.load(self.sd, root=self.root)
        self.assertEqual(st.ledger["entries"][next(k for k, e in st.ledger["entries"].items() if e["id"] == carried[0]["id"])]["status"], "OPEN")
        doms = st.project["domains"]
        self.assertEqual((doms["application-security"]["status"], doms["application-security"]["assessed_run_id"]),
                         (constants.CARRIED_FORWARD, "20261013T100000Z"))
        self.assertEqual(doms["privacy"]["assessed_run_id"], out["run_id"])
        self.assertEqual(st.project["controls"]["APPSEC-SECRETS"]["run_id"], "20261013T100000Z")
        self.assertEqual([pc["status"] for pc in st.ledger["positive_controls"].values()], ["observed"])
        code, rendered = run_cli("render", out["run_dir"], "--allow-missing-summary")
        self.assertEqual(code, 0, rendered)
        with open(os.path.join(out["run_dir"], "report.md")) as fh:
            report = fh.read()
        self.assertIn("CARRIED FORWARD (not reassessed in this run)", report)
        self.assertIn("VERIFIED (carried forward)", report)
        self.assertEqual(run_cli("state", "check", "--state-dir", self.sd)[0], 0)
        # Nothing changed since: the carried-forward chain holds, so there is nothing to reassess.
        self.assertTrue(self.init_changed("2026-10-13T13:00:00Z")["nothing_to_reassess"])

    def test_change_during_run_is_not_carried_forward(self):
        self.edit("docs/PRIVACY.md")
        out, res, final = self.run_changed(T2, before_finalize=lambda: self.edit("src/store.py"))
        cov = {c["domain"]: c["status"] for c in final["coverage"]}
        self.assertEqual(cov["application-security"], "NOT_ASSESSED")
        self.assertIn("Files changed after this run started", " ".join(final["validation"]["warnings"]))
        self.assertEqual(final["gate"]["decision"], constants.GATE_INSUFFICIENT)
        after = self.changes()
        self.assertIn({"path": "src/store.py", "change": "modified"}, after["files"])  # the snapshot is the new baseline
        self.assertIn("application-security", after["reassess_domains"])

    def test_carried_controls_are_rechecked_and_never_newly_verify(self):
        cov = {"carried_forward": {"run_id": "r1", "at": T1}}
        c = {"id": "APPSEC-SECRETS"}
        prior = {"state": "VERIFIED", "run_id": "r1", "at": T1, "evidence_files": {"a.py": "0" * 64}}
        self.assertEqual(assemble._carried_control(c, cov, prior, {"a.py": "1" * 64})["state"], "UNVERIFIED")
        self.assertEqual(assemble._carried_control(c, cov, prior, {})["state"], "UNVERIFIED")
        self.assertEqual(assemble._carried_control(c, cov, None, {})["state"], "UNVERIFIED")
        kept = assemble._carried_control(c, cov, prior, {"a.py": "0" * 64})
        self.assertEqual((kept["state"], kept["carried_forward"]["evidence"]), ("VERIFIED", "current"))
        regs = state.new_registers()
        regs["requirements"].append({"id": "REQ-0001", "title": "No secrets", "criticality": "high", "status": "IMPLEMENTED",
                                     "controls": ["APPSEC-SECRETS"], "links": {}, "history": []})
        self.assertEqual(registers.apply_assessment(regs, {"controls": [kept]}, {}, T2, "r2"), [])
        self.assertEqual(regs["requirements"][0]["status"], "IMPLEMENTED")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.led = ledger.new_ledger("t")
        self.audit = {"run": {"run_id": "r1"}, "findings": [helpers.sql_finding()],
                      "coverage": [{"domain": d, "status": "ASSESSED"} for d in catalog.domain_ids()]}
        ledger.merge(self.led, self.audit, T1, None)

    def test_user_triage_statuses(self):
        self.assertEqual(ledger.mark(self.led, "FC-0001", "UNDER_REVIEW", "checking with the team", T2), "OPEN")
        with self.assertRaises(ledger.LedgerError):
            ledger.mark(self.led, "FC-0001", "VERIFIED", "fixed it myself", T2)
        with self.assertRaises(ledger.LedgerError):
            ledger.mark(self.led, "FC-0001", "SUPERSEDED", "replaced", T2)
        with self.assertRaises(ledger.LedgerError):
            ledger.mark(self.led, "FC-0001", "FALSE_POSITIVE", "no", T2)
        ledger.mark(self.led, "FC-0001", "FALSE_POSITIVE", "the input is a constant, not user data", T2, by="owner")
        entry = ledger.find_entry(self.led, "FC-0001")[1]
        self.assertEqual((entry["status"], entry["history"][-1]["source"]), ("FALSE_POSITIVE", "user"))
        changes = ledger.merge(self.led, dict(self.audit, run={"run_id": "r2"}, findings=[helpers.sql_finding()]), T3, None)
        self.assertEqual(changes["false_positive_reobserved"], ["FC-0001"])
        self.assertEqual(entry["status"], "FALSE_POSITIVE")


def clean_audit(findings=()):
    controls = [{"id": c["id"], "state": "VERIFIED", "release_critical": c["release_critical"]} for c in catalog.controls()]
    return {"run": {"run_id": "20261013T120000Z", "started_at": T3}, "findings": list(findings), "controls": controls,
            "coverage": [{"domain": d, "status": "ASSESSED", "rationale": "x"} for d in catalog.domain_ids()]}


class GateInputTests(unittest.TestCase):
    def regs(self):
        return state.new_registers()

    def test_records_feed_the_gate(self):
        self.assertEqual(gate.decide(clean_audit(), None, None)["decision"], constants.GATE_READY)
        regs = self.regs()
        regs["requirements"].append({"id": "REQ-0001", "title": "Delete data", "criticality": "release-critical",
                                     "status": "ACCEPTED", "controls": []})
        d = gate.decide(clean_audit(), None, None, registers=regs)
        self.assertEqual(d["decision"], constants.GATE_INSUFFICIENT)
        self.assertIn("REQ-0001", " ".join(d["reasons"]))
        regs = self.regs()
        regs["legal"].append({"id": "LR-0001", "title": "Refund terms", "status": "ACTION_REQUIRED"})
        self.assertEqual(gate.decide(clean_audit(), None, None, registers=regs)["decision"], constants.GATE_NOT_READY)
        regs = self.regs()
        risk = {"id": "RISK-0001", "title": "Bulk refund job", "severity": "CRITICAL", "status": "OPEN", "links": {}}
        regs["risks"].append(risk)
        self.assertEqual(gate.decide(clean_audit(), None, None, registers=regs)["decision"], constants.GATE_CRITICAL)
        risk["status"] = "ACCEPTED"
        d = gate.decide(clean_audit(), None, None, registers=regs)
        self.assertEqual(d["decision"], constants.GATE_CRITICAL)
        self.assertIn("never lifts the block", " ".join(d["reasons"]))
        risk["severity"] = "MEDIUM"
        self.assertEqual(gate.decide(clean_audit(), None, None, registers=regs)["decision"], constants.GATE_READY_ACCEPTED)
        risk["links"] = {"findings": ["FC-0001"]}  # represented by its findings instead
        self.assertEqual(gate.decide(clean_audit(), None, None, registers=regs)["decision"], constants.GATE_READY)

    def test_false_positive_is_visible_and_critical_still_blocks(self):
        f = dict(helpers.sql_finding(), id="FC-0001", status="FALSE_POSITIVE", severity="MEDIUM",
                 effective_confidence="CONFIRMED", release_blocking=True)
        d = gate.decide(clean_audit([f]), None, None)
        self.assertEqual((d["decision"], d["dismissed_findings"]), (constants.GATE_READY_ACCEPTED, ["FC-0001"]))
        f["severity"] = "CRITICAL"
        self.assertEqual(gate.decide(clean_audit([f]), None, None)["decision"], constants.GATE_CRITICAL)


if __name__ == "__main__":
    unittest.main()
