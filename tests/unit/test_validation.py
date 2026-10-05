import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import catalog, minischema, validate  # noqa: E402

try:
    import jsonschema
except ImportError:  # optional cross-check
    jsonschema = None


def part_errors(part):
    errors, warnings = [], []
    validate.check_part(part, "part-test.json", errors, warnings)
    return errors


class PartValidationTests(unittest.TestCase):
    def test_valid_part_passes(self):
        self.assertEqual(part_errors(helpers.base_part()), [])

    def test_how_to_verify_only_on_unverified_controls(self):
        part = helpers.base_part()
        part["controls"].append({"id": "SUPPLY-KNOWN-VULNS", "state": "UNVERIFIED", "missing_evidence": "No scanner output was imported.",
                                 "how_to_verify": "Import pip-audit JSON for the audited revision with --evidence."})
        self.assertEqual(part_errors(part), [])
        part["controls"][-1]["state"] = "VERIFIED"
        part["controls"][-1]["rationale"] = "Scanned."
        self.assertTrue(any("how_to_verify belongs only on UNVERIFIED" in e for e in part_errors(part)))

    def test_legal_classification_without_review_flag_is_warned_and_fixed_at_assembly(self):
        from flight_check_lib import assemble
        f = helpers.deletion_finding()
        f["human_review"] = {"required": True, "types": ["privacy"], "reason": "Deletion behavior."}
        errors, warnings = [], []
        validate.check_part(helpers.base_part([helpers.sql_finding(), f]), "part-test.json", errors, warnings)
        self.assertEqual(errors, [])
        self.assertTrue(any("adds it when the run is assembled" in w for w in warnings))
        self.assertTrue(assemble.require_legal_review(f))
        self.assertTrue(f["human_review"]["required"])
        self.assertIn("legal", f["human_review"]["types"])
        self.assertIn("privacy", f["human_review"]["types"])
        self.assertFalse(assemble.require_legal_review(f))  # idempotent
        biz = helpers.deletion_finding(legal={"classification": "BUSINESS DECISION REQUIRED", "why_review": "Pricing choice.",
                                              "decisions_needed": ["Which refund window applies?"]},
                                       human_review={"required": False})
        self.assertTrue(assemble.require_legal_review(biz))
        self.assertEqual(biz["human_review"]["types"], ["business"])
        self.assertTrue(biz["human_review"]["reason"])
        plain = helpers.sql_finding()
        self.assertFalse(assemble.require_legal_review(plain))
        self.assertEqual(plain["human_review"], {"required": False})

    def test_missing_counterevidence_fails(self):
        part = helpers.base_part([helpers.sql_finding(counterevidence=[])])
        part["controls"] = []
        self.assertTrue(any("counterevidence" in e for e in part_errors(part)))

    def test_located_evidence_requires_quote(self):
        f = helpers.sql_finding()
        del f["evidence"][0]["quote"]
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("needs quote" in e for e in part_errors(part)))

    def test_absence_requires_searched(self):
        f = helpers.sql_finding(evidence=[{"kind": "absence"}])
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("absence evidence" in e for e in part_errors(part)))

    def test_path_traversal_rejected(self):
        f = helpers.sql_finding()
        f["evidence"][0]["path"] = "../outside.py"
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("'..'" in e for e in part_errors(part)))
        f["evidence"][0]["path"] = "/etc/passwd"
        self.assertTrue(any("relative" in e for e in part_errors(part)))

    def test_legal_overclaim_rejected_but_quoted_claim_allowed(self):
        f = helpers.deletion_finding(explanation="The service is GDPR compliant because deletion exists, so nothing else is needed here.")
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("prohibited claim" in e for e in part_errors(part)))
        f = helpers.deletion_finding(explanation="The policy states \"we are GDPR compliant\" but deletion only sets a flag, so the claim is unsupported by implementation.")
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertEqual(part_errors(part), [])

    def test_violation_and_enforceability_conclusions_rejected(self):
        for text in ("You are violating privacy law by keeping this data for longer than allowed.",
                     "This limitation clause is enforceable in every jurisdiction where the service operates.",
                     "The application is secure and ready for any production deployment scenario."):
            f = helpers.deletion_finding(explanation=text)
            part = helpers.base_part([f])
            part["controls"] = []
            self.assertTrue(any("prohibited claim" in e for e in part_errors(part)), text)

    def test_vague_title_rejected(self):
        f = helpers.sql_finding(title="Security could be improved")
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("vague" in e for e in part_errors(part)))

    def test_legal_classification_needs_specific_questions(self):
        f = helpers.deletion_finding()
        f["legal"]["questions"] = []
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("question for counsel" in e for e in part_errors(part)))
        f["legal"]["questions"] = ["Is this legal?"]
        errs = part_errors(part)
        self.assertTrue(any("too generic" in e or "shorter than" in e for e in errs), errs)
        f["legal"]["questions"] = ["Counsel should determine the retention exceptions for backups"]
        self.assertTrue(any("ending in '?'" in e for e in part_errors(part)))

    def test_legal_finding_without_review_flag_is_a_warning_not_an_error(self):
        f = helpers.deletion_finding(human_review={"required": False})
        part = helpers.base_part([f])
        part["controls"] = []
        errors, warnings = [], []
        validate.check_part(part, "part-test.json", errors, warnings)
        self.assertFalse(any("human_review.required" in e for e in errors))
        self.assertTrue(any("human_review.required" in w for w in warnings))

    def test_business_decision_needs_decisions(self):
        f = helpers.deletion_finding()
        f["legal"] = {"classification": "BUSINESS DECISION REQUIRED"}
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("decisions_needed" in e for e in part_errors(part)))

    def test_rule_must_match_domain(self):
        f = helpers.sql_finding(rule="privacy.sql-injection")
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("must start with its domain" in e for e in part_errors(part)))

    def test_control_state_rules(self):
        part = helpers.base_part([])
        part["controls"] = [{"id": "APPSEC-INJECTION", "state": "NOT_MET"}]
        self.assertTrue(any("NOT_MET must reference" in e for e in part_errors(part)))
        part["controls"] = [{"id": "APPSEC-INJECTION", "state": "UNVERIFIED"}]
        self.assertTrue(any("missing_evidence" in e for e in part_errors(part)))
        part["controls"] = [{"id": "APPSEC-INJECTION", "state": "VERIFIED", "evidence": [{"kind": "absence", "searched": "x"}]}]
        self.assertTrue(any("positive evidence" in e for e in part_errors(part)))
        part["controls"] = [{"id": "APPSEC-INJECTION", "state": "NOT_APPLICABLE", "rationale": "none"}]
        self.assertTrue(any("NOT_APPLICABLE needs evidence" in e for e in part_errors(part)))
        part["controls"] = [{"id": "NOT-A-CONTROL", "state": "UNVERIFIED", "missing_evidence": "x"}]
        self.assertTrue(any("unknown control" in e for e in part_errors(part)))

    def test_assessed_coverage_requires_searches(self):
        part = helpers.base_part()
        part["coverage"][0]["searches"] = []
        self.assertTrue(any("ASSESSED must list" in e for e in part_errors(part)))

    def test_informational_cannot_block(self):
        f = helpers.sql_finding(severity="INFORMATIONAL", release_blocking=True)
        part = helpers.base_part([f])
        part["controls"] = []
        self.assertTrue(any("INFORMATIONAL" in e for e in part_errors(part)))

    def test_run_dir_validation_reports_missing_files(self):
        import tempfile
        d = tempfile.mkdtemp()
        errors, _ = validate.validate_run_dir(d)
        self.assertTrue(any("run.json: missing" in e for e in errors))
        self.assertTrue(any("no part-" in e for e in errors))


