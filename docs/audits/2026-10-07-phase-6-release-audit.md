# Flight Check Phase 6: public release audit, 2026-10-07

This page checks the repository for public distribution, item by item, as Phase 6 of the development plan requires. Where possible, it uses deterministic checks. It also includes one Flight Check audit of this repository.

## Checklist

| Item | Result | How it was checked |
|---|---|---|
| No secrets | Pass | A pattern scan of the full git history (40 commits, all branches) for cloud, GitHub, Slack, Stripe, and model-provider keys, private keys, JWTs, and long password or token assignments found nothing; the only hit was a `${DB_PASSWORD}` placeholder. The repository lint, which forbids credential-like strings, passes. |
| No private information | Pass | The private denylist (5 terms) and the fixture canary hashes found nothing anywhere in the history. Commit identities are GitHub no-reply addresses only. Test-fixture names and answer keys stay outside the repository. |
| No project-specific assumptions | Pass | The shipped plugin contains no absolute paths. The leak gate finds no fixture identifiers. Phase 5 changes were general procedures; examples that mirrored a test application were replaced. |
| Correct packaging | Pass, with one blocker | `claude plugin validate --strict` passes, and a clean install from the local marketplace works and enables the plugin. **Blocker:** the default branch (`main`) contains only the initial README, so the documented `claude plugin marketplace add ARCFrontierSystems/flight-check` cannot work until the development branch is merged. |
| Documentation | Pass after fixes | Every relative link in the Markdown files resolves. The README status line was out of date (FC-0010) and now links the Phase 5 record. |
| License | Pass | Apache-2.0 `LICENSE`; `plugin.json` declares `Apache-2.0`. GPL-2.0 benchmark material used in testing is built outside the repository and never committed. |
| Security | Pass after fixes | Plugin scripts are standard-library only, with no network or subprocess use (enforced by the lint). Skills pre-approve only the subcommands they need; decision commands always prompt. The release self-audit found no CRITICAL or HIGH findings. Its one technical finding (FC-0009, below) was fixed. |
| Privacy | Pass after fixes | The README states that audited content goes to the user's model provider, and which outputs are masked. It now also says that run directories hold quoted project content and can be deleted (FC-0012). |
| Legal disclaimers | Pass | The README, every Attorney Review Packet, and every report say Flight Check gives no legal advice and claims no compliance or certification. The validator rejects such conclusions in agent output and packet requests. |
| Test coverage | Pass | 91 unit tests pass on Python 3.11 and 3.9. Line coverage of the shipped scripts is 86%. The uncovered parts are mostly error paths and the thin command-line entry point. |
| Installation | Pass locally | The local marketplace install succeeded. Installing from GitHub depends on the merge above. |
| Reusability | Pass | Agents work from evidence in any ecosystem. Phase 5 exercised a Python web application, a Java servlet application, and this repository. Nothing in the plugin depends on this repository or a test application. |

## Release self-audit

One Flight Check audit of this repository (run `20261007T120434Z`, about $3.89 of usage, no denied tool calls), with the earlier CI vulnerability-scan output imported as evidence:

- **Ship decision: BLOCKED — INSUFFICIENT EVIDENCE.** The only reason is FC-0001, the untagged PDF. A recorded partial fix was judged not effective, and the decision (implement tagging, or accept the LOW risk with the Markdown copy as the accessible version) belongs to the owner.
- **Findings:** no CRITICAL or HIGH; 1 MEDIUM and 6 LOW.

| ID | Severity | Finding | Status |
|---|---|---|---|
| FC-0013 | MEDIUM | The name overlaps existing marks and no counsel review is recorded | Owner: counsel review before public launch |
| FC-0009 | LOW | The quote check accepted quotes made mostly of `...` gaps | Fixed: such quotes need at least 12 checkable characters, or they count as unchecked. Recorded as REMEDIATED for the next audit to verify |
| FC-0010 | LOW | The README said the blind evaluation was unpublished | Fixed; recorded as REMEDIATED |
| FC-0011 | LOW | `SECURITY.md` points to private vulnerability reporting, which is not yet enabled | Owner: enable it in the repository settings |
| FC-0012 | LOW | No guidance on keeping or deleting run directories | Fixed in the README; recorded as REMEDIATED |
| FC-0014 | LOW | No terms of use, governing-law decision, or contribution terms beyond the license | Owner or counsel decision |
| FC-0015 | LOW | CI installs a test tool without a lockfile | Owner: optional |

## Owner actions before public release

1. **Merge the development branch into `main`.** Without the merge, the documented installation fails.
2. **Decide FC-0001:** implement PDF tagging, or accept the LOW risk with `/flight-check:track accept FC-0001`. It is the only item blocking the ship decision.
3. **Enable private vulnerability reporting** (FC-0011). See `docs/maintainers/github-settings.md`, which also lists the CI secret `FLIGHT_CHECK_LEAK_DENYLIST` and the repository description and topics.
4. **Counsel:** the name (FC-0013), and terms, governing law, and contribution terms (FC-0014).
5. **Optional:** lock the CI tool installs (FC-0015).

Phase 7 audits the held-out App B and re-runs App A on the release build, so both are measured in one batch.

This record reflects only the evidence examined. It is not a certification or legal advice.
