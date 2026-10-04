# Fairtide Phase 4: remediation and re-audit, 2026-10-04

This page records what was fixed after the [Phase 3 self-audit](2026-10-04-self-audit.md), what the re-audits found, and what remains open.

**Authorization.** The owner approved all the Phase 3 recommendations. The owner also decided that an accepted CRITICAL finding must keep the ship decision at BLOCKED — CRITICAL RISK.

## Fixes for the Phase 3 issues (commit `84a64c9`)

| Phase 3 item | Fix | How it was checked |
|---|---|---|
| M1: decision-recording commands were pre-approved | Each skill pre-approves only the subcommands it needs. `ledger accept`, `revoke`, `legal`, and `close` always raise a permission prompt, and the lint rejects broad or decision-recording pre-approvals. The headless runner and the hardened-mode settings allow only the audit subcommands, deny the ledger commands, and keep the session from editing the ledger. Acceptances recorded after a run started do not count for that run. | Unit tests for acceptance timing; a deliberate lint violation was rejected. All three re-audits and the packet run below completed under the narrowed permissions. |
| Owner decision on CRITICAL acceptances | An accepted CRITICAL finding is treated as open; the decision stays BLOCKED — CRITICAL RISK, and the acceptance is shown. | Unit test |
| T2: HTML-escaped quotes | The evidence check decodes HTML entities once when a quote does not match as given, restores the file's text, and adds a note. The skill saves agent output as received. | Unit tests, including that decoding never rescues a quote that is simply absent |
| T1: contract mismatch | UNVERIFIED controls may carry `how_to_verify`, which the report shows. Agents report one issue per finding. | Unit test. In re-audit 1, the finding bundled in Phase 3 (FT-0002) was reported as separate findings FT-0003 and FT-0005. |
| FT-0001: PDF accessibility | The PDF declares its language (with an optional `language` in the packet request) and displays its title. The docs say the Markdown copy is the accessible version. Full tagging was **not** implemented. | Unit tests; the generated packet contains `/Lang (en)` and `/DisplayDocTitle true` |
| COMP-REGULATED-DATA, AI-COST-LIMITS | The README says that audited content, including any personal or regulated data, goes to the model provider. It also gives measured usage for this repository's audit and ways to limit usage. | Re-audit 1 cited the new README text. |
| M3, M4: leak gate scope | The denylist and the fixture canaries cover every committable file (78 at the time). The lint notes when the canary list is empty. | A test leak in `docs/` was caught. |
| SUPPLY-KNOWN-VULNS | `pip-audit` on the CI Python install set for 3.9 and 3.12, and `npm audit` for the pinned Claude Code CLI; no known vulnerabilities. Scan output and its scope are in `.fairtide/evidence/`. | The control was VERIFIED in both re-audits. |
| Report publishing | `docs/testing.md` now says summaries go in `docs/audits/` and full reports belong with releases, outside the audited tree. It also explains headless interim results. | — |

## Re-audits

| Run | Audited code | Ship decision | Reason | Findings |
|---|---|---|---|---|
| Phase 3 baseline `20261004T223431Z` | `3329416` | BLOCKED — INSUFFICIENT EVIDENCE | SUPPLY-KNOWN-VULNS unverified (no scan) | FT-0001 (LOW), FT-0002 (INFO) |
| Re-audit 1 `20261004T230555Z` | `1c88f1e` | BLOCKED — INSUFFICIENT EVIDENCE | AUTHZ-SERVER-SIDE marked UNVERIFIED: the agent read Claude Code's tool permissions as "permissions" in the control's applicability text | Six new: FT-0003 to FT-0008 |
| Re-audit 2 `20261004T231719Z` | `54686c3` (the working tree also had one inventory-instruction line from `79d6a2e`) | BLOCKED — INSUFFICIENT EVIDENCE | APPSEC-OUTPUT-ENCODING and AI-DATA-BOUNDARIES marked UNVERIFIED because the agents said they had not read that code closely | None |

**Findings from re-audit 1:**
- **FT-0003 (INFO):** the product name differs from the repository, marketplace, and schema identifiers.
- **FT-0004 (INFO):** CONTRIBUTING does not state contribution terms.
- **FT-0005 (INFO):** no terms of use or governing-law decision.
- **FT-0006 (LOW):** `SECURITY.md` points reporters to private vulnerability reporting, which is not yet enabled.
- **FT-0007 (LOW):** the README promised secret masking in all output files, but JSON outputs and the ledger were not masked.
- **FT-0008 (INFO):** CI installs its test tools without lockfiles.

### Second round of fixes (commits `9e773cc`, `79d6a2e`, `9a34880`)

