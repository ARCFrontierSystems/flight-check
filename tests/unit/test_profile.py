"""Project profile, applicability, corrections, and overrides (Phase 4)."""

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import assemble, catalog, minischema, profile, state  # noqa: E402
from test_state import run_cli  # noqa: E402

T1, T2, T3 = "2026-10-13T10:00:00Z", "2026-10-13T11:00:00Z", "2026-10-13T12:00:00Z"
ROOT = helpers.FIXTURE_ROOT


def inventory(**over):
    inv = helpers.base_inventory()
    inv["items"] += [
        {"aspect": "Payment provider", "value": "No payment code found", "state": "NOT_FOUND",
         "evidence": [{"kind": "absence", "searched": "Grep for payment SDK names"}]},
        {"aspect": "Hosting environment", "value": "Unclear; a deploy note mentions a VM", "state": "UNVERIFIED"},
        {"aspect": "Environment configuration", "value": "DB_PASSWORD=" + "hunter2-" + "x9Kq7Lm2Vp", "state": "IDENTIFIED",
         "evidence": [{"kind": "code", "path": "src/store.py", "start_line": 2, "end_line": 2, "quote": "import sqlite3"}]},
        {"aspect": "Quantum widgets", "value": "none", "state": "NOT_FOUND",
         "evidence": [{"kind": "absence", "searched": "looked"}]},
    ]
    inv.update(over)
    return inv


def project():
    return state.new_project(state.new_identity(None, "p", T1))


def fact(proj, aspect):
    return [f for f in proj["profile"]["facts"] if f["aspect"] == aspect and f["status"] == "current"]


def valid(proj):
    errs = minischema.validate_def(json.loads(json.dumps(proj)), catalog.schema(), "projectState")
    return errs + state.semantic_errors(proj, None)


class MergeTests(unittest.TestCase):
    def test_inventory_becomes_evidence_backed_facts(self):
        p = project()
        profile.merge(p, inventory(), ROOT, T1, "r1")
        self.assertEqual(valid(p), [])
        lang = fact(p, "language")[0]
        self.assertEqual((lang["origin"], lang["source"], lang["presence"]), ("observed", "flight-check", "present"))
        with open(os.path.join(ROOT, "src", "store.py"), "rb") as fh:
            self.assertEqual(lang["evidence"][0]["sha256"], hashlib.sha256(fh.read()).hexdigest())
        self.assertNotIn("quote", lang["evidence"][0])
        pay = fact(p, "payments.provider")[0]
        self.assertEqual((pay["origin"], pay["presence"]), ("observed", "absent"))
        self.assertEqual(fact(p, "deployment.hosting")[0]["origin"], "unknown")
        self.assertNotIn("x9Kq7Lm2Vp", fact(p, "config.environment")[0]["value"])
        self.assertTrue(fact(p, "other.quantum-widgets"))

    def test_repeat_change_and_disappearance(self):
        p = project()
        profile.merge(p, inventory(), ROOT, T1, "r1")
        n = len(p["profile"]["facts"])
        changes = profile.merge(p, inventory(), ROOT, T2, "r2")
        self.assertEqual(len(p["profile"]["facts"]), n)
        self.assertEqual([c for c in changes if c[0] in ("new", "superseded", "historical")], [])
        self.assertEqual(fact(p, "language")[0]["last_observed"], T2)
        inv = inventory()
        inv["items"][0]["value"] = "Python and Go"
        inv["items"] = [i for i in inv["items"] if i["aspect"] != "Quantum widgets"]
        changes = profile.merge(p, inv, ROOT, T3, "r3")
        kinds = sorted(c[0] for c in changes if c[0] in ("new", "superseded", "historical"))
        self.assertEqual(kinds, ["historical", "superseded"])
        self.assertEqual(fact(p, "language")[0]["value"], "Python and Go")
        old = [f for f in p["profile"]["facts"] if f["aspect"] == "language" and f["status"] == "superseded"]
        self.assertEqual(len(old), 1)
        self.assertEqual([f["status"] for f in p["profile"]["facts"] if f["aspect"] == "other.quantum-widgets"], ["historical"])
        self.assertEqual(valid(p), [])

    def test_out_of_root_evidence_is_dropped(self):
        inv = inventory()
        inv["items"][0]["evidence"].append({"kind": "code", "path": "../../etc/passwd", "start_line": 1, "end_line": 1, "quote": "x"})
        p = project()
        profile.merge(p, inv, ROOT, T1, "r1")
        self.assertEqual([r["path"] for r in fact(p, "language")[0]["evidence"]], ["src/store.py"])


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.p = project()
        profile.merge(self.p, inventory(), ROOT, T1, "r1")
        self.lang = fact(self.p, "language")[0]

    def test_correction_contradicted_by_evidence_is_flagged_not_resolved(self):
        f = profile.correct(self.p, self.lang["id"], "not_used", "Python is only used in tests", T2, by="owner")
        self.assertTrue(f["contradiction"])
        self.assertEqual(len(f["evidence"]), 1)  # the evidence stays
        changes = profile.merge(self.p, inventory(), ROOT, T3, "r2")
        self.assertTrue(fact(self.p, "language")[0]["contradiction"])
        profile.withdraw_corrections(self.p, f["id"], "my mistake", T3)
        self.assertFalse(f["contradiction"])
        self.assertEqual([c["active"] for c in f["corrections"]], [False])
        self.assertEqual(valid(self.p), [])
        self.assertEqual(changes, [c for c in changes if c[0] != "contradiction"])  # already flagged before

    def test_corrections_carry_over_to_a_new_observation(self):
        profile.correct(self.p, self.lang["id"], "scope", "only the CLI", T2, value="cli/")
        inv = inventory()
        inv["items"][0]["value"] = "Python 3.12"
        profile.merge(self.p, inv, ROOT, T3, "r2")
        new = fact(self.p, "language")[0]
        self.assertNotEqual(new["id"], self.lang["id"])
        self.assertEqual([c["assertion"] for c in new["corrections"]], ["scope"])
        self.assertFalse(new["contradiction"])

    def test_declarations_sit_beside_observations(self):
        d1 = profile.declare(self.p, "deployment.model", "offline desktop app only", "product decision", T2, by="owner")
        self.assertEqual((d1["origin"], d1["source"]), ("declared", "user"))
        d2 = profile.declare(self.p, "Deployment model", "offline desktop and kiosk", "added kiosk", T3)
        self.assertEqual(d2["aspect"], "deployment.model")
        self.assertEqual(d1["status"], "superseded")
        with self.assertRaises(profile.ProfileError):
            profile.correct(self.p, d2["id"], "not_used", "x", T3)
        self.assertEqual(valid(self.p), [])

    def test_replace_needs_a_value_and_unknown_fact_is_refused(self):
        with self.assertRaises(profile.ProfileError):
            profile.correct(self.p, self.lang["id"], "replace", "wrong", T2)
        with self.assertRaises(profile.ProfileError):
            profile.correct(self.p, "PF-9999", "not_used", "x", T2)


