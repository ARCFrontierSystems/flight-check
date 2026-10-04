"""Shared helpers for Seaworthy unit tests: build realistic run directories programmatically."""

import copy
import json
import os
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS = os.path.join(REPO, "plugins", "seaworthy", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from seaworthy_lib import catalog  # noqa: E402

FIXTURE_ROOT = os.path.join(REPO, "tests", "fixtures", "mini-target")
NOW = "2026-10-04T12:00:00Z"


def sql_finding(**over):
    f = {
        "local_id": "F1",
        "rule": "application-security.sql-injection",
        "domain": "application-security",
        "title": "Search query concatenates user input into SQL",
        "severity": "HIGH",
        "severity_rationale": "Any caller-controlled term reaches a raw SQL string, allowing data extraction.",
        "confidence": "CONFIRMED",
        "evidence": [{"kind": "code", "path": "src/store.py", "start_line": 11, "end_line": 12,
                      "quote": "query = \"SELECT body FROM notes WHERE body LIKE '%\" + term + \"%'\"\nreturn conn.execute(query).fetchall()"}],
        "counterevidence": [{"searched": "Grep for escaping or parameter binding around search_notes and its callers",
                             "result": "No escaping or binding found; the parameterized pattern is used only in find_note."}],
        "affected_components": ["src/store.py: search_notes"],
        "explanation": "search_notes builds a SQL statement by string concatenation with the term argument and executes it directly.",
        "impact": "An attacker controlling the search term can read or modify data in the notes database.",
        "remediation": "Use a bound parameter for the LIKE pattern, for example passing '%' + term + '%' as a parameter.",
        "release_blocking": True,
        "human_review": {"required": False},
        "standards": [{"id": "CWE-89"}],
        "control_ids": ["APPSEC-INJECTION"],
    }
    f.update(over)
    return f


def deletion_finding(**over):
    f = {
        "local_id": "F2",
        "rule": "privacy.deletion-contradicts-policy",
        "domain": "privacy",
        "title": "Account deletion only flags users while the policy promises permanent deletion",
        "severity": "MEDIUM",
        "severity_rationale": "Personal data is retained indefinitely contrary to a published commitment.",
        "confidence": "CONFIRMED",
        "evidence": [
            {"kind": "code", "path": "src/store.py", "start_line": 15, "end_line": 16,
             "quote": "def delete_account(conn, user_id):\n    conn.execute(\"UPDATE users SET deleted = 1 WHERE id = ?\", (user_id,))"},
            {"kind": "documentation", "path": "docs/PRIVACY.md", "start_line": 3, "end_line": 3,
             "quote": "We permanently delete all of your data within 30 days after you delete your account."},
        ],
        "counterevidence": [{"searched": "Grep for purge jobs, hard deletes, or retention cleanup referencing users or deleted",
                             "result": "No purge or hard-delete job found in the repository."}],
        "affected_components": ["src/store.py: delete_account", "docs/PRIVACY.md"],
        "explanation": "delete_account sets a deleted flag but never removes the row or related notes, while the privacy policy promises permanent deletion within 30 days.",
        "impact": "Users' personal data remains stored after deletion, contradicting the published policy.",
        "remediation": "Implement hard deletion or anonymization with a scheduled purge, or align the policy with actual retention after review.",
        "release_blocking": False,
        "human_review": {"required": True, "types": ["legal", "privacy"], "reason": "Policy and implementation disagree."},
        "legal": {
            "classification": "POLICY/IMPLEMENTATION CONTRADICTION",
            "why_review": "The published privacy commitment and the implemented deletion behavior differ.",
            "questions": [
                "Which deletion and response obligations apply to the categories of personal data this service stores, given the stated 30-day commitment?",
                "What retention exceptions, if any, should apply to deleted user data and backups?",
            ],
            "decisions_needed": ["Decide whether to implement hard deletion within 30 days or revise the stated commitment."],
            "policy_refs": ["docs/PRIVACY.md"],
        },
        "control_ids": ["PRIV-DELETION"],
    }
    f.update(over)
    return f


def base_part(findings=None):
    findings = [sql_finding(), deletion_finding()] if findings is None else findings
    return {
        "agent": "appsec",
        "coverage": [
            {"domain": "application-security", "status": "ASSESSED", "rationale": "Reviewed all source files for injection and secrets.",
             "searches": ["Glob **/*.py", "Grep execute\\(", "Grep (password|secret|token)"]},
            {"domain": "privacy", "status": "ASSESSED", "rationale": "Compared deletion code with the privacy policy.",
             "searches": ["Read docs/PRIVACY.md", "Grep delete"]},
        ],
        "controls": [
            {"id": "APPSEC-INJECTION", "state": "NOT_MET", "related_findings": ["F1"]},
            {"id": "PRIV-DELETION", "state": "NOT_MET", "related_findings": ["F2"]},
            {"id": "APPSEC-SECRETS", "state": "VERIFIED", "rationale": "No credentials in tracked files.",
             "evidence": [{"kind": "code", "path": "src/store.py", "start_line": 1, "end_line": 2,
                           "quote": "\"\"\"Tiny fixture module used by Seaworthy's unit tests. Not a real application.\"\"\"\nimport sqlite3"}]},
        ],
        "findings": findings,
        "positive_controls": [
            {"local_id": "P1", "domain": "application-security", "title": "find_note uses a parameterized query",
             "evidence": [{"kind": "code", "path": "src/store.py", "start_line": 7, "end_line": 7,
                           "quote": "return conn.execute(\"SELECT body FROM notes WHERE id = ?\", (note_id,)).fetchone()"}]},
        ],
        "unverified_areas": [],
    }


def base_inventory():
    items = [{"aspect": "Language", "value": "Python", "state": "IDENTIFIED",
              "evidence": [{"kind": "code", "path": "src/store.py", "start_line": 2, "end_line": 2, "quote": "import sqlite3"}]}]
    applicability = []
    for d in catalog.domain_ids():
        applicable = "yes" if d in ("application-security", "privacy") else "no"
        applicability.append({"domain": d, "applicable": applicable,
                              "rationale": "Fixture: only application security and privacy are in scope." if applicable == "no" else "In scope.",
                              "evidence": [{"kind": "absence", "searched": "Glob ** across the fixture"}] if applicable == "no" else []})
    return {"items": items, "applicability": applicability}


def base_run(run_id="20261004T120000Z", mode="audit"):
    return {"schema_version": "1.0", "run_id": run_id, "started_at": NOW, "mode": mode, "trust_tier": "own",
            "target": {"name": "mini-target", "scope": []}, "context": {"provided": False}}


def write_run(parts=None, inventory=None, run=None, verification=None, base=None):
    base = base or tempfile.mkdtemp(prefix="seaworthy-test-")
    run = run or base_run()
    run_dir = os.path.join(base, run["run_id"])
    os.makedirs(run_dir, exist_ok=True)

    def dump(name, data):
        with open(os.path.join(run_dir, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    dump("run.json", run)
    dump("inventory.json", inventory or base_inventory())
    for i, part in enumerate(parts if parts is not None else [base_part()]):
        dump("part-%s.json" % (part.get("agent") or i), part)
    if verification is not None:
        dump("verification.json", verification)
    return run_dir


def all_controls_resolved(part):
    """Return a copy of part where every catalog control is reported (NOT_APPLICABLE outside the part's domains)."""
    part = copy.deepcopy(part)
    reported = {c["id"] for c in part["controls"]}
    for c in catalog.controls():
        if c["id"] in reported:
            continue
        if c["domain"] in ("application-security", "privacy"):
            part["controls"].append({"id": c["id"], "state": "NOT_APPLICABLE", "rationale": "Fixture has no such feature.",
                                     "evidence": [{"kind": "absence", "searched": "Glob ** across the fixture"}]})
    return part
