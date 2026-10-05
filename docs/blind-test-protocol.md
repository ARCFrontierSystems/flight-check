# Blind-test protocol

Flight Check is evaluated against deliberately constructed synthetic applications whose answers are hidden from the auditor. This document is the contract between this repository and the separate **testbed** repository and Claude project that hold the test applications.

## Separation rules

1. **Separate repository and project.** Test applications and their ground truth live in a separate, private repository, authored in a separate Claude project. The author must not have Flight Check's skill or agent text in context, and must work from the issue classes below, never from Flight Check's prompts.
2. **No answers in the target.** The audited directory contains only the application. The ground-truth manifest, notes, and expected findings live outside it. `tools/blindtest/run_audit.py` copies the application to a fresh temporary directory before auditing and refuses to run if the directory looks like it contains answers.
3. **No feedback into the skill as specifics.** After scoring, Flight Check may be improved only in general terms (procedures, issue classes, search strategies). Fixture-specific identifiers, file names, routes, or wording must never enter `plugins/`. The leak gate (`tools/lint_plugin.py`) checks hashed fixture identifiers listed in `tools/leak-hashes.txt`.
4. **Held-out fixtures.** Keep at least one fixture that is never used while improving Flight Check and is scored only at release time. If a release scores clearly better on the development fixtures than on the held-out one, treat that as a sign of overfitting.
5. **Canaries.** Put a unique random string in each fixture, for example in a comment. Add its SHA-256 hash to `tools/leak-hashes.txt`, so a leak into the plugin fails CI.

## Where the fixtures come from

Two kinds of fixture are used:

- **Generated test applications** in the private testbed repository. These cover documentation that contradicts the code, legal and business gaps, privacy commitments, accessibility, reliability, and operations. Fixture authors may decline to plant security flaws, and when the first fixture author was asked to, a safety check stopped it. Do not try to work around such a refusal. Security flaws that turn up anyway are scored as unlisted findings.
- **A public security benchmark** for the security domains. `tools/blindtest/owasp_subset.py` builds a balanced, deterministic sample of the OWASP Benchmark (Java). Its cases are labeled real vulnerability or false positive, and the false positives serve as decoys. The answer key is generated from the benchmark's published expected results and kept outside the audited directory. The benchmark is GPL-2.0, so the tool builds the sample outside this repository and never vendors it here. Models may have seen the benchmark during training, so report security scores from it as optimistic.

## What a test application should contain

The goal is a realistic, unglamorous application. Avoid the well-known public vulnerable apps: models may have memorized them. Include, spread across files and layers:
- **Realistic vulnerabilities** across authentication, authorization (including object-level and tenant isolation), data access, input/output, file handling, webhooks, secrets, payments and entitlements, AI/LLM tool use, infrastructure, reliability, and dependencies.
- **Secure counterexamples (decoys):** code that looks dangerous but is correctly mitigated elsewhere. For example: a parameterized query built in an unusual way, a webhook whose verification happens in a wrapper, an ID lookup scoped by a database policy.
- **Documentation that contradicts the implementation:** privacy policy, terms, README, and marketing claims.
- **Legal and business gaps:** missing or inconsistent liability limitation, governing law, refund and cancellation terms, deletion commitments.
- **Accessibility issues, reliability issues** (missing timeouts, non-idempotent jobs, untested restores), and **infrastructure issues** (debug in production configuration, public preview environments).
- **At least one domain that does not apply** (for example no payments, or no AI), to test applicability honesty.

Rules for the fixtures themselves:
- **Fake secrets** use an obviously fake format that no real provider issues, and never real credential formats.
- **Not deployable:** the application must never be deployed, and should refuse to start without an explicit environment variable.
- **No external package names you do not control.** This prevents dependency-confusion attacks.

## Ground-truth manifest

Store it outside the application, for example `ground-truth/manifest.json` next to `app/`. Paths are relative to the application root.

