"""The unblock guide: what keeps the latest decision from READY, with the next step for each."""

import datetime
import os
import shutil
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402,F401
from flight_check_lib import estimate, unblock  # noqa: E402
from test_freshness import T2, ProjectTestCase  # noqa: E402
from test_state import run_cli  # noqa: E402

TODAY = datetime.date(2026, 10, 13)
ACCEPTED = {"risk": "r", "reason": "r", "owner": "owner", "date": "2026-10-01", "scope": "release",
            "compensating_controls": "none", "source": "user"}


def entry(fid, severity, status, confidence="LIKELY", blocking=None):
    e = {"id": fid, "severity": severity, "status": status, "title": "t " + fid, "confidence": confidence}
    if blocking is not None:
        e["release_blocking"] = blocking
    return e


def records(decision="NOT READY — REMEDIATION REQUIRED", controls=None, entries=(), legal=(), control_acceptances=None):
    project = {"revision": 3, "readiness": [{"decision": decision, "run_id": "R1", "at": "2026-10-13T00:00:00Z", "revision": 3}],
               "controls": controls or {}, "domains": {}}
    led = {"entries": {e["id"]: e for e in entries}, "control_acceptances": control_acceptances or {}}
    regs = {"requirements": [], "decisions": [], "risks": [], "legal": list(legal)}
    return types.SimpleNamespace(project=project, ledger=led, registers=regs)


class UnblockTests(unittest.TestCase):
    def test_items_mirror_the_gate_most_serious_first(self):
        root = tempfile.mkdtemp(prefix="flight-check-unblock-")
        self.addCleanup(shutil.rmtree, root, True)
        for name in ("package-lock.json", "go.mod"):
            open(os.path.join(root, name), "w").close()
        st = records(controls={"SUPPLY-KNOWN-VULNS": {"state": "UNVERIFIED"}, "APPSEC-SECRETS": {"state": "UNVERIFIED"},
                               "AUTHN-SESSIONS": {"state": "NOT_MET"}, "TEST-EXECUTION": {"state": "UNVERIFIED"}},
                     control_acceptances={"APPSEC-SECRETS": ACCEPTED},
                     entries=[entry("FC-0001", "CRITICAL", "OPEN"), entry("FC-0002", "HIGH", "OPEN", "UNVERIFIED"),
                              entry("FC-0003", "MEDIUM", "OPEN", blocking=True), entry("FC-0004", "LOW", "OPEN"),
                              entry("FC-0005", "HIGH", "REMEDIATED"), dict(entry("FC-0006", "HIGH", "ACCEPTED_RISK"), accepted_risk=ACCEPTED),
                              entry("FC-0007", "HIGH", "NOT_REPRODUCED")],
                     legal=[{"id": "LR-0001", "status": "ACTION_REQUIRED", "title": "Terms"}])
        got = unblock.items(st, root, TODAY)
        self.assertEqual([(i["kind"], i["id"], i["effect"]) for i in got], [
            ("finding", "FC-0001", "critical"),
            ("control", "SUPPLY-KNOWN-VULNS", "insufficient"), ("finding", "FC-0002", "insufficient"),
            ("control", "AUTHN-SESSIONS", "blocking"), ("finding", "FC-0003", "blocking"), ("finding", "FC-0005", "blocking"),
            ("legal", "LR-0001", "blocking")])
        supply = got[1]
        self.assertIn("--recheck SUPPLY-KNOWN-VULNS --evidence", supply["action"])
        commands = " | ".join(supply["commands"])
        for expected in ("mkdir -p .flight-check/evidence", "npm audit --json", "govulncheck -json", "osv-scanner"):
            self.assertIn(expected, commands)
        self.assertIn("verify the fix", got[5]["action"])

    def test_ready_and_current_needs_nothing_but_records_changed_means_refresh(self):
        st = records(decision="READY FOR RELEASE")
        self.assertEqual(unblock.items(st, None, TODAY), [])
        st.project["revision"] = 4  # a record changed after the decision
        self.assertEqual([i["effect"] for i in unblock.items(st, None, TODAY)], ["refresh"])
        self.assertEqual(unblock.scanner_commands(None)[-1].split(":")[0], "Any ecosystem")


class UnblockViewTests(ProjectTestCase):
    def test_status_and_report_show_the_guide(self):
        code, out = run_cli("report", "--view", "unblock", "--root", self.root, "--format", "json", "--now", T2)
        self.assertEqual(code, 0, out)
        ids = [i["id"] for i in out["data"]["items"]]
        self.assertIn("APPSEC-INJECTION", ids)
        self.assertTrue(any(i["kind"] == "finding" and "remediate" in i["action"] for i in out["data"]["items"]))
        code, out = run_cli("status", "--root", self.root, "--format", "json", "--now", T2)
        self.assertEqual([i["id"] for i in out["data"]["unblock"]], ids)


class RecheckTests(ProjectTestCase):
    def init_run(self, *extra):
        return run_cli("init-run", "--base", os.path.join(self.sd, "runs"), "--root", self.root, "--now", T2, *extra)

    def test_recheck_reassesses_the_controls_domains_even_without_changes(self):
        code, out = self.init_run("--changed")
        self.assertTrue(out.get("nothing_to_reassess"), out)
        code, out = self.init_run("--recheck", "supply-known-vulns,DOC-CLAIMS", "--suffix", "r")
        self.assertEqual(code, 0, out)
        scope = out["scope"]
        self.assertEqual(scope["recheck"], {"controls": ["SUPPLY-KNOWN-VULNS", "DOC-CLAIMS"],
                                            "domains": ["documentation", "supply-chain"]})
        for d in ("supply-chain", "documentation"):
            self.assertIn(d, scope["reassess_domains"])
            self.assertNotIn(d, scope["carried_forward_domains"])
        self.assertTrue(set(scope["agents"]))
        self.assertEqual(out["estimate"]["agents"], len(scope["agents"]))
        with open(os.path.join(out["run_dir"], "scope.json")) as fh:
            self.assertIn("recheck", fh.read())
        code, out = self.init_run("--recheck", "NOPE-1", "--suffix", "x")
        self.assertEqual(code, 1)
        self.assertIn("unknown control", out["error"])


class EstimateTests(unittest.TestCase):
    def test_estimate_scales_with_size_agents_and_passes(self):
        full = estimate.estimate(120)
        self.assertEqual((full["size"], full["usd_low"], full["usd_high"]), ("medium", 2.0, 5.0))
        two = estimate.estimate(120, passes=2)
        self.assertGreater(two["usd_high"], full["usd_high"])
        few = estimate.estimate(120, agents=2)
        self.assertLess(few["usd_high"], full["usd_high"])
        self.assertEqual(estimate.estimate(10)["size"], "small")
        self.assertEqual(estimate.estimate(5000)["size"], "large")
        self.assertIn("rough range", full["summary"])


if __name__ == "__main__":
    unittest.main()
