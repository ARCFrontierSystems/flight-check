"""Schema contract for the Flight Check 1.5 project state files (docs/design/project-state.md).

The state code does not exist yet; these tests pin the data model the design document describes, so the
implementation phases build against a reviewed contract.
"""

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import helpers  # noqa: E402
from flight_check_lib import catalog, minischema  # noqa: E402

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional reference validator
    jsonschema = None

T = "2026-10-12T22:20:35Z"
H64 = "a" * 64
EV = {"kind": "code", "path": "src/billing/webhooks.py", "start_line": 10, "end_line": 20, "sha256": H64,
      "anchor_hash": "0123456789abcdef", "observed_at": T, "run_id": "20261012T222035Z"}
EVENT = {"at": T, "status": "PROPOSED", "source": "flight-check", "note": "suggested from finding FC-0003"}
LINKS = {"findings": ["FC-0003"], "decisions": [], "requirements": [], "risks": [], "legal": [], "profile": ["PF-0001"]}


def project():
    return {
        "schema_version": "2.0",
        "revision": 3,
        "identity": {"project_id": "0123456789abcdef0123456789abcdef", "created_at": T, "name": "shop",
                     "root": ".", "vcs": "git", "repository": "github.com/example/shop"},
        "profile": {"next_id": 2, "facts": [{
            "id": "PF-0001", "aspect": "payments.provider", "value": "hosted card checkout", "origin": "observed",
            "source": "flight-check", "confidence": "CONFIRMED", "status": "current", "evidence": [EV],
            "first_observed": T, "last_observed": T, "run_id": "20261012T222035Z",
            "corrections": [{"at": T, "by": "owner", "source": "user", "assertion": "scope",
                             "value": "production only", "reason": "test mode is never deployed", "active": True}],
            "contradiction": False, "history": [dict(EVENT, status="current")]}]},
        "applicability": [
            {"domain": "payments", "classification": "applicable", "confidence": "LIKELY", "reasoning": "checkout code",
             "evidence": [EV], "components": ["src/billing"], "evaluated_at": T, "run_id": "20261012T222035Z",
             "override": None, "contradiction": False},
            {"domain": "ai-llm", "classification": "uncertain", "reasoning": "no model client found", "evidence": [],
             "evaluated_at": T,
             "override": {"classification": "not_applicable", "source": "user", "by": "owner", "at": T,
                          "reason": "the product has no AI features"}},
        ],
        "readiness": [{"decision": "NOT READY — REMEDIATION REQUIRED", "at": T, "run_id": "20261012T222035Z",
                       "revision": 3, "commit": "6a73fe9"}],
    }


def registers():
    common = {"origin": "declared", "source": "user", "created_at": T, "updated_at": T, "links": LINKS,
              "evidence": [], "history": [dict(EVENT, source="user", status="ACCEPTED")]}
    return {
        "schema_version": "2.0",
        "next_ids": {"REQ": 2, "DEC": 2, "RISK": 2, "LR": 2},
        "requirements": [dict(common, id="REQ-0001", title="Delete account data", status="ACCEPTED",
                              description="Account deletion removes personal data within 30 days.",
                              category="data-lifecycle", criticality="release-critical",
                              components=["src/account"], verification={"state": "unverified"})],
        "decisions": [dict(common, id="DEC-0001", title="Hosted checkout", status="ACCEPTED",
                           context="Card data must not reach our servers.", decision="Use the provider's hosted page.",
                           rationale="Keeps card numbers out of scope.", owner="owner", date="2026-10-12",
                           review_triggers=["src/billing/"], assertions=["No card number field is rendered by the app."])],
        "risks": [dict(common, id="RISK-0001", title="Webhook replay", status="ACCEPTED", origin="observed",
                       source="flight-check", description="Replayed events could issue duplicate tickets.",
                       domain="payments", severity="MEDIUM", confidence="LIKELY",
                       acceptance={"reason": "provider deduplicates", "owner": "owner", "date": "2026-10-12",
                                   "scope": "until the next release", "compensating_controls": "daily reconciliation",
                                   "review_trigger": "any change to src/billing/"})],
        "legal": [dict(common, id="LR-0001", title="Refund terms", status="AWAITING_COUNSEL", origin="observed",
                       source="flight-check", category="refunds", description="Terms promise automatic refunds.",
                       trigger="FC-0003", classification="POLICY/IMPLEMENTATION CONTRADICTION",
                       jurisdiction_unknown=True, questions=["Do the refund terms match how refunds are issued?"])],
    }