```json
{
  "manifest_version": "1.0",
  "fixture": {"name": "testbed-A", "revision": "<commit>", "author": "<who>", "held_out": false},
  "expected_gate": "NOT READY — REMEDIATION REQUIRED",
  "issues": [
    {
      "id": "GT-001",
      "domains": ["authorization"],
      "severity": "HIGH",
      "severity_tolerance": 1,
      "tier": "must",
      "locations": [{"path": "src/routes/documents.ts", "start_line": 40, "end_line": 58}],
      "alternate_locations": [{"path": "src/services/documents.ts", "start_line": 12, "end_line": 20}],
      "description": "Document fetch by ID without an ownership check.",
      "required_evidence": ["the handler that loads by ID", "absence of an owner or tenant constraint"]
    },
    {
      "id": "GT-014",
      "domains": ["privacy", "documentation"],
      "severity": "MEDIUM",
      "tier": "must",
      "locations": [{"path": "docs/privacy.md", "start_line": 20, "end_line": 22}, {"path": "src/account/delete.ts", "start_line": 5, "end_line": 15}],
      "legal": {"classification": "POLICY/IMPLEMENTATION CONTRADICTION",
                "accepted_classifications": ["POLICY/IMPLEMENTATION CONTRADICTION", "LEGAL REVIEW REQUIRED"]},
      "description": "Policy promises deletion; implementation only flags accounts."
    }
  ],
  "secure_controls": [
    {"id": "SC-001", "domains": ["application-security"], "locations": [{"path": "src/db/search.ts", "start_line": 8, "end_line": 16}],
     "description": "Looks like string concatenation but values are bound parameters."}
  ],
  "expected_not_applicable_domains": ["payments"]
}
```

- **`tier`:** `must` for issues Flight Check is expected to find; `stretch` for hard ones.
- **`legal`:** set it on issues where legal or business classification is part of the expected result.

## Running a blind test

From a checkout of this repository, with the testbed checked out separately and results kept outside both repositories (they quote fixture content):

```bash
python3 tools/blindtest/run_audit.py --app ../testbed/app-a --out ../results/app-a-run1
python3 tools/blindtest/score.py --final ../results/app-a-run1/<run_id>/audit.final.json \
  --manifest ../testbed/ground-truth/app-a.json --json ../results/app-a-run1/score.json \
  --markdown ../results/app-a-run1/score.md
```

- **Each run** uses a fresh disposable copy of the application and the same narrowed permissions as `docs/hardened-mode.md`. It writes the full event log (`claude-stream.jsonl`) and a summary of denied tool calls (`claude-output.json`), so a stalled run can be diagnosed.
- **Repeat each fixture at least three times,** then combine the scores with `python3 tools/blindtest/stability.py ../results/app-a-run*/score.json`.
- **For the security benchmark:**
  1. Build the sample with `python3 tools/blindtest/owasp_subset.py --benchmark <checkout> --out ../results/owasp-subset`.
  2. Audit `../results/owasp-subset/app` the same way.
  3. Score against `../results/owasp-subset/ground-truth/manifest.json`.
- **The held-out fixture** is audited with the others but scored only at release time, by the person who holds its answer key. Nothing learned from it may change Flight Check before that release.

Then exercise the Legal Review Assistant on a development-fixture run: run `/flight-check:legal-packet` against the run directory, inspect the PDF, and score its questions (below).

## What is measured

- **Detection:** recall overall, must-tier recall, and per-domain recall (with 95% confidence intervals; per-domain numbers from one fixture are diagnostic only).
- **False positives:**
  - Decoy false-positive rate: findings on declared secure controls.
  - Unlisted findings: these are adjudicated by a person as either real unseeded issues or false positives. Precision is reported with unlisted findings counted against it (a lower bound) and excluding them.
- **Accuracy:** severity accuracy (exact, and within tolerance); confidence calibration (the share of true positives at each confidence level); evidence integrity (findings whose quotes did not match the files, which must be zero).
- **Applicability honesty:** expected not-applicable domains, and findings in domains that do not apply.
- **Legal Review Assistant:**
  - Classification accuracy and the presence of specific counsel questions.
  - Manual review of question quality: specific, answerable, not legal conclusions.
  - Packet checks: the required disclaimer, finding IDs and severities, and multiple findings in one packet.
- **Ship gate:** compared with `expected_gate`.
- **Regression behavior:** after a seeded fix and a seeded reintroduction, findings move to VERIFIED and then REGRESSION.
- **Stability:** repeat each fixture at least 3 times and report run-to-run variation. Treat single-run differences between versions as noise unless they persist across runs.
