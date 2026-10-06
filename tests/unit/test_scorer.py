import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402

sys.path.insert(0, os.path.join(helpers.REPO, "tools", "blindtest"))
import score  # noqa: E402


def finding(fid, domain, path, start, end, severity="HIGH", conf="CONFIRMED", legal=None, check="PASSED"):
    f = {"id": fid, "title": "t " + fid, "domain": domain, "severity": severity, "effective_confidence": conf,
         "evidence_check": check, "evidence": [{"kind": "code", "path": path, "start_line": start, "end_line": end}]}
    if legal:
        f["legal"] = legal
    return f


def final_with(findings, coverage=None):
    return {"run": {"run_id": "r1"}, "gate": {"decision": "NOT READY — REMEDIATION REQUIRED"},
            "findings": findings, "coverage": coverage or [{"domain": "payments", "status": "NOT_APPLICABLE"}]}


MANIFEST = {
    "fixture": {"name": "unit"},
    "issues": [
        {"id": "GT-1", "domains": ["authorization"], "severity": "HIGH", "tier": "must",
         "locations": [{"path": "src/a.ts", "start_line": 40, "end_line": 50}]},
        {"id": "GT-2", "domains": ["privacy", "documentation"], "severity": "MEDIUM", "tier": "must",
         "locations": [{"path": "docs/p.md", "start_line": 3, "end_line": 3}],
         "legal": {"classification": "POLICY/IMPLEMENTATION CONTRADICTION"}},
        {"id": "GT-3", "domains": ["reliability"], "severity": "LOW", "tier": "stretch",
         "locations": [{"path": "src/jobs.ts", "start_line": 1, "end_line": 9}]},
    ],
    "secure_controls": [{"id": "SC-1", "domains": ["application-security"], "locations": [{"path": "src/db.ts", "start_line": 10, "end_line": 20}]}],
    "expected_not_applicable_domains": ["payments"],
}


class ScorerTests(unittest.TestCase):
    def test_matching_decoys_unlisted_and_metrics(self):
        findings = [
            finding("FC-1", "authorization", "src/a.ts", 44, 47),
            finding("FC-2", "privacy", "docs/p.md", 2, 4, severity="HIGH",
                    legal={"classification": "POLICY/IMPLEMENTATION CONTRADICTION", "questions": ["q?"]}),
            finding("FC-3", "application-security", "src/db.ts", 12, 13),
            finding("FC-4", "testing", "src/other.ts", 1, 2, check="FAILED"),
            finding("FC-5", "authorization", "src/a.ts", 45, 46),  # second finding on same issue -> unlisted, with a hint
        ]
        r = score.score(final_with(findings), MANIFEST)
        self.assertEqual(r["counts"]["true_positives"], 2)
        self.assertEqual(r["counts"]["decoy_false_positives"], 1)
        self.assertEqual(r["counts"]["unlisted_findings"], 2)
        self.assertEqual(r["counts"]["unlisted_possible_repeats"], 1)
        hints = {u["finding"]: u["possible_repeat_of"] for u in r["unlisted_for_adjudication"]}
        self.assertEqual(hints, {"FC-4": [], "FC-5": ["GT-1"]})
        self.assertAlmostEqual(r["recall"], 0.667, places=3)
        self.assertEqual(r["must_recall"], 1.0)
        self.assertEqual(r["decoy_false_positive_rate"], 1.0)
        self.assertEqual(r["fabricated_evidence_findings"], ["FC-4"])
        self.assertEqual(r["legal"], {"issues": 1, "classification_correct": 1, "with_questions": 1})
        self.assertEqual(r["severity_exact"], 0.5)
        self.assertEqual(r["severity_within_tolerance"], 1.0)
        self.assertEqual(r["not_applicable_errors"]["expected_na_not_marked"], [])
        md = score.to_markdown(r)
        self.assertIn("GT-3", md)

    def test_domain_mismatch_is_not_a_match(self):
        r = score.score(final_with([finding("FC-1", "testing", "src/a.ts", 44, 47)]), MANIFEST)
        self.assertEqual(r["counts"]["true_positives"], 0)
        # ...but it is reported as a location-only match and not as an unlisted finding
        self.assertEqual(r["location_only_matches"], 1)
        self.assertEqual(r["counts"]["unlisted_findings"], 0)
        self.assertAlmostEqual(r["recall_location_only"], 0.333, places=3)
        row = [p for p in r["per_issue"] if p["issue"] == "GT-1"][0]
        self.assertFalse(row["detected"])
        self.assertTrue(row["detected_location_only"])
        self.assertEqual(row["finding_domain"], "testing")

    def test_stability_summary_across_runs(self):
        import stability
        findings_a = [finding("SW-1", "authorization", "src/a.ts", 44, 47), finding("SW-3", "application-security", "src/db.ts", 12, 13)]
        findings_b = [finding("SW-9", "reliability", "src/jobs.ts", 2, 3)]
        runs = [score.score(final_with(findings_a), MANIFEST), score.score(final_with(findings_b), MANIFEST)]
        r = stability.summarize(runs)
        self.assertEqual(r["runs"], 2)
        self.assertEqual(r["detection_frequency"], {"GT-1": 1, "GT-2": 0, "GT-3": 1})
        self.assertEqual((r["detected_in_every_run"], r["detected_in_some_runs"], r["never_detected"]), (0, 2, 1))
        self.assertEqual(r["must_never_detected"], 1)
        self.assertEqual(r["mean_pairwise_jaccard_of_detected_sets"], 0.0)
        self.assertEqual(r["metrics"]["decoy_false_positive_rate"], {"mean": 0.5, "min": 0.0, "max": 1.0})
        self.assertIn("2 runs", stability.to_markdown(r))
        with self.assertRaises(ValueError):
            stability.summarize([runs[0], dict(runs[1], fixture={"name": "other"})])

    def test_manifest_validation(self):
        bad = {"issues": [{"id": "X", "domains": [], "severity": "SEVERE", "locations": []}]}
        self.assertTrue(score.validate_manifest(bad))


if __name__ == "__main__":
    unittest.main()