def log_event(seq=1, prev=""):
    return {"seq": seq, "at": T, "actor": "user", "by": "owner", "command": "decision accept", "record": "DEC-0001",
            "change": {"status": ["PROPOSED", "ACCEPTED"]}, "reason": "agreed in review", "run_id": None,
            "revision": seq, "prev": prev, "hash": H64}


class StateSchemaTests(unittest.TestCase):
    def check(self, doc, definition):
        return minischema.validate_def(json.loads(json.dumps(doc)), catalog.schema(), definition)

    def test_valid_examples(self):
        self.assertEqual(self.check(project(), "projectState"), [])
        self.assertEqual(self.check(registers(), "registers"), [])
        self.assertEqual(self.check(log_event(), "logEvent"), [])
        self.assertEqual(self.check(log_event(2, H64), "logEvent"), [])

    def test_invalid_examples_are_rejected(self):
        for doc, definition, why in self.invalid_cases():
            self.assertTrue(self.check(doc, definition), why)

    def invalid_cases(self):
        cases = []

        def mutate(fn, base, definition, why):
            doc = copy.deepcopy(base)
            fn(doc)
            cases.append((doc, definition, why))

        p, r = project(), registers()
        mutate(lambda d: d["profile"]["facts"][0]["evidence"][0].update(path="/etc/passwd"), p, "projectState", "absolute path")
        mutate(lambda d: d["profile"]["facts"][0]["evidence"][0].update(path="src/../../x"), p, "projectState", "parent traversal")
        mutate(lambda d: d["profile"]["facts"][0]["evidence"][0].update(quote="secret code"), p, "projectState", "quotes are not stored")
        mutate(lambda d: d["profile"]["facts"][0].update(origin="assumed"), p, "projectState", "unknown origin")
        mutate(lambda d: d["profile"]["facts"][0]["corrections"][0].update(source="flight-check"), p, "projectState",
               "a correction can only come from the user")
        mutate(lambda d: d["applicability"][1]["override"].update(source="flight-check"), p, "projectState",
               "an override can only come from the user")
        mutate(lambda d: d["identity"].update(project_id="shop"), p, "projectState", "project id is random hex")
        mutate(lambda d: d.update(schema_version="1.0"), p, "projectState", "state files are schema 2.0")
        mutate(lambda d: d["requirements"][0].update(status="DONE"), r, "registers", "unknown requirement status")
        mutate(lambda d: d["requirements"][0].update(status="VERIFIED", verification={"state": "fresh"}), r, "registers",
               "unknown verification state")
        mutate(lambda d: d["decisions"][0].update(superseded_by="DEC-1"), r, "registers", "bad decision id")
        mutate(lambda d: d["risks"][0]["acceptance"].pop("owner"), r, "registers", "acceptance needs an owner")
        mutate(lambda d: d["legal"][0].update(status="LEGALLY_APPROVED"), r, "registers", "no such legal status")
        mutate(lambda d: d["legal"][0]["links"].update(findings=["FT-0001"]), r, "registers", "old finding prefix")
        mutate(lambda d: d.update(change={"status": ["A", "B", "C"]}), log_event(), "logEvent", "change is [old, new]")
        mutate(lambda d: d.update(record="../ledger.json"), log_event(), "logEvent", "record must be an id")
        mutate(lambda d: d.update(hash="xyz"), log_event(), "logEvent", "hash is sha256 hex")
        return cases

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_builtin_validator_agrees_with_reference(self):
        schema = catalog.schema()
        docs = [(project(), "projectState"), (registers(), "registers"), (log_event(), "logEvent")]
        docs += [(doc, definition) for doc, definition, _ in self.invalid_cases()]
        for doc, definition in docs:
            wrapper = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/%s" % definition}
            ref_invalid = bool(list(jsonschema.Draft202012Validator(wrapper).iter_errors(json.loads(json.dumps(doc)))))
            self.assertEqual(bool(self.check(doc, definition)), ref_invalid, (definition, doc))


if __name__ == "__main__":
    unittest.main()
