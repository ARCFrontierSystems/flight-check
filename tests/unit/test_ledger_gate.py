import copy
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import catalog, constants, fingerprint, gate, ledger  # noqa: E402

TODAY = datetime.date(2026, 10, 4)


def audit_with(findings, run_id="20261004T120000Z", coverage_status="ASSESSED", controls=None, checks=None):
    cov = [{"domain": d, "status": coverage_status if d in ("application-security", "privacy") else "NOT_APPLICABLE",
            "rationale": "test"} for d in catalog.domain_ids()]
    ctrls = controls if controls is not None else [
        {"id": c["id"], "state": "NOT_APPLICABLE", "release_critical": c["release_critical"], "domain": c["domain"]} for c in catalog.controls()]
    return {"run": {"run_id": run_id, "mode": "audit"}, "findings": copy.deepcopy(findings), "coverage": cov,
            "controls": ctrls, "remediation_checks": checks or []}


class FingerprintTests(unittest.TestCase):
    def test_stable_across_line_shift(self):
        a = helpers.sql_finding()
        b = helpers.sql_finding()
        b["evidence"][0]["start_line"] += 40
        b["evidence"][0]["end_line"] += 40
        self.assertEqual(fingerprint.fingerprint(a), fingerprint.fingerprint(b))

    def test_duplicates_in_one_run_get_distinct_fingerprints(self):
        fps = fingerprint.assign([helpers.sql_finding(), helpers.sql_finding(local_id="F9")])
        self.assertNotEqual(fps[0], fps[1])


