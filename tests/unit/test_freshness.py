"""Change detection, evidence freshness, lifecycle additions, and new gate inputs (Phase 7a)."""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_source_change_reassesses_access_control_and_data_domains(self):
        self.edit("src/store.py")
        with mock.patch.object(freshness, "_applicable_domains", lambda st: set(catalog.domain_ids())):
            out = self.changes_direct()
        for d in ("application-security", "authentication", "authorization", "data-protection", "privacy"):
            self.assertIn(d, out["reassess_domains"])
        # Recorded not applicable but applicable now: nothing to carry, so it is reassessed.
        self.assertIn("accessibility", out["reassess_domains"])

    def changes_direct(self):
        return freshness.changes(self.root, state.load(self.sd, root=self.root))

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
        listed = {f["id"]: f for f in run_cli("findings", out["run_dir"])[1]["findings"]}
        self.assertEqual(listed[carried[0]["id"]]["carried_forward_from"], "20261013T100000Z")
        # Nothing changed since: the carried-forward chain holds, so there is nothing to reassess.
        self.assertTrue(self.init_changed("2026-10-13T13:00:00Z")["nothing_to_reassess"])

    def test_inventory_no_does_not_drop_a_carried_domain(self):
        self.edit("docs/PRIVACY.md")
        start = self.init_changed(T2)
        inv = helpers.base_inventory()
        for a in inv["applicability"]:
            if a["domain"] == "application-security":
                a.update(applicable="no", rationale="No application code found this time.",
                         evidence=[{"kind": "absence", "searched": "Glob **/*.py"}])
        helpers.write_run(parts=[privacy_part()], inventory=inv, base=os.path.join(self.sd, "runs"),
                          run=dict(helpers.base_run(), run_id=start["run_id"]))
        code, res = run_cli("finalize", start["run_dir"], "--root", self.root, "--ledger", self.led, "--now", T2)
        self.assertIn(code, (0, 2, 3, 4, 5), res)
        self.assertIn("application-security", res["carried_forward_domains"])
        self.assertIn("carried forward", " ".join(res["warnings"]))
        app = next(a for a in state.load(self.sd).project["applicability"] if a["domain"] == "application-security")
        self.assertEqual(app["classification"], "uncertain")

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


