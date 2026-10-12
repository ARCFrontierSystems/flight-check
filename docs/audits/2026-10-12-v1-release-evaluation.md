# Flight Check 1.0 release evaluation, 2026-10-12

This record covers the blind evaluation of the 1.0.0 release candidate on a new held-out test application, and the release self-audit. Following the blind-test protocol, it reports aggregates only: no fixture code, names, or answer-key contents.

## Held-out application

- **Fixture:** testbed-C, a web application of about 90 files in a language not used by the earlier fixtures. It was written in a separate session that never saw Flight Check's development, and its answer key stayed with the owner until all runs had finished. It has 33 planted issues (21 must-find, 12 stretch; 1 CRITICAL), 9 secure decoys, and one domain that does not apply.
- **Configuration:** three independent runs of `/flight-check:audit --passes 2`, with no imported evidence, at plugin commit `2e5b084`.
- **Cost:** $9.64, $11.14, and $8.70 of usage. All three runs finished with no denied tool calls.

| Metric | App C, 1.0 candidate, 2 passes | App B, Phase 7, 1 pass |
|---|---|---|
| Ship decision as expected | 3 of 3 | 3 of 3 |
| Recall, strict | 0.74 (0.73–0.76) | 0.60 (0.48–0.68) |
| Must-find recall, strict | 0.79 (0.76–0.81) | 0.79 (0.69–0.88) |
| Must-find recall, location only | 0.86 | 0.94 |
| Must-find recall, any evidence cited | 0.95 | 1.00 |
| Run-to-run overlap of detected issues (Jaccard) | 0.90 | 0.75 |
| Decoy false-positive rate | 0.00 | 0.08 |
| Severity within tolerance | 1.00 | 0.95 |
| Legal classification correct | 6, 6, 5 of 7 | 8, 7, 6 of 12 |
| Findings per run | 42–44 | 25–30 |
| Findings with failed evidence checks, all runs | 2 | 8 (6 caused by an escaping bug fixed before 1.0) |

- **The CRITICAL issue** was found in every run at its exact location and rated CRITICAL. In Phase 7 the CRITICAL issue of App B was rated one level too low.
- **Strict** means the finding cites the issue's location and is filed under one of its domains. **Location only** also counts a finding filed under another domain. Unlisted findings are not counted as false positives; `precision_lower_bound` treats all of them as wrong and was 0.59.
- **The two fixtures differ,** so the comparison is indicative only. The two biggest changes since Phase 7 are repeat passes and the product-logic procedures.

## Missed issues

- **Must-find, never matched strictly (4 of 21):**
  - a vulnerable dependency version, which needs a vulnerability database (Flight Check does not query one; import scanner output with `--evidence`);
  - a race between checking and using a limited quantity;
  - an advertised price that leaves out a mandatory fee until checkout;
  - a database port exposed by deployment configuration. This one was reported in every run, but under a different domain.
- **Stretch issues never found (3 of 12):** a credential that stays valid after a transfer, unescaped user text in an email template, and spreadsheet formula injection in an export. Another 3 stretch issues were found in some runs only.
- These are issue classes, not fixture specifics. Under the protocol, App C is now spent: nothing learned from it may be presented as a held-out result again.

## Scorer correction

While scoring, the blind-test scorer (`tools/blindtest/score.py`, development-only) matched a finding about a neighbouring issue to the CRITICAL issue, because that finding overlapped one of the CRITICAL issue's alternate locations by more lines. The run's real CRITICAL finding was then left unmatched and counted as a decoy false positive, and the neighbouring issue was counted as missed. The scorer now ranks a match on an issue's primary location above a match on an alternate location, with a unit test. All numbers above, for both App C and App B, use the corrected scorer. With the old scorer, App C scored 0.71 recall and 0.75 must-find recall.

## Release self-audit

One audit of this repository at the release candidate (run `20261012T220354Z`, version 1.0.0, `--passes 2`, about $5.26), with the local test results and the CI vulnerability-scan output imported as evidence. An earlier attempt stopped partway when a safety classifier interrupted the coordinating session while it saved one agent's results; nothing from it reached the ledger.

- **Ship decision: BLOCKED — INSUFFICIENT EVIDENCE.** Two release-critical controls were left UNVERIFIED by both passes: output encoding of quoted project text, and the data boundary of the model provider. Both had been VERIFIED with checked evidence in the self-audit earlier the same day, and the code behind them has changed since only in the secret-masking rule for file paths. This is run-to-run variance that repeat passes reduce but do not remove (see "Limitations" below). The model-provider control also depends on the user's own provider terms, which no repository can show.
- **Fixes verified:** all five findings recorded as REMEDIATED after the previous self-audit (FC-0015 to FC-0018 and FC-0020) were checked and marked VERIFIED. Test execution was verified from the imported test results, and four controls the previous self-audit left open (dependency lockfiles, rollback, documentation claims, sensitive-data handling) were VERIFIED.
- **Findings:** 4 LOW and 1 INFORMATIONAL; no CRITICAL, HIGH, or MEDIUM.

| ID | Finding | Status |
|---|---|---|
| FC-0021 | Controls reported VERIFIED reach the gate with a mechanical quote check but no verifier review | Open; design limitation, see below |
| FC-0022 | The GitHub repository description still overclaims ("enforce secure development practices") | Owner: change the description (`docs/maintainers/github-settings.md`, section 1) |
| FC-0023 | No terms of use beyond Apache-2.0 | Owner decision recorded: the license and its disclaimers |
| FC-0024 | The name overlaps a registered mark | Owner decision recorded (same subject as FC-0013) |
| FC-0025 | The ledger is not locked against two Flight Check commands writing it at the same time | Open; low impact for single-user use |

## Limitations at release

- **Variance remains.** With `--passes 2`, run-to-run overlap of detected issues was 0.90 on App C, but a whole control can still be left UNVERIFIED by both passes, as in the release self-audit. A BLOCKED — INSUFFICIENT EVIDENCE decision means "not established", never "found wrong"; check the named controls by hand or re-run.
- **Controls are not re-verified.** The verifier reviews findings, not VERIFIED controls; those rely on the mechanical quote check (FC-0021).
- **Known-vulnerable dependencies** need imported scanner output.
- **Must-find recall is about 0.8 by strict matching.** Roughly one in five must-find issues is missed or filed under another domain in a typical run.

This record reflects only the evidence examined. It is not a certification or legal advice.