class LedgerTests(unittest.TestCase):
    def test_stable_ids_and_regression(self):
        led = ledger.new_ledger("t")
        a1 = audit_with([helpers.sql_finding()], "20261001T000000Z")
        ledger.merge(led, a1, helpers.NOW, TODAY)
        fid = a1["findings"][0]["id"]
        self.assertEqual(fid, "FC-0001")
        # not observed in an assessed domain -> NOT_REPRODUCED, never silently dropped
        a2 = audit_with([], "20261002T000000Z")
        changes = ledger.merge(led, a2, helpers.NOW, TODAY)
        self.assertIn(fid, changes["not_reproduced"])
        ledger.close(led, fid, "user confirmed fix", helpers.NOW)
        # reappears after being closed -> REGRESSION with the same ID
        a3 = audit_with([helpers.sql_finding()], "20261003T000000Z")
        changes = ledger.merge(led, a3, helpers.NOW, TODAY)
        self.assertEqual(a3["findings"][0]["id"], fid)
        self.assertEqual(a3["findings"][0]["status"], "REGRESSION")
        self.assertIn(fid, changes["regressions"])

    def test_same_finding_with_changed_quote_keeps_id(self):
        led = ledger.new_ledger("t")
        a1 = audit_with([helpers.sql_finding()], "20261001T000000Z")
        ledger.merge(led, a1, helpers.NOW, TODAY)
        changed = helpers.sql_finding()
        changed["evidence"][0]["quote"] = "query = 'SELECT body FROM notes WHERE body LIKE ' + term"
        a2 = audit_with([changed], "20261002T000000Z")
        ledger.merge(led, a2, helpers.NOW, TODAY)
        self.assertEqual(a2["findings"][0]["id"], "FC-0001")

    def test_reworded_rule_on_the_same_line_keeps_id(self):
        # Agents name rules freely; the same issue on the same quoted line must keep its ID.
        led = ledger.new_ledger("t")
        a1 = audit_with([helpers.sql_finding()], "20261001T000000Z")
        ledger.merge(led, a1, helpers.NOW, TODAY)
        reworded = helpers.sql_finding(rule="application-security.sql-injection-in-search",
                                       title="User input is concatenated into the search SQL query")
        a2 = audit_with([reworded], "20261002T000000Z")
        changes = ledger.merge(led, a2, helpers.NOW, TODAY)
        self.assertEqual(a2["findings"][0]["id"], "FC-0001")
        self.assertEqual(changes["new"], [])
        self.assertEqual(led["entries"][a2["findings"][0]["fingerprint"]]["rule"], "application-security.sql-injection-in-search")

    def test_reworded_rule_and_quote_with_the_same_title_keeps_id_and_flags_regression(self):
        led = ledger.new_ledger("t")
        a1 = audit_with([helpers.sql_finding()], "20261001T000000Z")
        ledger.merge(led, a1, helpers.NOW, TODAY)
        ledger.merge(led, audit_with([], "20261002T000000Z"), helpers.NOW, TODAY)
        ledger.close(led, "FC-0001", "user confirmed fix", helpers.NOW)
        back = helpers.sql_finding(rule="application-security.unparameterized-query",
                                   title="Search query concatenates user input into raw SQL")
        back["evidence"][0]["quote"] = "return conn.execute(query).fetchall()"
        a2 = audit_with([back], "20261003T000000Z")
        changes = ledger.merge(led, a2, helpers.NOW, TODAY)
        self.assertEqual(a2["findings"][0]["id"], "FC-0001")
        self.assertEqual(changes["regressions"], ["FC-0001"])

    def test_different_issue_on_the_same_line_gets_a_new_id(self):
        led = ledger.new_ledger("t")
        a1 = audit_with([helpers.sql_finding()], "20261001T000000Z")
        ledger.merge(led, a1, helpers.NOW, TODAY)
        other = helpers.sql_finding(rule="application-security.unbounded-result-set",
                                    title="Search returns every matching row without a limit", severity="LOW")
        a2 = audit_with([other], "20261002T000000Z")
        changes = ledger.merge(led, a2, helpers.NOW, TODAY)
        self.assertEqual(changes["new"], ["FC-0002"])
        self.assertIn("FC-0001", changes["not_reproduced"])

    def test_ambiguous_reworded_match_gets_a_new_id(self):
        led = ledger.new_ledger("t")
        first = helpers.sql_finding(rule="application-security.sql-injection-a")
        second = helpers.sql_finding(local_id="F2", rule="application-security.sql-injection-b")
        a1 = audit_with([first, second], "20261001T000000Z")
        ledger.merge(led, a1, helpers.NOW, TODAY)
        a2 = audit_with([helpers.sql_finding(rule="application-security.sql-injection-c")], "20261002T000000Z")
        changes = ledger.merge(led, a2, helpers.NOW, TODAY)
        self.assertEqual(changes["new"], ["FC-0003"])

    def test_exact_match_wins_over_a_looser_one(self):
        led = ledger.new_ledger("t")
        a1 = audit_with([helpers.sql_finding()], "20261001T000000Z")
        ledger.merge(led, a1, helpers.NOW, TODAY)
        # The reworded finding sorts first; the exact finding must still get FC-0001.
        reworded = helpers.sql_finding(local_id="F0", rule="application-security.aaa-sql-injection")
        a2 = audit_with([reworded, helpers.sql_finding()], "20261002T000000Z")
        ledger.merge(led, a2, helpers.NOW, TODAY)
        ids = {f["rule"]: f["id"] for f in a2["findings"]}
        self.assertEqual(ids["application-security.sql-injection"], "FC-0001")
        self.assertEqual(ids["application-security.aaa-sql-injection"], "FC-0002")
        self.assertEqual(len(led["entries"]), 2)

    def test_domain_not_assessed_does_not_mark_not_reproduced(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.sql_finding()], "20261001T000000Z"), helpers.NOW, TODAY)
        changes = ledger.merge(led, audit_with([], "20261002T000000Z", coverage_status="NOT_ASSESSED"), helpers.NOW, TODAY)
        self.assertIn("FC-0001", changes["not_assessed_open"])
        self.assertEqual(ledger.find_entry(led, "FC-0001")[1]["status"], "OPEN")

    def test_remediation_requires_reaudit_verification(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.sql_finding()], "20261001T000000Z"), helpers.NOW, TODAY)
        ledger.record_remediation(led, "FC-0001", "parameterized the query", ["src/store.py"], helpers.NOW)
        # not observed but no verification -> stays REMEDIATED (pending)
        changes = ledger.merge(led, audit_with([], "20261002T000000Z"), helpers.NOW, TODAY)
        self.assertIn("FC-0001", changes["remediated_pending"])
        checks = [{"finding_id": "FC-0001", "result": "FIXED_VERIFIED", "notes": "query now uses a bound parameter"}]
        changes = ledger.merge(led, audit_with([], "20261003T000000Z", checks=checks), helpers.NOW, TODAY)
        self.assertIn("FC-0001", changes["verified_this_run"])
        self.assertEqual(ledger.find_entry(led, "FC-0001")[1]["status"], "VERIFIED")

    def test_remediated_but_still_observed_reopens(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.sql_finding()], "20261001T000000Z"), helpers.NOW, TODAY)
        ledger.record_remediation(led, "FC-0001", "attempted fix", [], helpers.NOW)
        a = audit_with([helpers.sql_finding()], "20261002T000000Z")
        changes = ledger.merge(led, a, helpers.NOW, TODAY)
        self.assertIn("FC-0001", changes["reopened"])
        self.assertEqual(a["findings"][0]["status"], "OPEN")

    def test_acceptance_requires_all_fields_and_expires(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.sql_finding()], "20261001T000000Z"), helpers.NOW, TODAY)
        with self.assertRaises(ledger.LedgerError):
            ledger.accept(led, "FC-0001", {"risk": "x risk", "reason": "", "owner": "a", "date": "2026-10-01", "scope": "s s s", "compensating_controls": "none"}, helpers.NOW, TODAY)
        fields = {"risk": "SQL injection in search", "reason": "internal tool only", "owner": "Product owner", "date": "2026-10-01",
                  "scope": "search endpoint", "compensating_controls": "network restricted", "review_date": "2026-10-10"}
        ledger.accept(led, "FC-0001", fields, helpers.NOW, TODAY)
        self.assertEqual(ledger.find_entry(led, "FC-0001")[1]["status"], "ACCEPTED_RISK")
        later = datetime.date(2026, 11, 1)
        a = audit_with([helpers.sql_finding()], "20261101T000000Z")
        changes = ledger.merge(led, a, helpers.NOW, later)
        self.assertIn("FC-0001", changes["expired_acceptances"])
        self.assertEqual(a["findings"][0]["status"], "OPEN")

    def test_legal_status_rules(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.deletion_finding()], "20261001T000000Z"), helpers.NOW, TODAY)
        with self.assertRaises(ledger.LedgerError):
            ledger.set_legal_status(led, "FC-0001", "DECISION_RECEIVED", "counsel said fine", "flight-check", helpers.NOW)
        with self.assertRaises(ledger.LedgerError):
            ledger.set_legal_status(led, "FC-0001", "COUNSEL_REVIEWED", "", "user", helpers.NOW)
        ledger.set_legal_status(led, "FC-0001", "LEGAL_REVIEW_REQUESTED", "sent packet", "user", helpers.NOW)
        ledger.set_legal_status(led, "FC-0001", "DECISION_RECEIVED", "User reports counsel advised implementing hard deletion within 30 days.", "user", helpers.NOW)
        with self.assertRaises(ledger.LedgerError):
            ledger.set_legal_status(led, "FC-0001", "OPEN", "", "user", helpers.NOW)
        entry = ledger.find_entry(led, "FC-0001")[1]
        self.assertEqual(entry["legal"]["status"], "DECISION_RECEIVED")
        self.assertEqual(entry["legal"]["history"][-1]["source"], "user")

    def test_closing_open_finding_is_refused(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.sql_finding()], "20261001T000000Z"), helpers.NOW, TODAY)
        with self.assertRaises(ledger.LedgerError):
            ledger.close(led, "FC-0001", "looks fine", helpers.NOW)

    def test_remediation_check_applies_even_when_domain_not_assessed(self):
        for result, expected in (("FIXED_VERIFIED", "VERIFIED"), ("NOT_FIXED", "OPEN")):
            led = ledger.new_ledger("t")
            a11y = helpers.sql_finding(domain="accessibility", rule="accessibility.untagged-pdf", control_ids=[])
            ledger.merge(led, audit_with([a11y], "20261001T000000Z"), helpers.NOW, TODAY)
            ledger.record_remediation(led, "FC-0001", "added language", ["pdf.py"], helpers.NOW)
            check = {"finding_id": "FC-0001", "result": result, "notes": "re-read pdf.py"}
            changes = ledger.merge(led, audit_with([], "20261005T000000Z", checks=[check]), helpers.NOW, TODAY)
            self.assertEqual(ledger.find_entry(led, "FC-0001")[1]["status"], expected, result)
            self.assertNotIn("FC-0001", changes["not_assessed_open"])
            if result == "NOT_FIXED":
                self.assertIn("FC-0001", changes["inconsistent_remediation"])

    def test_run_cannot_be_merged_twice(self):
        led = ledger.new_ledger("t")
        a = audit_with([helpers.sql_finding()], "20261001T000000Z")
        ledger.merge(led, a, helpers.NOW, TODAY)
        ledger.record_run(led, "20261001T000000Z", helpers.NOW, "x", "audit")
        with self.assertRaises(ledger.LedgerError):
            ledger.merge(led, audit_with([], "20261001T000000Z"), helpers.NOW, TODAY)