class ReviewFixTests(ProjectTestCase):
    """Defects in change detection found by the V1.5 review (Phase 10)."""

    def test_rule_matching_ignores_case_and_nested_build_dirs_are_scanned(self):
        self.assertIn("payments", freshness.domains_for_path("src/components/CheckoutForm.tsx"))
        self.assertIn("infrastructure", freshness.domains_for_path("Dockerfile"))
        for rel in ("src/coverage/claims.py", "tools/build/release.sh", "build/out.js"):
            os.makedirs(os.path.join(self.root, os.path.dirname(rel)), exist_ok=True)
            with open(os.path.join(self.root, rel), "w") as fh:
                fh.write("x")
        files, _ = freshness.scan(self.root)
        self.assertIn("src/coverage/claims.py", files)
        self.assertIn("tools/build/release.sh", files)
        self.assertNotIn("build/out.js", files)

    def test_dot_slash_evidence_paths_are_normalized(self):
        self.assertEqual(freshness.normalize_path("./src//store.py"), "src/store.py")
        f = {"evidence": [{"kind": "code", "path": "./src/store.py"}]}
        self.assertEqual(list(freshness.entry_evidence_files(f, self.root)), ["src/store.py"])

    def test_a_file_cited_only_by_a_record_without_a_domain_still_widens(self):
        with open(os.path.join(self.root, "access.rules"), "w") as fh:
            fh.write("allow read: if false")
        freshness.write_manifest(self.sd, self.root, "20261013T100000Z", T1)
        digest = freshness.scan(self.root)[0]["access.rules"]
        self.edit("access.rules", "allow read: if true")
        cited = [("profile", "PF-0009", None, {"access.rules": digest}, "current")]
        with mock.patch.object(freshness, "_conclusions", lambda st: cited):
            out = self.changes_direct()
        self.assertTrue(out["widened"])

    def changes_direct(self):
        return freshness.changes(self.root, state.load(self.sd, root=self.root))

    def test_carried_findings_seen_later_in_the_chain_still_count(self):
        from flight_check_lib import finalize
        audit = {"findings": [], "coverage": [{"domain": "application-security", "status": constants.CARRIED_FORWARD,
                                                "carried_forward": {"run_id": "20261013T100000Z", "at": T1}}]}
        entry = {"id": "FC-0001", "domain": "application-security", "rule": "application-security.x", "title": "t",
                 "severity": "HIGH", "status": "OPEN", "last_seen_run": "20261013T110000Z", "confidence": "CONFIRMED",
                 "release_blocking": True}
        self.assertEqual([f["id"] for f in finalize._carried_findings(audit, {"FC-0001": entry}, {})], ["FC-0001"])
        entry["last_seen_run"] = "20261012T090000Z"  # before the domain's assessment: not a current conclusion
        self.assertEqual(finalize._carried_findings(audit, {"FC-0001": entry}, {}), [])

    def test_a_change_no_applicable_domain_needs_runs_with_no_agent(self):
        os.makedirs(os.path.join(self.root, "img"))
        with open(os.path.join(self.root, "img", "logo.svg"), "w") as fh:
            fh.write("<svg/>")
        code, out = run_cli("init-run", "--base", os.path.join(self.sd, "runs"), "--root", self.root, "--changed", "--now", T2)
        self.assertEqual((code, out["scope"]["reassess_domains"]), (0, []), out)
        helpers.write_run(parts=[], base=os.path.join(self.sd, "runs"), run=dict(helpers.base_run(), run_id=out["run_id"]))
        code, res = run_cli("finalize", out["run_dir"], "--root", self.root, "--ledger", self.led, "--now", T2)
        self.assertIn(code, (0, 2, 3, 4, 5), res)
        self.assertEqual(sorted(res["carried_forward_domains"]), ["application-security", "privacy"])

    def test_imported_evidence_outside_the_project_is_not_stored_as_a_path(self):
        from flight_check_lib import profile
        refs = profile.evidence_refs([{"kind": "user-provided", "path": "/tmp/scan.json"},
                                      {"kind": "user-provided", "path": "../outside.json"}], self.root, T1, "r1")
        self.assertTrue(all("path" not in r for r in refs))

    def test_expired_acceptance_means_the_decision_must_be_remade(self):
        from types import SimpleNamespace
        from flight_check_lib import cli
        import datetime
        st = state.load(self.sd, root=self.root)
        self.assertEqual(cli._decision_inputs_changed(st, datetime.date(2026, 10, 13)), [])
        entry = next(iter(st.ledger["entries"].values()))
        entry.update(status="ACCEPTED_RISK", accepted_risk={"risk": "r", "reason": "reason", "owner": "o", "date": "2026-10-01",
                                                            "scope": "s", "compensating_controls": "none", "review_date": "2026-10-05",
                                                            "recorded_at": T1, "source": "user"})
        self.assertIn("no longer counts", " ".join(cli._decision_inputs_changed(st, datetime.date(2026, 10, 13))))


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


