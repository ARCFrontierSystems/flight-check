"""SARIF export of the findings that count against release (for GitHub code scanning)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402,F401
from flight_check_lib import sarif  # noqa: E402
from test_freshness import ProjectTestCase  # noqa: E402
from test_state import run_cli  # noqa: E402


class SarifTests(ProjectTestCase):
    def test_open_findings_become_sarif_results_with_their_lines(self):
        code, out = run_cli("sarif", "--root", self.root, "--out", "flight-check.sarif")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["written"], os.path.join(self.sd, "reports", "flight-check.sarif"))
        with open(out["written"]) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["version"], "2.1.0")
        run = doc["runs"][0]
        sql = next(r for r in run["results"] if r["ruleId"] == "application-security.sql-injection")
        loc = sql["locations"][0]["physicalLocation"]
        self.assertEqual((loc["artifactLocation"]["uri"], loc["region"]["startLine"]), ("src/store.py", 11))
        self.assertEqual(sql["level"], "error")
        self.assertRegex(sql["properties"]["flight_check_id"], r"^FC-\d{4}$")
        rule = next(r for r in run["tool"]["driver"]["rules"] if r["id"] == sql["ruleId"])
        self.assertEqual(rule["properties"]["security-severity"], "8.0")
        self.assertIn("security", rule["properties"]["tags"])
        self.assertEqual(out["exported"], len(run["results"]))
        # Accepted or dismissed findings are left out; paths are percent-encoded; --out stays under reports/.
        fid = sql["properties"]["flight_check_id"]
        self.assertEqual(run_cli("ledger", "mark", "--root", self.root, "--id", fid,
                                 "--status", "FALSE_POSITIVE", "--reason", "The input is a fixed constant in this fixture.")[0], 0)
        ids = [r["properties"]["flight_check_id"] for r in run_cli("sarif", "--root", self.root)[1]["runs"][0]["results"]]
        self.assertNotIn(fid, ids)
        self.assertEqual(sarif._uri("docs/a b/é.md"), "docs/a%20b/%C3%A9.md")
        self.assertEqual(run_cli("sarif", "--root", self.root, "--out", "../x.sarif")[0], 1)


if __name__ == "__main__":
    unittest.main()
