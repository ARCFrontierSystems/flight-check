# Methodology

## Principles

1. **Evidence over assumption.** Documentation, comments, names, and intent are not proof that a control works. Fairtide verifies implementation where it can, and marks everything else UNVERIFIED.
2. **No security theater.** Findings need evidence. Secure implementations are reported as positive controls. Padding is treated as a defect.
3. **Fail safe.** A control that cannot be established is never marked safe.
4. **Evidence-first findings.** Every finding has an ID, domain, severity with rationale, confidence, evidence (file, lines, verbatim quote, or a described search showing an absence), counter-evidence searched, affected components, explanation, impact, remediation direction, human or legal review flags, and a release-blocking decision.
5. **No legal overclaims.** Fairtide identifies potential legal and business risk and prepares questions for qualified counsel. It never states legal conclusions, compliance, or certification. A validator rejects such statements in findings, summaries, and packets.

## Pipeline

1. **Inventory.** The read-only inventory agent records the platform, frameworks, data stores, authentication, payments, third parties, AI services, deployment, CI/CD, testing, logging, monitoring, documentation, policies, data and user lifecycles, and administrative surfaces. It decides which of the 19 domains apply. It says "not applicable" only with evidence of the searches it ran, and "unknown" otherwise.
2. **Domain audits.** Nine read-only agents run in parallel. Their tools are limited to Read, Grep, and Glob, enforced by Claude Code, and they do not load the project's `CLAUDE.md`. Each traces entry points through guards to sinks, applies the false-positive defense, reports required controls, positive controls, and unverified areas, and returns JSON.
3. **Validation.** `fairtide.py validate` checks the output against the schema and Fairtide's rules. These include located evidence with exact quotes, at least one counter-evidence search per finding, specific questions for counsel when legal review is indicated, state-specific rules for controls, searches listed for every assessed domain, and the rejection of vague findings and legal or compliance conclusions. Invalid output is returned to the agent with the errors, at most twice. If it is still invalid, the agent's domains are recorded as not assessed.
4. **Verification.** An independent verifier agent re-reads every cited line, searches for mitigations the original agent may have missed, and issues a verdict: CONFIRMED, DOWNGRADED (lower severity or confidence), REJECTED (unsupported or mitigated), DUPLICATE (merged into another finding), or NEEDS_HUMAN.
5. **Finalize.** `fairtide.py finalize`:
   - Checks every quote mechanically against the cited lines, resolving paths safely inside the audit root.
   - Downgrades positive claims whose evidence does not match.
   - Merges duplicates, assigns stable IDs, and updates the ledger.
   - Computes the ship gate.
6. **Report.** A 33-section report, plus `audit.final.json`.

## Required controls

`plugins/fairtide/references/required-controls.json` lists 46 controls across the 19 domains. Every audit must report each one as VERIFIED, NOT_MET, UNVERIFIED, or NOT_APPLICABLE:
- **VERIFIED** needs positive evidence.
- **NOT_MET** must reference the findings that show the gap.
- **UNVERIFIED** must say what evidence is missing.
- **NOT_APPLICABLE** needs evidence of the searches that support it.
- **Missing** controls are treated as UNVERIFIED.

23 controls are **release-critical**. If one of them is UNVERIFIED, the ship gate cannot be better than BLOCKED — INSUFFICIENT EVIDENCE, unless a user explicitly accepts that risk.

## Severity and confidence

- **Severity:** CRITICAL, HIGH, MEDIUM, LOW, INFORMATIONAL. Each finding explains its severity. Severity is never inflated to be conservative.
- **Confidence:** CONFIRMED (traced end to end, no mitigation), LIKELY (one link unconfirmed), POTENTIAL (depends on unseen factors), UNVERIFIED (could not be decided).
- **Effective confidence:** a finding whose quotes cannot be found in the cited files is treated as UNVERIFIED, whatever confidence the agent reported.

## Ship gate

Computed in `fairtide_lib/gate.py`. The first matching rule wins; every applicable condition is still reported.

1. **BLOCKED — CRITICAL RISK:** an open CRITICAL finding with CONFIRMED or LIKELY effective confidence.
2. **BLOCKED — INSUFFICIENT EVIDENCE:** any of the following:
   - an unaccepted release-critical control is UNVERIFIED;
   - a domain was not assessed;
   - an open CRITICAL finding is only POTENTIAL or UNVERIFIED;
   - an open HIGH finding is UNVERIFIED;
   - a re-audit contradicted a recorded fix.
3. **NOT READY — REMEDIATION REQUIRED:** an open release-blocking finding. This covers every CRITICAL finding, HIGH findings with CONFIRMED or LIKELY confidence, and other findings the agent marked with a stated rationale.
4. **READY WITH ACCEPTED RISKS:** valid, unexpired, user-recorded acceptances are in effect.
5. **READY FOR RELEASE:** none of the above.

Two rules limit what an acceptance can do:
- **An accepted CRITICAL finding still blocks.** It is treated exactly like an open CRITICAL finding; the acceptance is recorded and shown, but never lifts the block.
- **Acceptances count only from the next run.** An acceptance recorded after a run started is not counted for that run. The run's start is the earlier of its script-generated run ID and the `started_at` in `run.json`. Nothing recorded while an audit is in progress, by anyone, can change that audit's decision.

Commands that record decisions (`ledger accept`, `revoke`, `legal`, `close`) are never pre-approved by a skill, so each one raises a permission prompt that the user must approve. The repository lint enforces this.

Every decision carries a scope statement: it reflects only the evidence examined.

## Ledger and regressions

`.fairtide/ledger.json` keeps one entry per finding, keyed by a fingerprint built from the rule, the file path, and the first meaningful quoted line. The fingerprint survives line shifts; a secondary match on rule plus path survives small edits.

Lifecycle transitions:
- **REGRESSION:** a finding that was VERIFIED or CLOSED is observed again.
- **NOT_REPRODUCED:** a finding is no longer observed in an assessed domain. It needs your confirmation before it can be closed; it is never silently dropped.
- **REMEDIATED:** a fix was recorded. It becomes **VERIFIED** only when a re-audit's verifier confirms the fix.
- **ACCEPTED_RISK:** requires risk, reason, owner, date, scope, and compensating controls, all from the user. It lapses after its review date.

## Legal Review Assistant

Findings that raise legal or business questions are classified as one of:
- LEGAL REVIEW REQUIRED
- BUSINESS DECISION REQUIRED
- POLICY GAP
- POLICY/IMPLEMENTATION CONTRADICTION
- POTENTIAL LEGAL RISK
- INFORMATIONAL

Findings that send something to counsel must include specific, finding-related questions that end in a question mark; generic questions are rejected. The Attorney Review Packet copies evidence verbatim from the finalized audit, so it cannot drift from what was actually found. It states that jurisdiction-specific research is UNVERIFIED, and never cites legal authority.

Legal review status follows: OPEN → LEGAL REVIEW REQUESTED → COUNSEL REVIEWED → DECISION RECEIVED → IMPLEMENTATION REQUIRED → IMPLEMENTED → VERIFIED → CLOSED. Counsel's review and decision can only be recorded from what you report.

## Standards

Fairtide refers to standards by identifier only, for example CWE IDs, WCAG 2.2 success criteria, and the OWASP Top 10 for LLM Applications 2026 categories. It does not reproduce their text. Standards evolve; identifiers are reviewed with each release.
