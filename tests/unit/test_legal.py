"""Legal review queue and packets from queue items (Phase 6)."""

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import catalog, ledger, legal, minischema, state  # noqa: E402
from test_state import run_cli  # noqa: E402

T1, T2, T3 = "2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z", "2026-10-13T12:00:00Z"


def audit(findings, run_id="20261013T100000Z"):
    return {"run": {"run_id": run_id, "mode": "audit", "target": {"name": "t"}}, "findings": findings,
            "coverage": [{"domain": d, "status": "ASSESSED"} for d in catalog.domain_ids()], "remediation_checks": []}


def setup_queue(findings):
    led = ledger.new_ledger("t")
    a = audit(findings)
    ledger.merge(led, a, T1, None)
    proj, regs = state.new_project(state.new_identity(None, "t", T1)), state.new_registers()
    changes = legal.sync_findings(regs, proj, led, a["findings"], T1, "r1", helpers.FIXTURE_ROOT)
    return led, proj, regs, a, changes


class SyncTests(unittest.TestCase):
    def test_legal_findings_open_items_and_informational_ones_do_not(self):
        info = helpers.sql_finding()
        info["legal"] = {"classification": "INFORMATIONAL", "why_review": "context only"}
        led, proj, regs, a, changes = setup_queue([helpers.deletion_finding(), info])
        self.assertEqual(len(regs["legal"]), 1)
        item = regs["legal"][0]
        deletion_id = next(f["id"] for f in a["findings"] if f["legal"]["classification"] != "INFORMATIONAL")
        self.assertEqual((item["status"], item["origin"], item["source"], item["trigger"]),
                         ("NEEDS_REVIEW", "observed", "flight-check", "finding %s" % deletion_id))
        self.assertTrue(item["jurisdiction_unknown"])
        self.assertEqual(item["questions"], helpers.deletion_finding()["legal"]["questions"])
        self.assertTrue(all("quote" not in ev and ev.get("sha256") for ev in item["evidence"] if ev.get("path")))
        self.assertEqual(sorted(str(f["legal_status"]) for f in a["findings"]), ["NEEDS_REVIEW", "None"])
        self.assertEqual(changes[0][1], {"status": [None, "NEEDS_REVIEW"]})
        errs = minischema.validate_def(json.loads(json.dumps(regs)), catalog.schema(), "registers") + state.semantic_errors(proj, regs)
        self.assertEqual(errs, [])

    def test_reclassification_new_questions_and_regression(self):
        led, proj, regs, a, _ = setup_queue([helpers.deletion_finding()])
        legal.set_status(regs, proj, led, "FC-0001", "RESOLVED", "user", T2, "Counsel advised; deletion now implemented.")
        f = helpers.deletion_finding()
        f["legal"] = dict(f["legal"], classification="LEGAL REVIEW REQUIRED",
                          questions=f["legal"]["questions"] + ["Does the retention period need to appear in the terms?"])
        a2 = audit([f], "r2")
        ledger.merge(led, a2, T3, None)
        a2["findings"][0]["status"] = "REGRESSION"
        changes = legal.sync_findings(regs, proj, led, a2["findings"], T3, "r2")
        item = regs["legal"][0]
        self.assertEqual((item["classification"], item["status"]), ("LEGAL REVIEW REQUIRED", "NEEDS_REVIEW"))
        self.assertEqual(len(item["questions"]), len(helpers.deletion_finding()["legal"]["questions"]) + 1)
        self.assertIn("came back", item["history"][-1]["note"])
        self.assertEqual(changes[0][1], {"status": ["RESOLVED", "NEEDS_REVIEW"]})

    def test_regression_without_a_legal_block_still_reopens_the_item(self):
        led, proj, regs, a, _ = setup_queue([helpers.deletion_finding()])
        legal.set_status(regs, proj, led, "FC-0001", "RESOLVED", "user", T2, "Counsel advised; deletion now implemented.")
        f = helpers.deletion_finding()
        del f["legal"]
        a2 = audit([f], "r2")
        ledger.merge(led, a2, T3, None)
        a2["findings"][0]["status"] = "REGRESSION"
        legal.sync_findings(regs, proj, led, a2["findings"], T3, "r2")
        self.assertEqual((regs["legal"][0]["status"], a2["findings"][0]["legal_status"]), ("NEEDS_REVIEW", "NEEDS_REVIEW"))

    def test_an_item_flight_check_opens_is_attributed_to_it(self):
        info = helpers.deletion_finding()
        info["legal"] = {"classification": "INFORMATIONAL", "why_review": "context only"}
        led, proj, regs, a, _ = setup_queue([info])
        rec, _ = legal.set_status(regs, proj, led, "FC-0001", "NEEDS_REVIEW", "flight-check", T2, "flagged for review")
        self.assertEqual((rec["source"], rec["history"][0]["source"]), ("flight-check", "flight-check"))

    def test_only_the_user_closes_legal_questions(self):
        led, proj, regs, _, _ = setup_queue([helpers.deletion_finding()])
        for status in ("REVIEWED", "RESOLVED", "NOT_APPLICABLE", "AWAITING_COUNSEL"):
            with self.assertRaises(legal.LegalError):
                legal.set_status(regs, proj, led, "FC-0001", status, "flight-check", T2, "auto")
        for status in ("REVIEWED", "RESOLVED", "NOT_APPLICABLE"):
            with self.assertRaises(legal.LegalError):
                legal.set_status(regs, proj, led, "FC-0001", status, "user", T2, "")
        with self.assertRaises(legal.LegalError):
            legal.set_status(regs, proj, led, "FC-0099", "AWAITING_COUNSEL", "user", T2, "x")


class CliAndPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flight-check-legal-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.led = os.path.join(self.tmp, "ledger.json")
        self.run_dir = helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())], base=self.tmp)
        code, out = run_cli("finalize", self.run_dir, "--root", helpers.FIXTURE_ROOT, "--ledger", self.led, "--now", T1)
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.run_dir, "audit.final.json")) as fh:
            self.final = json.load(fh)
        self.legal_id = next(f["id"] for f in self.final["findings"] if f.get("legal"))

    def cli(self, *argv):
        return run_cli(*argv, "--state-dir", self.tmp)

    def request(self, **over):
        req = {"project_identifier": "fixture", "executive_summary": "E" * 50, "overall_summary": "O" * 50,
               "finding_ids": [], "item_ids": []}
        req.update(over)
        path = os.path.join(self.tmp, "req.json")
        with open(path, "w") as fh:
            json.dump(req, fh)
        return path

    def test_finding_status_shows_in_reports_and_v1_command_maps(self):
        self.assertEqual(next(f for f in self.final["findings"] if f["id"] == self.legal_id)["legal_status"], "NEEDS_REVIEW")
        code, out = run_cli("ledger", "legal", "--ledger", self.led, "--id", self.legal_id, "--status", "VERIFIED",
                            "--source", "flight-check", "--note", "looks fine")
        self.assertEqual(code, 1)  # Flight Check never resolves a legal question
        code, out = run_cli("ledger", "legal", "--ledger", self.led, "--id", self.legal_id, "--status",
                            "LEGAL_REVIEW_REQUESTED", "--source", "user", "--note", "sent to counsel", "--now", T2)
        self.assertEqual((code, out["status"]), (0, "AWAITING_COUNSEL"), out)
        code, out = run_cli("ledger", "show", "--ledger", self.led)
        self.assertEqual(next(e for e in out["entries"] if e["id"] == self.legal_id)["legal_status"], "AWAITING_COUNSEL")

    def test_standalone_items_list_message_and_packet(self):
        code, out = self.cli("legal", "add", "--title", "Refund terms for annual plans", "--category", "Refund and cancellation",
                             "--description", "The product sells annual plans but no refund terms were provided.",
                             "--trigger", "owner request", "--question", "Are refund terms needed for annual plans?",
                             "--classification", "POLICY GAP", "--now", T2)
        self.assertEqual((code, out["id"], out["status"]), (0, "LR-0002", "NEEDS_REVIEW"), out)
        item = self.cli("legal", "show", "--id", "LR-0002")[1]["record"]
        self.assertTrue(item["jurisdiction_unknown"])
        code, out = self.cli("legal", "update", "--id", "LR-0002", "--status", "RESOLVED", "--now", T3)
        self.assertEqual(code, 1)  # needs a note saying what resolved it
        pdf = os.path.join(self.tmp, "packet.pdf")
        code, out = run_cli("packet", self.run_dir, "--request", self.request(item_ids=["LR-0002"]), "--out", pdf,
                            "--ledger", self.led, "--now", T3)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["findings"], [])
        with open(os.path.splitext(pdf)[0] + ".md") as fh:
            md = fh.read()
        self.assertIn("LR-0002", md)
        self.assertIn("Not determined; counsel should identify the relevant jurisdictions", md)
        self.assertIn("Are refund terms needed for annual plans?", md)
        regs = state.load(self.tmp).registers
        self.assertIn("attorney review packet", next(i for i in regs["legal"] if i["id"] == "LR-0002")["history"][-1]["note"])
        # Closing every open item leads to the "no specific issue" message, and an empty packet is refused.
        for iid in ("LR-0001", "LR-0002"):
            code, out = self.cli("legal", "update", "--id", iid, "--status", "NOT_APPLICABLE",
                                 "--note", "Counsel confirmed this does not apply to the product.", "--now", T3)
            self.assertEqual(code, 0, out)
        code, out = self.cli("legal", "list")
        self.assertEqual(out["message"], legal.NO_ISSUES)
        code, out = run_cli("packet", self.run_dir, "--request", self.request(), "--out", pdf, "--ledger", self.led, "--now", T3)
        self.assertEqual(code, 1)
        self.assertIn("no specific legal-review issue has been identified", " ".join(out["errors"]))
        code, out = run_cli("packet", self.run_dir, "--request", self.request(item_ids=["LR-0042"]), "--out", pdf,
                            "--ledger", self.led, "--now", T3)
        self.assertEqual(code, 1)
        self.assertEqual(run_cli("state", "check", "--state-dir", self.tmp)[0], 0)


if __name__ == "__main__":
    unittest.main()
