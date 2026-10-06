# Flight Check Phase 5: blind test, 2026-10-05 and 2026-10-06

This page records how Flight Check performed on test applications whose answers it never saw. It follows the [blind-test protocol](../blind-test-protocol.md). It reports aggregate numbers only: no fixture file names, routes, wording, or answer-key entries, so nothing here can teach Flight Check a specific test application.

**The main caution.** Every number below comes from one development fixture, one held-out fixture, and a small security sample, with three runs each. That is enough to see patterns and clear weaknesses, not to state reliable rates. A run that did not find an issue is not evidence that the issue is absent. In a typical run, about a third of the development fixture's seeded issues were missed under strict matching, and about a sixth even when findings filed under another domain are counted.

## What was tested

| Fixture | Source | Answer key | Runs |
|---|---|---|---|
| App A (development) | Generated test application in the private testbed repository. 24 seeded issues (15 must-find, 9 stretch), 8 decoys (code that looks wrong but is safe), 2 domains that do not apply | Used for scoring and for improving Flight Check | 3 |
| App B (held out) | Second generated test application in the same repository | Held by the owner; not opened. Scored only at release time | 3 |
| Security sample | 44 OWASP Benchmark (Java) test cases, 2 real and 2 false-positive cases for each of 11 vulnerability categories, built with `tools/blindtest/owasp_subset.py` outside this repository | Generated from the benchmark's published expected results | 3 |

The generated applications cover documentation that contradicts the code, legal and business gaps, privacy commitments, accessibility, reliability, and operations. Their author declined to plant security flaws, so the security sample covers that area.

**How the runs were made.** Each run audited a fresh temporary copy with `tools/blindtest/run_audit.py`, headless, with the narrowed permissions from [hardened mode](../hardened-mode.md). App A and App B were audited with the build from just before the rename (then named Fairtide; the audit logic was the same). The security sample, the regression test, and the packet test used the Flight Check build.

## App A: detection (3 runs)

| Metric | Mean | Range |
|---|---|---|
| Recall, strict (same location and same domain) | 0.64 | 0.54 to 0.75 |
| Must-find recall, strict | 0.76 | 0.67 to 0.80 |
| Recall, location only (same location, Flight Check chose a different domain) | 0.83 | 0.79 to 0.88 |
| Must-find recall, location only | 0.89 | 0.87 to 0.93 |
| Decoy false-positive rate | 0 | 0 of 8 decoys, all runs |
| Precision after adjudication (see below) | 0.99 | 0.96 to 1.0 |
| Severity within the answer key's tolerance | 1.0 | 1.0 |
| Severity exact | 0.75 | 0.67 to 0.80 |
| Findings whose quoted evidence failed the mechanical check | 0 | 0 in 85 findings |
| Findings per run | 28 | 26 to 30 |

- **Run-to-run variation is large.** 11 seeded issues were found in every run, 8 in some runs, and 5 in none (strict matching). The detected sets overlapped by 0.71 on average (Jaccard). A finding list from one run is not a complete picture.
- **Domain disagreements are common.** Location-only matches add about 0.2 to recall. These are real detections filed under a neighboring domain, for example a missing timeout filed as application security instead of reliability. They are reported separately and never counted in the strict numbers.
- **Applicability was honest.** Both domains that do not apply were marked NOT_APPLICABLE in every run, with no findings in them.

**Where it was weakest** (classes of issue, not specific fixtures):

1. **Contradictions between two legal documents.** For example, terms of service and a privacy policy that state different time limits or ages. One such issue was never found at its location in any run; others were found in some runs only. Flight Check is much better at comparing a document with code than a document with another document.
2. **README and marketing promises checked against the code.** Product promises outside the formal policies were found at the right location but often filed under another domain, or bundled with a neighboring issue.
3. **Outbound calls without timeouts.** Found in two of three runs, but filed as application security both times, so it never counted as a strict match.
4. **Product-logic gaps that no single line shows,** such as a role that cannot be transferred. Never found.

**Confidence calibration.** Of the strict true positives, 42 were rated CONFIRMED, 3 LIKELY, and 1 POTENTIAL. The one false positive (below) was rated LIKELY, and its rationale said it rested on an unobserved framework default.

### Unlisted findings: preliminary adjudication