class RecordedIdentityTests(unittest.TestCase):
    """The verifier's recorded_id keeps a finding's history when an agent rewords it (Phase 10)."""

    def setUp(self):
        self.led = ledger.new_ledger("t")
        self.cov = [{"domain": d, "status": "ASSESSED"} for d in catalog.domain_ids()]
        ledger.merge(self.led, {"run": {"run_id": "r1"}, "findings": [helpers.sql_finding()], "coverage": self.cov}, T1, None)

    def reworded(self, **over):
        f = helpers.sql_finding(rule="application-security.unsafe-query-building", title="Notes search builds SQL from a raw term")
        f["evidence"][0]["quote"] = f["evidence"][0]["quote"].replace("SELECT", "select")  # the anchor changed too
        f.update(over)
        return f

    def test_reworded_finding_without_identity_is_new(self):
        changes = ledger.merge(self.led, {"run": {"run_id": "r2"}, "findings": [self.reworded()], "coverage": self.cov}, T2, None)
        self.assertEqual((changes["new"], changes["not_reproduced"]), (["FC-0002"], ["FC-0001"]))

    def test_recorded_id_keeps_history_across_rule_title_and_domain(self):
        f = self.reworded(recorded_id="FC-0001", domain="data-protection", rule="data-protection.query-exposure")
        changes = ledger.merge(self.led, {"run": {"run_id": "r2"}, "findings": [f], "coverage": self.cov}, T2, None)
        self.assertEqual((changes["new"], changes["not_reproduced"], changes["recorded_matches"]), ([], [], ["FC-0001"]))
        entry = ledger.find_entry(self.led, "FC-0001")[1]
        self.assertEqual((entry["domain"], entry["rule"], entry["last_seen_run"]), ("data-protection", "data-protection.query-exposure", "r2"))
        self.assertIn("identified by the verifier", entry["history"][-1]["note"])

    def test_identity_judgment_is_only_a_possible_regression_and_never_beats_a_fingerprint(self):
        ledger.mark(self.led, "FC-0001", "FALSE_POSITIVE", "constant input in this deployment", T2, by="owner")
        entry = ledger.find_entry(self.led, "FC-0001")[1]
        entry["status"] = "VERIFIED"
        changes = ledger.merge(self.led, {"run": {"run_id": "r2"}, "findings": [self.reworded(recorded_id="FC-0001")],
                                          "coverage": self.cov}, T2, None)
        self.assertEqual((changes["regressions"], changes["possible_regressions"]), ([], ["FC-0001"]))
        # An exact fingerprint match wins over another finding's claim, and an unknown ID is reported, not invented.
        both = [helpers.sql_finding(), self.reworded(recorded_id="FC-0001"), self.reworded(local_id="F9", recorded_id="FC-0042",
                                                                                             title="Another reworded title here")]
        changes = ledger.merge(self.led, {"run": {"run_id": "r3"}, "findings": both, "coverage": self.cov}, T3, None)
        self.assertEqual(changes["unknown_recorded_ids"], ["FC-0042"])
        self.assertEqual(len(changes["new"]), 2)
        self.assertEqual(both[0]["id"], "FC-0001")


class RecordedIdentityFinalizeTests(ProjectTestCase):
    def test_verifier_recorded_id_reaches_the_ledger_through_a_duplicate(self):
        sql_id = next(e["id"] for e in state.load(self.sd).ledger["entries"].values() if e["rule"].endswith("sql-injection"))
        code, out = run_cli("init-run", "--base", os.path.join(self.sd, "runs"), "--root", self.root, "--now", T2)
        self.assertIn(sql_id, [r["id"] for r in out["project_records"]["recorded_findings"]])
        main = helpers.all_controls_resolved(helpers.base_part(findings=[helpers.sql_finding(rule="application-security.raw-sql-search",
                                                                                              title="Search term reaches raw SQL"),
                                                                          helpers.deletion_finding()]))
        second = helpers.base_part(findings=[helpers.sql_finding(local_id="F7", rule="application-security.sql-built-from-input",
                                                                 title="SQL text built from user input")])
        second.update(agent="appsec-p2", controls=[], positive_controls=[])
        verification = {"verdicts": [
            {"local_id": "appsec.F1", "verdict": "CONFIRMED", "notes": "Re-read the search query; confirmed."},
            {"local_id": "appsec.F2", "verdict": "CONFIRMED", "notes": "Re-read deletion and policy; confirmed."},
            {"local_id": "appsec-p2.F7", "verdict": "DUPLICATE", "duplicate_of": "appsec.F1", "recorded_id": sql_id,
             "notes": "Same raw SQL search as appsec.F1."}]}
        helpers.write_run(parts=[main, second], verification=verification, base=os.path.join(self.sd, "runs"),
                          run=dict(helpers.base_run(), run_id=out["run_id"]))
        code, res = run_cli("finalize", out["run_dir"], "--root", self.root, "--ledger", self.led, "--now", T2)
        self.assertIn(code, (0, 2, 3, 4, 5), res)
        self.assertIn(sql_id, [f["id"] for f in res["findings"]])
        self.assertEqual(res["lifecycle"]["recorded_matches"], [sql_id])
        self.assertEqual(res["lifecycle"]["new"], [])