class ApplicabilityTests(unittest.TestCase):
    def entries(self, p):
        return {a["domain"]: a for a in p["applicability"]}

    def test_classifications_and_missing_absence_evidence(self):
        inv = inventory()
        for a in inv["applicability"]:
            if a["domain"] == "payments":
                a["evidence"] = []  # "no" without saying what was searched
            if a["domain"] == "ai-llm":
                a["applicable"] = "unknown"
        p = project()
        profile.merge(p, inv, ROOT, T1, "r1")
        e = self.entries(p)
        self.assertEqual(e["application-security"]["classification"], "applicable")
        self.assertEqual(e["supply-chain"]["classification"], "not_applicable")
        self.assertEqual(e["payments"]["classification"], "uncertain")
        self.assertIn("without evidence", e["payments"]["reasoning"])
        self.assertEqual(e["ai-llm"]["classification"], "uncertain")
        self.assertEqual(len(e), len(catalog.domain_ids()))
        self.assertEqual(valid(p), [])

    def test_overrides_never_hide_evidence(self):
        inv = inventory()
        for a in inv["applicability"]:
            if a["domain"] == "ai-llm":
                a["applicable"], a["evidence"] = "unknown", []
        p = project()
        profile.merge(p, inv, ROOT, T1, "r1")
        entry, _ = profile.override(p, "ai-llm", "not_applicable", "no AI features", T2, by="owner")
        self.assertEqual((profile.effective(entry), entry["contradiction"]), ("not_applicable", False))
        entry, _ = profile.override(p, "privacy", "not_applicable", "we think not", T2)
        self.assertEqual((profile.effective(entry), entry["contradiction"]), ("applicable", True))
        entry, _ = profile.override(p, "supply-chain", "applicable", "we ship dependencies", T2)
        self.assertEqual((profile.effective(entry), entry["contradiction"]), ("applicable", True))
        # A later run that finds AI code flags the earlier override instead of trusting it.
        for a in inv["applicability"]:
            if a["domain"] == "ai-llm":
                a["applicable"] = "yes"
        changes = profile.merge(p, inv, ROOT, T3, "r2")
        self.assertIn(("override_contradiction", "ai-llm", "not_applicable"), changes)
        self.assertIn(("applicability", "ai-llm", ["uncertain", "applicable"]), changes)
        self.assertEqual(profile.effective(self.entries(p)["ai-llm"]), "applicable")
        entry, old = profile.override(p, "ai-llm", None, "withdrawn", T3)
        self.assertEqual((old, entry["override"], entry["contradiction"]), ("not_applicable", None, False))
        self.assertEqual(valid(p), [])