Findings that match no seeded issue and no decoy need a person to decide whether they are real. The adjudication below is **Claude's preliminary review, not the owner's.** The owner should confirm it before it is quoted.

| Run | Unlisted | Real issues the fixture did not plant | Repeats of a seeded issue (split or duplicated) | False positives | Artifact of the test setup |
|---|---|---|---|---|---|
| 1 | 7 | 7 | 0 | 0 | 0 |
| 2 | 9 | 7 | 2 | 0 | 0 |
| 3 | 9 | 5 | 2 | 1 | 1 |

- **Real issues the fixture did not plant** (each confirmed by reading the cited code): client-side sessions with no server-side revocation, a per-process login throttle and account enumeration at sign-up, a scheduled-job container that runs as root and writes all environment secrets to a file, no error tracking or alerting, no rollback path and no schema migrations, dependencies and CI actions pinned by mutable tag, a missing test for a privacy setting, and an unnamed third-party processor.
- **The false positive:** a finding said a web framework's session lifetime setting has no effect unless sessions are marked permanent, so cookies never expire on the server. The framework's source shows the lifetime is enforced on every session cookie. The no-revocation part of the finding was true, and was already reported by another run.
- **The test-setup artifact:** a finding that every entry point refuses to start without an undocumented environment variable. That is the protocol's own start guard for test applications. It is an accurate observation, so it is excluded from precision rather than counted either way.
- **Precision after adjudication** counts real unplanted issues and repeats as correct: 26 of 26, 30 of 30, and 27 of 28 (one false positive; the setup artifact excluded).
- **Repeats are noise** even when correct. Among the unlisted findings, each run repeated 0 to 2 seeded issues. Usually one agent split a seeded issue into parts, or two agents reported the same part.

The scorer now marks an unlisted finding that overlaps an already-detected issue as a *possible repeat*. This is a hint for the adjudicator, not a verdict: in these runs, location overlap also flagged several distinct real issues in the same files.

## App A: legal and business classification

- Among the legal and business issues each run detected (5, 10, and 7), the classification was in the answer key's accepted set for 17 of 22. Every miss was an adjacent category, for example BUSINESS DECISION REQUIRED where LEGAL REVIEW REQUIRED was expected. None was dismissed as informational.
- 20 of 22 carried specific counsel questions. The other two were classified BUSINESS DECISION REQUIRED and listed the decisions needed instead.

## App B: held out (aggregates only)

App B's answer key was not opened. It will be scored at release time, by the owner or with the owner's key, using a fresh set of runs on the release build (see the protocol).

| Run | Ship decision | Findings (HIGH / MEDIUM / LOW) | Evidence check failures | Release-critical controls UNVERIFIED | Denied tool calls |
|---|---|---|---|---|---|
| 1 | BLOCKED — INSUFFICIENT EVIDENCE | 28 (4 / 10 / 14) | 1 | 3 | 12 |
| 2 | BLOCKED — INSUFFICIENT EVIDENCE | 28 (3 / 15 / 10) | 0 | 2 | 1 |
| 3 | BLOCKED — INSUFFICIENT EVIDENCE | 26 (4 / 13 / 9) | 0 | 2 | 6 |

The first attempt at run 1 stalled and was repeated (see "Tool behavior"). The mechanical check caught the one evidence failure, and the finding's confidence was lowered to UNVERIFIED.

## Ship gate

Every run of both applications ended **BLOCKED — INSUFFICIENT EVIDENCE**; the answer key expected NOT READY — REMEDIATION REQUIRED. Both decisions stop a release. Flight Check blocked for missing evidence (2 to 4 release-critical controls it could not verify, such as backup restores and known-vulnerability scans) before it reached the remediation decision. That is the intended order: Flight Check does not report a project as merely needing fixes while it cannot see whether more serious controls hold.

## Security sample (OWASP Benchmark subset)

Three runs on the 44-case sample (22 real vulnerabilities, 22 labeled false positives), each about 4 to 5 minutes and $1.20 to $1.60 of usage.

| Metric | Each run |
|---|---|
| Real vulnerabilities detected | 1 of 22 (recall 0.045; 2 different cases across the three runs) |
| Labeled false positives reported | 0 of 22 |
| Ship decision | BLOCKED — INSUFFICIENT EVIDENCE (5 to 8 release-critical controls UNVERIFIED) |
| Findings | 6 to 7, none with failed evidence |