@unittest.skipIf(jsonschema is None, "jsonschema not installed")
class SchemaAgreementTests(unittest.TestCase):
    """The built-in validator must agree with a reference JSON Schema implementation."""

    def _both(self, doc, definition):
        schema = catalog.schema()
        mini = minischema.validate_def(doc, schema, definition)
        wrapper = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/%s" % definition}
        ref = list(jsonschema.Draft202012Validator(wrapper).iter_errors(doc))
        return bool(mini), bool(ref)

    def test_agreement_on_valid_and_invalid_documents(self):
        cases = [(helpers.base_part(), "part"), (helpers.base_inventory(), "inventory"), (helpers.base_run(), "run")]
        bad_part = helpers.base_part()
        bad_part["findings"][0]["severity"] = "SEVERE"
        cases.append((bad_part, "part"))
        bad_part2 = copy.deepcopy(helpers.base_part())
        bad_part2["findings"][0]["unexpected"] = 1
        cases.append((bad_part2, "part"))
        bad_run = helpers.base_run()
        bad_run["run_id"] = "today"
        cases.append((bad_run, "run"))
        for doc, definition in cases:
            mini_invalid, ref_invalid = self._both(json.loads(json.dumps(doc)), definition)
            self.assertEqual(mini_invalid, ref_invalid, (definition, doc if len(json.dumps(doc)) < 300 else definition))


if __name__ == "__main__":
    unittest.main()