class AssembleOverrideTests(unittest.TestCase):
    def coverage(self, inv, overrides):
        tmp = tempfile.mkdtemp(prefix="flight-check-profile-")
        self.addCleanup(shutil.rmtree, tmp, True)
        run_dir = helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())], inventory=inv, base=tmp)
        audit, _ = assemble.assemble(run_dir, overrides)
        return {c["domain"]: c for c in audit["coverage"]}

    def test_overrides_in_coverage(self):
        inv = helpers.base_inventory()
        for a in inv["applicability"]:
            if a["domain"] == "ai-llm":
                a["applicable"], a["evidence"] = "unknown", []
            if a["domain"] == "payments":
                a["evidence"] = []
        decl = {"classification": "not_applicable", "source": "user", "at": T1, "reason": "no AI features", "by": "owner"}
        cov = self.coverage(inv, {})
        self.assertEqual(cov["ai-llm"]["status"], "NOT_ASSESSED")
        self.assertEqual(cov["payments"]["status"], "NOT_ASSESSED")  # "no" without evidence is not enough
        self.assertEqual(cov["supply-chain"]["status"], "NOT_APPLICABLE")
        cov = self.coverage(inv, {"ai-llm": decl, "supply-chain": dict(decl, classification="applicable")})
        self.assertEqual(cov["ai-llm"]["status"], "NOT_APPLICABLE")
        self.assertEqual(cov["ai-llm"]["reported_by"], ["owner"])
        self.assertIn("Declared not applicable by the project owner (owner)", cov["ai-llm"]["rationale"])
        self.assertEqual(cov["supply-chain"]["status"], "NOT_ASSESSED")
        inv2 = copy.deepcopy(inv)
        for a in inv2["applicability"]:
            if a["domain"] == "ai-llm":
                a["applicable"] = "yes"
        cov = self.coverage(inv2, {"ai-llm": decl})
        self.assertEqual(cov["ai-llm"]["status"], "NOT_ASSESSED")
        self.assertIn("inventory found it applies", cov["ai-llm"]["rationale"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="flight-check-profile-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.led = os.path.join(self.tmp, "ledger.json")
        inv = inventory()
        for a in inv["applicability"]:
            if a["domain"] == "ai-llm":
                a["applicable"], a["evidence"] = "unknown", []
        run_dir = helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())], inventory=inv, base=self.tmp)
        code, out = run_cli("finalize", run_dir, "--root", ROOT, "--ledger", self.led, "--now", T1)
        self.assertEqual(code, 0, out)

    def test_profile_and_applicability_commands(self):
        code, out = run_cli("profile", "show", "--state-dir", self.tmp)
        self.assertEqual(code, 0, out)
        lang = next(f for f in out["facts"] if f["aspect"] == "language")
        self.assertEqual(lang["evidence"], ["src/store.py"])
        code, out = run_cli("profile", "correct", "--state-dir", self.tmp, "--id", lang["id"], "--assertion", "excluded",
                            "--reason", "vendored tool", "--by", "owner", "--now", T2)
        self.assertEqual((code, out["contradiction"]), (0, True), out)
        code, out = run_cli("applicability", "override", "--state-dir", self.tmp, "--domain", "ai-llm",
                            "--classification", "not_applicable", "--reason", "no AI features", "--now", T2)
        self.assertEqual((code, out["effective"]), (0, "not_applicable"), out)
        code, out = run_cli("applicability", "show", "--state-dir", self.tmp)
        ai = next(d for d in out["domains"] if d["domain"] == "ai-llm")
        self.assertEqual((ai["classification"], ai["effective"]), ("uncertain", "not_applicable"))
        st = state.load(self.tmp)
        self.assertEqual([(e["actor"], e["command"], e["record"]) for e in st.events[-2:]],
                         [("user", "profile correct", lang["id"]), ("user", "applicability override", "domain:ai-llm")])
        self.assertEqual(run_cli("state", "check", "--state-dir", self.tmp)[0], 0)
        # The next audit honors the override for the unassessed, uncertain domain.
        run_dir = helpers.write_run(parts=[helpers.all_controls_resolved(helpers.base_part())],
                                    inventory=helpers.base_inventory(), base=self.tmp,
                                    run=dict(helpers.base_run(), run_id="20261013T120000Z"))
        inv_path = os.path.join(run_dir, "inventory.json")
        with open(inv_path) as fh:
            inv = json.load(fh)
        for a in inv["applicability"]:
            if a["domain"] == "ai-llm":
                a["applicable"], a["evidence"] = "unknown", []
        with open(inv_path, "w") as fh:
            json.dump(inv, fh)
        code, out = run_cli("finalize", run_dir, "--root", ROOT, "--ledger", self.led, "--now", T3)
        self.assertEqual(code, 0, out)
        with open(os.path.join(run_dir, "audit.final.json")) as fh:
            cov = {c["domain"]: c for c in json.load(fh)["coverage"]}
        self.assertEqual((cov["ai-llm"]["status"], cov["ai-llm"]["reported_by"]), ("NOT_APPLICABLE", ["owner"]))

    def test_user_commands_need_existing_state(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        code, out = run_cli("applicability", "override", "--state-dir", empty, "--domain", "payments",
                            "--classification", "not_applicable", "--reason", "x", "--now", T2)
        self.assertEqual(code, 1)
        self.assertIn("not initialized", out["error"])
        code, out = run_cli("applicability", "override", "--state-dir", self.tmp, "--domain", "nonsense",
                            "--classification", "not_applicable", "--reason", "x")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