**This is not a usable detection score.** In every run, Flight Check recognized the sample as the OWASP Benchmark and called it "a deliberately vulnerable" test suite. It then read the helper classes and a single test case, reported that one case's flaw (as INFORMATIONAL in two runs and MEDIUM in one), and did not examine the other 43. Its other findings were real issues in the support code (hardcoded credentials, plaintext passwords, error details returned to clients, weak security headers, no build manifest).

What the runs do show:

- **Flight Check was honest about what it skipped.** Each run's coverage note said that the other test cases were not read; one called its findings "representative, not exhaustive". Each run left the affected controls UNVERIFIED, so the gate blocked for missing evidence rather than implying the code was checked.
- **Real weakness: sampling.** Faced with dozens of similar request handlers, the application-security agent read one and generalized. A real application with many handlers would be sampled the same way. Read together with the gate behavior, this is a coverage gap reported honestly, not a hidden miss, but it is still a gap.
- **Open design question: deliberately vulnerable code.** Reporting the intentional flaws of a known test corpus once, as context, is reasonable for a user auditing that corpus. It is wrong when vulnerable code would actually ship.
- **The sample needs to change** before it can measure detection: identifying names and headers must be removed in the local copy. The benchmark is GPL-2.0. A modified copy would stay local and never be distributed; whether to make one is the owner's decision.

## Regression behavior

The protocol's regression test ran on a separate copy of App A, auditing only the accessibility domain (about 4 minutes and $0.75 of usage per run, no denied calls):

1. **Baseline.** All 4 seeded accessibility issues were found, with no findings on the 2 accessibility decoys and no unlisted findings. The issue chosen for the test was a missing document language.
2. **Fix.** The language was added to the template, and the fix was recorded with `ledger remediate` (status REMEDIATED).
3. **Re-audit.** The verifier checked the recorded fix, read the changed line, and reported **FIXED_VERIFIED**; the ledger moved the finding to **VERIFIED**.
4. **Reintroduction.** The fix was removed again. The next audit reported the issue under the same ID, and the ledger marked it **REGRESSION**.

Each run's ship decision was BLOCKED — INSUFFICIENT EVIDENCE, as it should be. An audit limited to one domain leaves the other domains' release-critical controls unverified, and the run's summary said so.

**A defect this exposed: finding IDs were not stable.** The ledger recognizes "the same issue" by the rule name the agent chooses, the file, and the first quoted line. Agents reword rule names between runs, for example `icon-button-no-accessible-name` and then `icon-button-without-name`, for an unchanged issue on the same line. Across the three runs, the three issues that were never touched received 7 different IDs between them. Each time, the old ID was marked NOT_REPRODUCED while the same issue stayed open under a new one. The regression check worked only because the agent happened to reuse the same rule name for the reintroduced issue.

**Fixed in this phase.** Matching now has two passes:

- **Pass 1: exact fingerprints.** These are matched first, so a looser match can never take an entry that matches another finding exactly.
- **Pass 2: looser matches, used only when exactly one entry qualifies:**
  - the same rule and file;
  - or the same domain, file, and quoted line, with a similar rule name or title;
  - or the same domain and file, with a nearly identical title.

The ledger stores a hash of the quoted line, not the code itself. Replaying the same three regression runs through the new code gives 4 stable IDs, with the fix moving to VERIFIED and then to REGRESSION. Replaying the three full App A audits gives 53 ledger entries instead of 67. All 16 of the new looser matches were checked by hand and are the same issue under a reworded rule. The remaining churn is real run-to-run variation, not ID instability.

## Legal Review Assistant

`/flight-check:legal-packet all` ran headlessly against App A run 2 (copied, with its finding IDs migrated to the new `FC-` prefix), with only the `runs`, `findings`, and `packet` subcommands allowed.

