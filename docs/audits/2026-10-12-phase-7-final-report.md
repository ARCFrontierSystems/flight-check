# Flight Check 0.1.0: final report (Phase 7), 2026-10-12

This report closes the development plan. Every conclusion is limited to the evidence examined: a finding Flight Check did not report is not evidence that the problem is absent. Nothing here is legal advice, a certification, or a claim of compliance.

## Executive summary

Flight Check is a Claude Code plugin for evidence-based ship-readiness audits: security, privacy, legal and business, accessibility, reliability, and operations.
- **What it produces:** findings backed by quoted evidence that a script checks mechanically, a ship decision computed deterministically, a ledger that tracks findings across audits, and an Attorney Review Packet that organizes questions for counsel.
- **How well it found planted issues** (release build, generated test applications):
  - On the development fixture, it found 71% of seeded issues by strict matching and 87% of must-find issues.
  - On the held-out fixture, which was never used during development, it found 60% and 81%.
  - Some finding's evidence pointed at every must-find issue in every run on both fixtures.
- **What it got wrong:** it flagged no decoy (safe code that looks wrong) on the development fixture, and about 8% of decoys on the held-out one.
- **How reliable it is:** results vary from run to run, so one audit is not a complete picture. One run in seven ended without a ship decision because of a tool limitation, and it said so rather than guessing.
- **Its own ship decision:** Flight Check's last audit of its own repository returned **BLOCKED — INSUFFICIENT EVIDENCE**. That decision is reported as is, below.

## Architecture

