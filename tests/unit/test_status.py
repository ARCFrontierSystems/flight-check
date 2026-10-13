"""Status and report views, and decision-consistency links (Phase 8)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import legal, registers, state, status  # noqa: E402
from test_freshness import T1, T2, T3, ProjectTestCase  # noqa: E402
from test_state import run_cli  # noqa: E402


def run_text(*argv):
    """Run the CLI and return (exit code, raw stdout) for Markdown output."""
    import contextlib
    import io
    from flight_check_lib import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class StatusTests(ProjectTestCase):
    def test_status_answers_from_records(self):
        code, out = run_cli("status", "--root", self.root, "--format", "json", "--now", T2)
        self.assertEqual(code, 0, out)
        d = out["data"]
        self.assertEqual(out["report_version"], status.REPORT_VERSION)
        self.assertEqual(d["readiness"]["latest"]["run_id"], "20261013T100000Z")
        self.assertFalse(d["readiness"]["historical"])
        self.assertEqual([f["severity"] for f in d["findings"]["top"]], ["HIGH"])
        self.assertEqual(d["legal"]["open"][0]["status"], "NEEDS_REVIEW")
        self.assertIn("application-security", d["applicability"]["applicable"])
        self.edit("src/store.py")
        d = run_cli("status", "--root", self.root, "--format", "json", "--now", T2)[1]["data"]
        self.assertTrue(d["readiness"]["historical"])
        self.assertIn("application-security", d["changes"]["reassess_domains"])
        self.assertTrue(any("--changed" in s for s in d["next_steps"]))
        code, text = run_text("status", "--root", self.root, "--now", T2)
        self.assertEqual(code, 0)
        for heading in ("Release readiness", "What changed", "Most important open issues", "Legal questions", "Next steps"):
            self.assertIn(heading, text)
        self.assertIn("Historical", text)

    def test_every_view_renders_and_writes(self):
        with state.transaction(self.sd, "user", "test", T2, root=self.root) as tx:
            registers.add("decision", tx.project, tx.registers, tx.ledger,
                          {"title": "Parameterized SQL only", "context": "Injection risk", "decision": "Bind all parameters.",
                           "assertions": ["Every SQL statement binds user input as parameters."]}, "user", T2)
        for view in status.VIEWS:
            code, text = run_text("report", "--view", view, "--root", self.root, "--now", T3)
            self.assertEqual(code, 0, (view, text))
            self.assertTrue(text.startswith("# Flight Check"), view)
        fid = state.load(self.sd, root=self.root).ledger["entries"]
        fid = sorted(e["id"] for e in fid.values())[0]
        code, out = run_cli("report", "--view", "findings", "--id", fid, "--root", self.root, "--format", "json", "--out", "finding.json")
        self.assertEqual((code, out["written"]), (0, os.path.join(self.sd, "reports", "finding.json")))
        with open(out["written"]) as fh:
            self.assertEqual(json.load(fh)["data"]["finding"]["id"], fid)
        self.assertEqual(run_cli("report", "--view", "decisions", "--id", "DEC-0042", "--root", self.root)[0], 1)
        # Reports are written only under <state dir>/reports/, never through a link or to another path.
        for bad in (os.path.join(self.tmp, "x.md"), "../x.md", "x.json", ".x.md"):
            self.assertEqual(run_cli("report", "--view", "readiness", "--root", self.root, "--out", bad)[0], 1, bad)
        os.symlink(os.path.join(self.tmp, "elsewhere.md"), os.path.join(self.sd, "reports", "link.md"))
        self.assertEqual(run_cli("report", "--view", "readiness", "--root", self.root, "--out", "link.md")[0], 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "elsewhere.md")))
        with open(os.path.join(self.sd, ".gitignore")) as fh:
            self.assertIn("reports/", fh.read())

    def test_no_legal_issues_message_and_no_state(self):
        regs = state.new_registers()
        self.assertEqual(legal.open_items(regs), [])
        code, out = run_cli("status", "--root", os.path.join(self.tmp, "nowhere"), "--format", "json")
        self.assertEqual(code, 1)
        self.assertIn("no project state yet", out["error"])


class DecisionConsistencyTests(ProjectTestCase):
    def test_contradiction_links_finding_and_flags_decision(self):
        with state.transaction(self.sd, "user", "test", T2, root=self.root) as tx:
            registers.add("decision", tx.project, tx.registers, tx.ledger,
                          {"title": "Parameterized SQL only", "context": "Injection risk", "decision": "Bind all parameters.",
                           "assertions": ["Every SQL statement binds user input as parameters."]}, "user", T2)
        code, out = run_cli("init-run", "--base", os.path.join(self.sd, "runs"), "--root", self.root, "--now", T3)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["project_records"]["decision_assertions"][0]["decision"], "DEC-0001")
        part = helpers.all_controls_resolved(helpers.base_part(findings=[helpers.sql_finding(decision_ids=["DEC-0001", "DEC-0099"]),
                                                                          helpers.deletion_finding()]))
        helpers.write_run(parts=[part], base=os.path.join(self.sd, "runs"), run=dict(helpers.base_run(), run_id=out["run_id"]))
        code, res = run_cli("finalize", out["run_dir"], "--root", self.root, "--ledger", self.led, "--now", T3)
        self.assertIn(code, (0, 2, 3, 4, 5), res)
        self.assertIn("DEC-0099", " ".join(res["warnings"]))
        with open(os.path.join(out["run_dir"], "audit.final.json")) as fh:
            final = json.load(fh)
        sql = next(f for f in final["findings"] if f["rule"] == "application-security.sql-injection")
        self.assertEqual(sql["decision_ids"], ["DEC-0001"])
        dec = state.load(self.sd, root=self.root).registers["decisions"][0]
        self.assertEqual((dec["status"], dec["links"]["findings"]), ("NEEDS_REVIEW", [sql["id"]]))
        self.assertEqual(dec["history"][-1]["source"], "flight-check")
        code, text = run_text("status", "--root", self.root, "--now", T3)
        self.assertIn("DEC-0001", text)


if __name__ == "__main__":
    unittest.main()