class GateTests(unittest.TestCase):
    def _final(self, findings, controls=None, coverage_status="ASSESSED"):
        led = ledger.new_ledger("t")
        a = audit_with(findings, controls=controls, coverage_status=coverage_status)
        ledger.merge(led, a, helpers.NOW, TODAY)
        for f in a["findings"]:
            f.setdefault("evidence_check", "PASSED")
        return a, led

    def test_ready_for_release(self):
        a, led = self._final([])
        self.assertEqual(gate.decide(a, led, TODAY)["decision"], constants.GATE_READY)

    def test_not_ready(self):
        a, led = self._final([helpers.sql_finding()])
        d = gate.decide(a, led, TODAY)
        self.assertEqual(d["decision"], constants.GATE_NOT_READY)
        self.assertIn("FC-0001", d["blocking_findings"])

    def test_critical(self):
        a, led = self._final([helpers.sql_finding(severity="CRITICAL")])
        self.assertEqual(gate.decide(a, led, TODAY)["decision"], constants.GATE_CRITICAL)

    def test_failed_evidence_turns_critical_into_insufficient_evidence(self):
        a, led = self._final([helpers.sql_finding(severity="CRITICAL")])
        a["findings"][0]["evidence_check"] = "FAILED"
        self.assertEqual(gate.decide(a, led, TODAY)["decision"], constants.GATE_INSUFFICIENT)

    def test_unverified_release_critical_control_blocks(self):
        controls = [{"id": c["id"], "state": "UNVERIFIED" if c["id"] == "SUPPLY-KNOWN-VULNS" else "NOT_APPLICABLE",
                     "release_critical": c["release_critical"], "domain": c["domain"], "missing_evidence": "no scan"} for c in catalog.controls()]
        a, led = self._final([], controls=controls)
        d = gate.decide(a, led, TODAY)
        self.assertEqual(d["decision"], constants.GATE_INSUFFICIENT)
        led["control_acceptances"]["SUPPLY-KNOWN-VULNS"] = {
            "risk": "deps unscanned", "reason": "scanner rollout pending", "owner": "Eng lead", "date": "2026-10-01",
            "scope": "this release", "compensating_controls": "lockfile pinned", "source": "user", "recorded_at": "2026-10-03T09:00:00Z"}
        self.assertEqual(gate.decide(a, led, TODAY)["decision"], constants.GATE_READY_ACCEPTED)

    def test_acceptance_recorded_during_the_run_does_not_count(self):
        controls = [{"id": c["id"], "state": "UNVERIFIED" if c["id"] == "SUPPLY-KNOWN-VULNS" else "NOT_APPLICABLE",
                     "release_critical": c["release_critical"], "domain": c["domain"], "missing_evidence": "no scan"} for c in catalog.controls()]
        a, led = self._final([], controls=controls)
        acc = {"risk": "deps unscanned", "reason": "scanner rollout pending", "owner": "Eng lead", "date": "2026-10-01",
               "scope": "this release", "compensating_controls": "lockfile pinned", "source": "user"}
        for recorded in ("2026-10-04T12:00:00Z", "2026-10-04T12:30:00Z", None):
            led["control_acceptances"]["SUPPLY-KNOWN-VULNS"] = dict(acc, recorded_at=recorded) if recorded else dict(acc)
            d = gate.decide(a, led, TODAY)
            self.assertEqual(d["decision"], constants.GATE_INSUFFICIENT, recorded)
            self.assertEqual(d["accepted_controls"], [])
            self.assertTrue(any("SUPPLY-KNOWN-VULNS" in r for r in d["acceptances_not_counted"]), d["reasons"])

    def test_late_finding_acceptance_keeps_the_finding_open(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.sql_finding()], "20261001T000000Z"), helpers.NOW, TODAY)
        fields = {"risk": "sql risk", "reason": "internal tool", "owner": "Eng lead", "date": "2026-10-04",
                  "scope": "admin page", "compensating_controls": "VPN only"}
        ledger.accept(led, "FC-0001", fields, "2026-10-04T12:05:00Z", TODAY)
        a = audit_with([helpers.sql_finding()], "20261004T120000Z")
        ledger.merge(led, a, "2026-10-04T12:10:00Z", TODAY)
        a["findings"][0]["evidence_check"] = "PASSED"
        d = gate.decide(a, led, TODAY)
        self.assertEqual(d["decision"], constants.GATE_NOT_READY)
        self.assertEqual(d["accepted_findings"], [])
        self.assertTrue(any("after this run started" in r for r in d["reasons"]))

    def test_timely_finding_acceptance_counts(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.sql_finding()], "20261001T000000Z"), helpers.NOW, TODAY)
        fields = {"risk": "sql risk", "reason": "internal tool", "owner": "Eng lead", "date": "2026-10-02",
                  "scope": "admin page", "compensating_controls": "VPN only"}
        ledger.accept(led, "FC-0001", fields, "2026-10-02T08:00:00Z", TODAY)
        a = audit_with([helpers.sql_finding()], "20261004T120000Z")
        ledger.merge(led, a, "2026-10-04T12:10:00Z", TODAY)
        a["findings"][0]["evidence_check"] = "PASSED"
        d = gate.decide(a, led, TODAY)
        self.assertEqual(d["decision"], constants.GATE_READY_ACCEPTED)
        self.assertEqual(d["accepted_findings"], ["FC-0001"])

    def test_accepted_critical_finding_still_blocks(self):
        led = ledger.new_ledger("t")
        ledger.merge(led, audit_with([helpers.sql_finding(severity="CRITICAL")], "20261001T000000Z"), helpers.NOW, TODAY)
        fields = {"risk": "sql risk", "reason": "deadline", "owner": "Eng lead", "date": "2026-10-02",
                  "scope": "everything", "compensating_controls": "none"}
        ledger.accept(led, "FC-0001", fields, "2026-10-02T08:00:00Z", TODAY)
        a = audit_with([helpers.sql_finding(severity="CRITICAL")], "20261004T120000Z")
        ledger.merge(led, a, "2026-10-04T12:10:00Z", TODAY)
        a["findings"][0]["evidence_check"] = "PASSED"
        d = gate.decide(a, led, TODAY)
        self.assertEqual(d["decision"], constants.GATE_CRITICAL)
        self.assertEqual(d["accepted_critical_findings"], ["FC-0001"])
        self.assertEqual(d["accepted_findings"], [])
        self.assertTrue(any("never lifts the block" in r for r in d["reasons"]))

    def test_run_start_uses_the_earlier_of_run_id_and_run_json(self):
        self.assertEqual(gate.run_started_at({"run_id": "20261004T120000Z"}), "2026-10-04T12:00:00Z")
        self.assertEqual(gate.run_started_at({"run_id": "20261004T120000Z-reaudit", "started_at": "2026-10-05T00:00:00Z"}),
                         "2026-10-04T12:00:00Z")
        self.assertEqual(gate.run_started_at({"run_id": "20261004T120000Z", "started_at": "2026-10-04T11:00:00Z"}),
                         "2026-10-04T11:00:00Z")
        self.assertEqual(gate.run_started_at({"run_id": "20261004T120000Z", "started_at": "yesterday"}), "2026-10-04T12:00:00Z")
        self.assertIsNone(gate.run_started_at({}))

    def test_unassessed_domain_blocks(self):
        a, led = self._final([], coverage_status="NOT_ASSESSED")
        self.assertEqual(gate.decide(a, led, TODAY)["decision"], constants.GATE_INSUFFICIENT)

    def test_low_finding_does_not_block_but_high_potential_does(self):
        a, led = self._final([helpers.sql_finding(severity="LOW", release_blocking=False)])
        self.assertEqual(gate.decide(a, led, TODAY)["decision"], constants.GATE_READY)
        a, led = self._final([helpers.sql_finding(severity="HIGH", confidence="POTENTIAL", release_blocking=False)])
        self.assertEqual(gate.decide(a, led, TODAY)["decision"], constants.GATE_READY)
        a, led = self._final([helpers.sql_finding(severity="HIGH", confidence="UNVERIFIED", release_blocking=False)])
        self.assertEqual(gate.decide(a, led, TODAY)["decision"], constants.GATE_INSUFFICIENT)

    def test_exit_codes_distinct(self):
        self.assertEqual(len(set(constants.GATE_EXIT_CODES.values())), 5)


if __name__ == "__main__":
    unittest.main()