class GateReviewFixTests(unittest.TestCase):
    """Ship-decision defects found by the V1.5 review (Phase 10)."""

    def setUp(self):
        self.led = ledger.new_ledger("t")
        self.cov = [{"domain": d, "status": "ASSESSED"} for d in catalog.domain_ids()]
        ledger.merge(self.led, {"run": {"run_id": "r1"}, "findings": [helpers.sql_finding()], "coverage": self.cov}, T1, None)
        self.proj, self.regs = state.new_project(state.new_identity(None, "t", T1)), state.new_registers()

    def test_open_findings_in_a_domain_reported_not_applicable_still_count(self):
        from flight_check_lib import finalize
        entry = ledger.find_entry(self.led, "FC-0001")[1]
        entry.update(confidence="CONFIRMED", release_blocking=True)
        audit = {"findings": [], "coverage": [{"domain": "application-security", "status": "NOT_APPLICABLE"}]}
        carried = finalize._carried_findings(audit, {"FC-0001": entry}, {})
        self.assertEqual([(f["id"], f["basis"]) for f in carried], [("FC-0001", "not_applicable")])
        d = gate.decide(dict(clean_audit(), carried_forward_findings=carried), self.led, None)
        self.assertEqual(d["decision"], constants.GATE_NOT_READY)

    def test_a_judged_match_does_not_inherit_an_acceptance_for_something_milder(self):
        ledger.accept(self.led, "FC-0001", {"risk": "r", "reason": "internal only", "owner": "o", "date": "2026-10-13",
                                            "scope": "s", "compensating_controls": "none"}, T1, datetime.date(2026, 10, 13))
        entry = ledger.find_entry(self.led, "FC-0001")[1]
        entry["severity"] = "LOW"
        other = helpers.sql_finding(rule="data-protection.something-else", domain="data-protection",
                                    title="A different and worse problem", recorded_id="FC-0001", severity="HIGH")
        other["evidence"][0]["quote"] = other["evidence"][0]["quote"].replace("SELECT", "select")
        changes = ledger.merge(self.led, {"run": {"run_id": "r2"}, "findings": [other], "coverage": self.cov}, T2, None)
        self.assertEqual((entry["status"], changes["reopened"]), ("OPEN", ["FC-0001"]))

    def test_flight_check_never_reverts_user_decisions(self):
        req = registers.add("requirement", self.proj, self.regs, self.led, {"title": "No secrets", "description": "None in code.",
                                                                            "category": "security", "criticality": "release-critical"}, "user", T1)
        with self.assertRaises(registers.RegisterError):
            registers.update(self.proj, self.regs, self.led, req["id"], "flight-check", T2, status="PROPOSED")
        registers.update(self.proj, self.regs, self.led, req["id"], "flight-check", T2, status="IMPLEMENTED")
        item = registers.add("legal", self.proj, self.regs, self.led, {"title": "Terms", "category": "Terms",
                                                                       "description": "Terms are missing.", "trigger": "owner"}, "user", T1)
        registers.update(self.proj, self.regs, self.led, item["id"], "user", T2, status="ACTION_REQUIRED")
        with self.assertRaises(registers.RegisterError):
            registers.update(self.proj, self.regs, self.led, item["id"], "flight-check", T3, status="NEEDS_REVIEW")

    def test_register_acceptance_timing_and_dates(self):
        import datetime
        risk = registers.add("risk", self.proj, self.regs, self.led, {"title": "Bulk job", "description": "A risky job.",
                                                                      "domain": "reliability", "severity": "HIGH"}, "user", T1)
        acc = {"reason": "reviewed", "owner": "o", "date": "2026-10-13", "scope": "s", "compensating_controls": "none"}
        with self.assertRaises(registers.RegisterError):
            registers.accept_risk(self.regs, risk["id"], dict(acc, date="2027-01-01"), T2)
        registers.accept_risk(self.regs, risk["id"], acc, T2)
        audit = dict(clean_audit(), run={"run_id": "20261013T103000Z", "started_at": "2026-10-13T10:30:00Z"})
        d = gate.decide(audit, None, datetime.date(2026, 10, 13), registers=self.regs)
        self.assertEqual(d["decision"], constants.GATE_NOT_READY)  # recorded at 11:00, after the run started
        self.assertIn("counts from the next run", " ".join(d["acceptances_not_counted"]))

    def test_dismissal_during_a_run_does_not_change_that_run(self):
        ledger.mark(self.led, "FC-0001", "FALSE_POSITIVE", "constant input in this deployment", "2026-10-13T12:05:00Z", by="owner")
        f = dict(helpers.sql_finding(), id="FC-0001", status="FALSE_POSITIVE", effective_confidence="CONFIRMED", release_blocking=True)
        audit = dict(clean_audit([f]), run={"run_id": "20261013T120000Z", "started_at": "2026-10-13T12:00:00Z"})
        d = gate.decide(audit, self.led, None)
        self.assertEqual(d["decision"], constants.GATE_NOT_READY)

    def test_requirement_not_verified_while_a_linked_finding_is_triaged_and_remediation_allowed(self):
        req = registers.add("requirement", self.proj, self.regs, self.led, {"title": "Safe SQL", "description": "Bound parameters.",
                                                                            "category": "security", "criticality": "high"}, "user", T1,
                            links=["control:APPSEC-SECRETS", "FC-0001"])
        ledger.mark(self.led, "FC-0001", "REMEDIATION_PLANNED", "fix scheduled this sprint", T2, by="owner")
        final = {"controls": [{"id": "APPSEC-SECRETS", "state": "VERIFIED", "evidence_check": "PASSED"}]}
        registers.apply_assessment(self.regs, final, registers.entries_by_id(self.led), T3, "r3")
        self.assertNotEqual(req["status"], "VERIFIED")
        ledger.record_remediation(self.led, "FC-0001", "parameterized the query", ["src/store.py"], T3)
        self.assertEqual(ledger.find_entry(self.led, "FC-0001")[1]["status"], "REMEDIATED")


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
        risk["links"] = {"findings": ["FC-0001"]}
        # A linked finding that is not in this run's decision does not stand in for the risk.
        self.assertEqual(gate.decide(clean_audit(), None, None, registers=regs)["decision"], constants.GATE_READY_ACCEPTED)
        f = {"id": "FC-0001", "severity": "MEDIUM", "confidence": "CONFIRMED", "release_blocking": False, "status": "OPEN"}
        self.assertEqual(gate.decide(clean_audit([f]), None, None, registers=regs)["decision"], constants.GATE_READY)
        risk.update(severity="CRITICAL", status="OPEN")  # a milder linked finding never hides a more severe risk
        self.assertEqual(gate.decide(clean_audit([f]), None, None, registers=regs)["decision"], constants.GATE_CRITICAL)

    def test_false_positive_is_visible_and_critical_still_blocks(self):
        f = dict(helpers.sql_finding(), id="FC-0001", status="FALSE_POSITIVE", severity="MEDIUM",
                 effective_confidence="CONFIRMED", release_blocking=True)
        d = gate.decide(clean_audit([f]), None, None)
        self.assertEqual((d["decision"], d["dismissed_findings"]), (constants.GATE_READY_ACCEPTED, ["FC-0001"]))
        f["severity"] = "CRITICAL"
        self.assertEqual(gate.decide(clean_audit([f]), None, None)["decision"], constants.GATE_CRITICAL)


if __name__ == "__main__":
    unittest.main()