- **Inventory agent:** maps the project, decides which of 19 domains apply (only with evidence), and lists every entry-point file.
- **Domain agents:** nine read-only agents (Read, Grep, and Glob only) audit their domains in parallel against required controls, with false-positive defenses. When a project has more than 12 entry-point files, up to eight extra handler reviewers each read up to 10 of them end to end.
- **Validation:** a standard-library Python script (`flight_check.py`) checks every agent output against a schema and against rules such as exact quotes, required counter-evidence searches, specific counsel questions, and no legal or compliance conclusions.
- **Verifier agent:** re-reads the evidence for every finding, searches for mitigations the original agent missed, and confirms, downgrades, rejects, or merges duplicates. It also checks recorded fixes.
- **Finalize:** checks quotes mechanically, masks likely secrets, assigns stable finding IDs through the ledger (including regression detection), and computes the ship gate deterministically.
- **Outputs:** a 33-section report, `audit.final.json`, the committed ledger, and on request an Attorney Review Packet (PDF and Markdown).
- **Safety design:**
  - Skills pre-approve only the script subcommands they need.
  - Decisions (accepting a risk, recording counsel's decisions, closing findings) always require the user's approval.
  - Audited content is treated as untrusted data.
  - The hardened mode audits a disposable copy.

## Implemented capabilities

| Command | What it does |
|---|---|
| `/flight-check:audit` | Full or domain-limited audit with a ship decision. Imported evidence (for example, vulnerability scans) can resolve unverified controls. |
| `/flight-check:legal-packet` | Attorney Review Packet for findings that need legal or business review: evidence copied verbatim, specific questions for counsel, and space for counsel's notes. |
| `/flight-check:track` | Accepted risks (with owner, scope, compensating controls, and a review date), the legal-review lifecycle, counsel decisions as reported by the user, and closures. |
| `/flight-check:remediate` | Authorized fixes on the user's own projects, with re-audit verification. |

The ship decisions are READY FOR RELEASE, READY WITH ACCEPTED RISKS, NOT READY — REMEDIATION REQUIRED, BLOCKED — INSUFFICIENT EVIDENCE, and BLOCKED — CRITICAL RISK. An accepted CRITICAL finding still blocks release.

## Tests performed

- **Unit tests:** 91 tests on Python 3.9, 3.11, and 3.12 (CI), with 86% line coverage of the shipped scripts.
- **Repository checks:** the lint (standard-library only, no network or subprocess calls, pre-approval limits, invisible characters, credential-like strings), the leak gate (private denylist and fixture canaries across every committable file and the full history), and strict plugin validation.
- **Self-audits** of this repository: Phase 3, two in Phase 4, Phase 6, and Phase 7.
- **Blind tests** (protocol in [blind-test-protocol.md](../blind-test-protocol.md)):
  - a development fixture: 3 runs in Phase 5, and 3 on the release build, 2 of which completed;
  - a held-out fixture: 3 runs in Phase 5, scored only now, and 3 on the release build;
  - an OWASP Benchmark sample in three configurations, 3 runs each.
- **Other tests:** a regression test (fix, verify, reintroduce, flag REGRESSION), a headless Attorney Review Packet test, an adversarial multi-agent review of the Phase 5 changes, and the Phase 6 release checklist.

## Test results

Release build, generated test applications. Strict matching means the same location and the same domain. Evidence coverage counts an issue as covered when any finding's evidence points at it.

| Metric | Development fixture, Phase 5 build (3 runs) | Development fixture, release build (2 runs) | Held-out fixture, release build (3 runs) |
|---|---|---|---|
| Seeded issues (must-find) | 24 (15) | 24 (15) | 31 (16) |
| Strict recall | 0.64 | 0.71 | 0.60 (0.48 to 0.68) |
| Must-find recall, strict | 0.76 | 0.87 | 0.81 (0.69 to 0.94) |
| Must-find recall, any domain | 0.89 | 0.93 | 0.98 |
| Evidence coverage, must-find | not measured | 1.0 | 1.0 |
| Decoy false-positive rate | 0 | 0 | 0.08 |
| Severity within the answer key's tolerance | 1.0 | 1.0 | 0.95 |
| Domains that do not apply marked correctly | yes | yes | yes |
| Ship decision (expected) | INSUFFICIENT EVIDENCE every run (NOT READY) | INSUFFICIENT EVIDENCE (NOT READY) | INSUFFICIENT EVIDENCE every run (CRITICAL RISK) |
| Usage per audit | about $6 | about $4.60 to $5.10 | about $3.50 to $4.90 |

- **Improvements aimed at Phase 5's weak spots took effect:**
  - Outbound calls without timeouts were found under reliability in both release runs; in Phase 5 they never counted as a strict match.
  - Two issue classes were found in one of two runs each; in Phase 5, neither counted as a strict match in any run:
    - conflicts between two legal documents;
    - README promises checked against the code.
- **Overfitting check.** The held-out fixture scored below the development fixture on strict recall (0.60 against 0.71), close on must-find recall (0.81 against 0.87), and higher on must-find recall in any domain (0.98 against 0.93). This gap is small and partly explained by the held-out fixture's larger share of hard stretch issues. It does not suggest the improvements were tuned to the development fixture.
- **Never found on the held-out fixture, even in another domain:** 5 stretch issues. They are a missing waiver for in-person activities, billing-date drift, tests that run against a fake database, terms that change by posting, and bookings that survive a membership ending. Product-logic gaps of this kind are the main remaining blind spot.
- **Severity.** The held-out fixture's one CRITICAL issue was found in every run but rated HIGH (within tolerance). The ship decision therefore blocked for missing evidence rather than critical risk; both decisions stop a release.
- **Security sample** (OWASP Benchmark subset, de-identified, with handler review): strict recall 0.71, must-find 0.83, evidence coverage 0.94, decoy false-positive rate 0.11. Treat these as optimistic; see the [Phase 5 record](2026-10-06-phase-5-blind-test.md).
- **Other results:**
  - **Regression behavior:** passed.
  - **Attorney Review Packet:** passed, with question repetition later fixed by letting the request choose questions.
  - **Evidence integrity:** every evidence failure was caught by the mechanical check, and no fabricated quote reached a confirmed finding.

## Known limitations

1. **Run-to-run variation.**
   - Two audits of the same code can differ in which issues they report and which controls they verify. For example, the last two self-audits reported 7 findings and then none.
   - The ledger marks findings that are not reported again as NOT_REPRODUCED, never as fixed, and closing a finding needs the user's confirmation.
   - For release decisions, run two or three audits.
2. **Large agent replies** (fixed after this report). One release-build run of seven stopped before its ship decision: a corrected agent reply was too large for the coordinating session to save. The audit said so and produced no decision. The skills now save such replies with `flight_check.py save-reply`; replaying that run's stored reply passes validation.
3. **Double-escaped quotes.**
   - Agent replies sometimes arrive HTML-escaped twice (for example `&amp;gt;`). The evidence check decodes them only once, so in one run 7 correct findings had their confidence lowered to UNVERIFIED.
   - This fails safe, but it understates real findings.
   - Fixed after this report: the check now undoes up to three layers of escaping. Re-checking that run lowered its failed findings from 7 to 1, a genuine mismatch.
4. **Product-logic gaps** that no single line shows, and some legal-document gaps, are still often missed.
5. **Severity of business-logic failures** that repeat money movement (for example, double charging) can be rated one level too low.
6. **Static analysis only.** Flight Check never runs the project. Infrastructure outside the repository, organizational controls, and deployment settings remain UNVERIFIED unless the user imports evidence.
7. **Attorney Review Packet PDFs are untagged.** This is accepted as FC-0001; the Markdown copy is the accessible version.
8. **Prompt injection** cannot be fully prevented. Agents are read-only, treat project content as data, and the hardened mode isolates untrusted code.
9. **Model dependence and cost.** Results depend on the model and can change between model versions. A full audit used about $3.50 to $5 on the test applications, and more on projects with many request handlers.

## Remaining risks

- A user may treat a clean-looking report as proof of safety. The report and README state the opposite, but over-reliance remains the main risk of a tool like this.
- Missed issues: roughly one in five must-find planted issues was missed in a typical run, by strict matching.
- Limitations 2 and 3 above can hide or weaken real findings until they are fixed.
- The product name overlaps existing marks (FC-0013), and counsel review is still pending.

## Legal and business review status (this project)

| Item | Status |
|---|---|
| FC-0001: untagged PDF | ACCEPTED_RISK by the owner on 2026-10-12, review 2027-04-12 |
| FC-0013: name overlaps existing marks | Open: counsel review before public launch. An Attorney Review Packet was prepared on 2026-10-12 |
| FC-0014: terms of use, governing law, contribution terms | Owner decided on 2026-10-12: no separate terms of use (license plus disclaimers), no governing-law clause while there are no separate terms, and contributions under Apache-2.0 with a Developer Certificate of Origin sign-off. README and CONTRIBUTING.md updated |
| FC-0011: private vulnerability reporting | CLOSED: the owner enabled it on 2026-10-12 |

FC-0013 shows as NOT_REPRODUCED in the ledger because the last self-audit did not report it again. It is still open; close it only once counsel has reviewed the name.

## Attorney Review Packet status

- The packet was tested end to end in Phase 5: 13 findings, 39 pages, the required disclaimer, verbatim evidence, and specific counsel questions with no legal conclusions.
- Since then, a packet request can leave out paraphrased repeat questions (never add or reword them), and business-decision findings explain why they have no counsel questions.
- No packet has been prepared for this project's own open legal items. `/flight-check:legal-packet` can produce one for FC-0013 and FC-0014 when the owner wants counsel's view.

## Public-release readiness

- **Done:**
  - merged into `main`, so the documented marketplace install works;
  - the Phase 6 checklist passed: no secrets or private information in the history, packaging, license, documentation, disclaimers, tests, and local install;
  - Apache-2.0 license.
- **Before a public launch:**
  - get counsel's review of the name (FC-0013); the Attorney Review Packet is ready.
- **Done after this report:** terms and contribution terms decided (FC-0014), and CI tool installs locked with hashes (FC-0015).
- **Recommended next fixes** (owner approval needed):
  1. Done after this report: decode quotes escaped more than once, and save oversized agent replies with `save-reply`.
  2. Rate repeatable money-movement failures as CRITICAL when they affect every customer.
  3. Run the held-out fixture again after these fixes.

## Final ship gate

**Flight Check's own release audit (run `20261012T194029Z`, release build): BLOCKED — INSUFFICIENT EVIDENCE.**

- **Why it blocked:** four release-critical controls were UNVERIFIED in this run. They cover secrets handling, output encoding of untrusted quotes, the protection of local `.flight-check/` files, and whether the plugin needs its own privacy disclosure. The previous self-audit had blocked only on FC-0001, which the owner has now accepted. That run-to-run variation is limitation 1 above.
- **Recorded fixes:** the three fixes recorded in Phase 6 were verified (FC-0009, FC-0010, FC-0012).
- **No open findings:** there are no open CRITICAL or HIGH findings in the ledger. The one accepted risk is LOW.

A blocked decision means the evidence in that run was not enough to support a release decision; it does not mean a known critical defect exists. Releasing Flight Check 0.1.0 as a pre-release is the owner's decision, with the limitations and open items above stated publicly, as the README already does.

This record reflects only the evidence examined. It is not a certification or legal advice.