- **It worked as designed.** It produced a 39-page PDF and a Markdown copy covering all 13 findings that carried a legal or business classification (3 HIGH, 5 MEDIUM, 5 LOW) in one packet. It used about $0.12 of usage, with no denied calls.
- **Packet checks passed.** The packet has the required disclaimer, every finding ID with its severity, classification, and confidence, and evidence copied verbatim with its mechanical check result. The PDF declares its language and displays its title. The executive and overall summaries stated themes and open questions without answering them.
- **Question quality** (Claude's review):
  - The questions were specific: they quoted the project's own documents and named the conflicting sections.
  - They were answerable by counsel, and none stated a legal conclusion or cited a statute.
  - **Weakness: repetition.** When several agents report the same issue, their questions are merged. Exact and near-exact repeats are removed, but paraphrases are not. One finding carried 11 questions, of which about half repeated others in different words.
  - **Weakness: a business-decision finding had no questions.** It showed the decisions needed, but the questions section said none were generated.
- **Minor:** the closing next-steps message used the skill's example finding ID instead of an ID from the packet.

## Tool behavior

- **Mechanical safeguards held.** No fabricated evidence reached the confirmed findings. The narrowed permissions allowed writes only inside each run's directory, and they contained every off-path or off-script tool call.
- **Path drift.** In early App B runs, agents sometimes rebuilt absolute paths without part of the audit root, so their writes were denied and the first attempt stalled. The agents and the audit skill now say to reuse the root exactly as given, and the runner audits copies placed directly in the temporary root. Denied calls fell from 12 in the first recorded App B run to 1 and 6 in the later App B runs, and were 1, 0, and 0 in the App A runs. All were contained.
- **Validation retries.** Deterministic validation caught every malformed agent reply. Each was fixed by a retry, and recurring causes were turned into clearer contracts or into fixes at assembly (a missing legal-review flag is now added automatically).
- **Usage limit.** Partway through, the account reached its weekly usage limit and the remaining runs stopped right after starting. The runner counted a run that had only created its directory as a success; it now succeeds only when the audit was finalized. The affected runs were repeated after the limit reset.
- **Cost.** A full audit of a generated application used about $3.50 to $6.10 of usage and took 9 to 19 minutes. A security-sample audit used about $1.20 to $1.60.

## Recommended improvements (need the owner's approval)

All are general procedures. None names or targets a specific test application.

1. **Compare legal documents with each other,** not only with the code: time limits, ages, governing law, liability, and deletion promises across terms, privacy policy, and data-processing terms.
2. **Sweep product promises** in the README, marketing pages, and help text, and check each against the code, as is already done for formal policies.
3. **Sweep outbound network calls** for timeouts and retries under reliability, so the issue is filed where readers expect it.
4. **Check framework behavior before relying on it.** When a finding depends on a library's default, read the library's code or documentation if it is available locally. Otherwise state the assumption and keep confidence below CONFIRMED. The verifier should not raise a finding's standing on an unobserved default.
5. **De-duplicate counsel questions by meaning.** Let the packet request choose and order the audit's questions (never add new ones), and say how many were left out.
6. **Give business-decision findings a question,** for example which options counsel should weigh in on, or say plainly that the item is a business decision.
7. **Use placeholders in skill examples** (`<ID>`), so example IDs are never copied into output.
8. **Check every handler, not a sample.** When a project has many similar request handlers, list every dangerous sink with a search, then read the data flow at each hit, and record in the coverage note how many were read out of how many.
9. **Decide how to treat deliberately vulnerable code** (owner decision). Recommended: report each vulnerability class once, with every affected location as evidence, at its normal severity. Lower it only when the project itself documents that the code is never deployed.
10. **Build a de-identified security sample** (owner decision; see the licensing note above) and repeat the three runs.

## Checks at the end of Phase 5

80 unit tests pass on Python 3.11, and on Python 3.9 with one test skipped. Agent sync passes, as do the repository lint (with the private denylist and the fixture canaries, across every committable file) and strict plugin validation.

Fixture material stays outside this repository: run outputs, scores, packets, and the answer keys. This page reports aggregates only.

## Owner decisions from Phase 5

1. **Confirm or correct the preliminary adjudication** of App A's unlisted findings (above).
2. **Approve or decline recommendations 1 to 8.** These are general procedure changes; none targets a test application.
3. **Deliberately vulnerable code** (recommendation 9): decide how Flight Check should report it.
4. **The security sample** (recommendation 10): decide whether to build a de-identified local copy and re-run it.
5. **App B:** keep its answer key until release; App B is scored then on fresh runs of the release build.

This record reflects only the evidence examined. It is not a certification or legal advice.