- **FT-0007:** Likely secrets are masked in all agent text when a run is assembled, so `audit.final.json` and the ledger never hold them. The README now says exactly which outputs are masked. Re-audit 2's verifier reported **FIXED_VERIFIED**, and the ledger marks it VERIFIED.
- **AUTHZ-SERVER-SIDE wording:** the control now applies to projects that serve multiple users or principals. Tool permission settings are excluded.
- **Pending fixes:** full audits now check every fix recorded as REMEDIATED. Re-audit 2 was the first run to do this.
- **Remediation-check bug:** in re-audit 2 the verifier reported FT-0001 **NOT_FIXED** (the PDF is still untagged), but the ledger ignored the check because that run treated accessibility as not applicable. Checks now always apply. As a result, FT-0001 is still recorded as REMEDIATED. The next audit will check it again, and while the PDF stays untagged, that check is expected to reopen it.
- **Contract wording:** agent retries launch a new agent. The inventory agent must quote a line for every file it cites, and the verifier's contract lists the allowed evidence kinds. Each of these had caused a validation retry.

## Ledger state after Phase 4

| ID | Ledger status | What is actually true |
|---|---|---|
| FT-0001 | REMEDIATED | Partly fixed. The verifier says it is not fixed because the PDF is untagged. Owner decision: implement tagging, or accept the LOW risk with the Markdown copy as the accessible version. |
| FT-0002 | NOT_REPRODUCED | Replaced by FT-0003 and FT-0005. |
| FT-0003 | NOT_REPRODUCED | Still true until the repository is renamed. |
| FT-0004 | NOT_REPRODUCED | Still true; an owner decision on contribution terms is needed. |
| FT-0005 | NOT_REPRODUCED | Still true; a business or counsel decision is needed. |
| FT-0006 | NOT_REPRODUCED | Still true until private vulnerability reporting is enabled. |
| FT-0007 | VERIFIED | Fixed and confirmed by re-audit 2. |
| FT-0008 | NOT_REPRODUCED | Still true; a technical, informational item. |

"Not reproduced" means a later run did not report the finding again. It does not mean the problem is gone. Close these only after confirming each is resolved, with `/fairtide:track close`.

## What the re-audits showed about Fairtide itself

- **Detection varies a lot between runs.** Three audits of nearly identical code produced three different findings sets (2, 6, and 0 findings) and three different reasons for the same fail-safe decision. Issues that clearly still exist, such as FT-0006, were not reported again in re-audit 2. The gate failed safe every time, but the findings list of a single run is not a complete picture. Phase 5 must measure this, including several runs per fixture.
- **The mechanical limits held.** In re-audit 2, the coordinating session tried a shell command outside Fairtide's script (`python3 -c "print(1)"`, probably a Python availability check), against the skill's instructions. The narrowed permissions denied it. Both re-audits and the packet run completed with only the subcommands they need.
- **Deterministic validation caught every malformed agent reply, and each was fixed by one retry:**
  - inventory evidence without lines (both re-audits);
  - a missing legal review flag (re-audit 1);
  - a wrong evidence kind from the verifier (re-audit 2);
  - an extra field in `summary.json` (re-audit 1).
- **The verifier was honest about a partial fix** (FT-0001 NOT_FIXED) and confirmed a real one (FT-0007).

## Attorney Review Packet

`/fairtide:legal-packet FT-0003,FT-0004,FT-0005` ran headlessly against re-audit 1, with only `runs`, `findings`, and `packet` allowed. It produced an 11-page PDF with a Markdown copy. The PDF declares its language and displays its title, and its text contains the required disclaimer and no compliance claims. The packet stays in the gitignored run directory (`.fairtide/runs/20261004T230555Z/`) and is not committed.

## Open items for the owner

1. Enable private vulnerability reporting (FT-0006). It is the most urgent item in [the GitHub settings checklist](../maintainers/github-settings.md).
2. Rename the repository to `fairtide` (FT-0003); the install instructions are then updated to match.
3. Decide contribution terms (FT-0004), and terms of use and governing law (FT-0005, LEGAL-TERMS, LEGAL-GOVERNING-LAW). The Attorney Review Packet is ready if you want counsel's view.
4. Decide on FT-0001: full PDF tagging, or accept the LOW risk through `/fairtide:track`.
5. Optionally lock the CI tool installs (FT-0008): hash-pinned Python requirements and an npm lockfile.

## Checks at the end of Phase 4

71 unit tests pass on Python 3.11 and 3.9. Agent sync, the repository lint (with the private denylist across every committable file), and strict plugin validation all pass.

This record reflects only the evidence examined. It is not a certification or legal advice.
