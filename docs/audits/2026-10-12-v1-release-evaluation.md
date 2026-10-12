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

Pending; this section is completed when the run finishes.

This record reflects only the evidence examined. It is not a certification or legal advice.
