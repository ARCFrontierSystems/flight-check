# Self-audit with two passes, 2026-10-12

One Flight Check audit of this repository after the Phase 7 fixes. It was run with `--passes 2` (run `20261012T202214Z`, about $4.61 of usage), with the earlier CI vulnerability-scan output imported as evidence. It had two purposes: to check whether the four release-critical controls left UNVERIFIED in the Phase 7 self-audit can now be verified, and to try repeat passes end to end.

## Results

- **Ship decision: BLOCKED — INSUFFICIENT EVIDENCE.** The only reason was FC-0015: the verifier found that its recorded fix covered the Python test tools but not the npm install of Claude Code in CI.
- **The four controls from Phase 7:** secrets handling, output encoding, and protection of local `.flight-check/` files were VERIFIED; the plugin's own privacy disclosure was judged NOT_APPLICABLE. In two of these, one pass verified the control with evidence and the other left it UNVERIFIED; the repeat-pass rule kept the verified result. With a single pass, the gate would again have blocked on them.
- **Findings:** 5 LOW, no CRITICAL, HIGH, or MEDIUM; 1 rejected by the verifier.

| ID | Finding | Status |
|---|---|---|
| FC-0016 | The final report listed a CI Python version that CI does not run | Fixed; REMEDIATED |
| FC-0017 | README and CHANGELOG said the held-out results were still to come | Fixed; REMEDIATED |
| FC-0018 | The README's agent count left out handler reviewers and repeat passes | Fixed; REMEDIATED |
| FC-0019 | The name overlaps a registered mark | Owner decision recorded (same subject as FC-0013) |
| FC-0020 | CI installs Claude Code with only the top-level version pinned | Fixed with an integrity-pinned lockfile; FC-0015 recorded as REMEDIATED with it |

Other controls left open: test results for the audited revision were not imported (TEST-EXECUTION); no stated retention rule for run directories (PRIV-RETENTION) and no documented rollback (OPS-ROLLBACK), both now documented; sensitive-data handling was not traced by either pass (DATA-SENSITIVE-HANDLING).

## What this showed about Flight Check

- **Repeat passes steady the controls more than they add findings.** The second pass reported one finding, which the verifier rejected, so no duplicates needed merging. Merging is covered by unit tests and should be checked on a larger project.
- **Masking false positive.** The high-entropy secret rule redacted a long dated file path (`docs/audits/<date>-...`) in finding text, which makes the affected component unreadable. Not fixed yet: narrowing a secret-masking rule needs care, because a missed secret is worse than an over-masked path.

The follow-up audit verifies the REMEDIATED items. This record reflects only the evidence examined. It is not a certification or legal advice.
