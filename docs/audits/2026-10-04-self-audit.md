# Fairtide self-audit, 2026-10-04 (Phase 3)

**Follow-up:** the fixes and re-audits are recorded in [the Phase 4 record](2026-10-04-phase-4-remediation.md).

Fairtide audited its own repository with `/fairtide:audit`. This page records the result exactly as Fairtide produced it, then a review of that result, including what the audit missed and how the tool behaved. Nothing was fixed in this phase. Fixes are Phase 4 and need the owner's authorization.

## How it was run

- **Audited:** commit `3329416` on branch `claude/wizardly-faraday-4u55nr`, the working tree right after the rename to Fairtide.
- **Command:** a headless `claude -p "/fairtide:audit --evidence .fairtide/evidence/local-checks.txt"`, with the plugin loaded from `plugins/fairtide`. The audit used the own-project trust tier, writes were limited to `.fairtide/`, and Fairtide's script was pre-approved.
- **Imported evidence:** [`.fairtide/evidence/local-checks.txt`](../../.fairtide/evidence/local-checks.txt). It holds the unit tests, agent sync check, lint, and strict plugin validation, all passing on Python 3.11. Fairtide never runs project code, so test results reach it only as imported evidence.
- **Run:** `20261004T223431Z`, about six minutes of wall-clock time.
  - Agents: the inventory agent; eight domain agents, with payments skipped as not applicable; and the verifier.
  - Cost: the CLI reported US$2.43 in both of its result events. That figure probably excludes the subagents, so the real usage is unknown (see T4).
- **Ledger:** committed at [`.fairtide/ledger.json`](../../.fairtide/ledger.json). The full run output (`report.md`, `audit.final.json`) stays in the gitignored run directory; see "Publishing full reports" below.

## Result as produced by Fairtide

**Ship decision: BLOCKED — INSUFFICIENT EVIDENCE**

The reason, verbatim: "Release-critical control SUPPLY-KNOWN-VULNS is UNVERIFIED: No scanner output was imported. The only third-party dependencies are the CI-only tools jsonschema 4.23.0 and @anthropic-ai/claude-code 2.1.289, and their vulnerability status was not checked."

### Findings

| ID | Severity and confidence | Verifier | Finding | Legal or business classification |
|---|---|---|---|---|
| FT-0001 | LOW, LIKELY | Confirmed | The Attorney Review Packet PDF is untagged and declares no document language (`plugins/fairtide/scripts/fairtide_lib/pdf.py:346`). | Business decision required |
| FT-0002 | INFORMATIONAL, POTENTIAL (downgraded from LOW, LIKELY) | Downgraded | There are no terms of use beyond the Apache-2.0 license and disclaimers. Repository, marketplace, and schema identifiers still use the former name `project-guardian`. | Legal review required (3 questions for counsel) |

There were no CRITICAL, HIGH, or MEDIUM findings.

### Controls not verified or not met

| Control | State | Note from the audit |
|---|---|---|
| SUPPLY-KNOWN-VULNS (release-critical) | UNVERIFIED | No scanner output for the CI tools. This is the gate reason. |
| LEGAL-TERMS | NOT_MET | No terms of use. |
| LEGAL-GOVERNING-LAW | UNVERIFIED | No governing-law or dispute-resolution decision. |
| COMP-REGULATED-DATA | UNVERIFIED | Audited project content, which may contain personal or regulated data, goes to the user's model provider. No guidance tells users this. |
| AI-COST-LIMITS | UNVERIFIED | Fairtide sets no token or cost limits and gives no per-audit cost guidance. |

The audit also listed what it did not see: CI results on Python 3.9 and 3.12, a scan of git history for secrets, and the live GitHub repository settings.

**Positive controls it reported:**
- The agents are read-only.
- Evidence paths are confined to the audit root.
- Writes are atomic.
- Actions are pinned by SHA, with read-only workflow permissions.
- The plugin scripts use no network or subprocess calls.
- The gate is computed by the script.
- The 61 unit tests passed.

**Coverage:** 18 domains assessed, and payments not applicable.

## Review of the findings

- **FT-0001: agree.** The PDF catalog has no `/MarkInfo`, `/StructTreeRoot`, or `/Lang`. This was also on the reviewer's independent list. The Markdown copy of the packet is the accessible version, but `docs/limitations.md` does not say so. LOW is the right severity.
- **FT-0002: partly agree.** It bundles two separate items:
  - The lack of terms beyond Apache-2.0 and the disclaimers. This is a fair question for counsel, because the tool produces legal-adjacent output.
  - The old name in identifiers. This is a rename task already tracked in `docs/maintainers/github-settings.md`, not a legal issue.

  The verifier's downgrade is reasonable. Bundling two issues into one finding is a quality problem in its own right; findings should be atomic.
- **SUPPLY-KNOWN-VULNS: correct.** Blocking the gate is the intended fail-safe behavior, since nothing was scanned.
- **COMP-REGULATED-DATA and AI-COST-LIMITS: good catches the reviewer had not listed.** The documentation tells users neither that audited code goes to their model provider nor roughly what an audit costs.
- **AI-TOOL-AUTHORIZATION, marked VERIFIED: disagree.** The audit looked only at the agents' tool limits. See M1.

## What the audit missed

| # | Issue | Reviewer's severity | Evidence |
|---|---|---|---|
| M1 | The audit and legal-packet skills pre-approve every `fairtide.py` subcommand, including `ledger accept`, `ledger legal`, and `ledger close`, which record decisions only the user may make. Only instructions stop the coordinating session from running them, and in headless runs the same rule holds for the whole session. If injected project content survived an agent and persuaded the coordinating session, it could record an "accepted risk" for an existing finding or an unverified control before finalize. That turns BLOCKED or NOT READY into READY WITH ACCEPTED RISKS. Accepted CRITICAL findings are excluded from the critical block. The report would still list the acceptance, and the ledger diff would show it in git, but the guarantee that only a human decides is not enforced mechanically. | MEDIUM | `plugins/fairtide/skills/audit/SKILL.md:6`, `plugins/fairtide/skills/legal-packet/SKILL.md:6`, `plugins/fairtide/scripts/fairtide_lib/cli.py:381`, `:397`, `:415`, `plugins/fairtide/scripts/fairtide_lib/gate.py:45-48`, `tools/blindtest/run_audit.py:56` |
| M2 | Agent output must be saved unchanged, but nothing checks that it was. In this run the coordinating session did change a quote (see T2). Its change was benign and disclosed. | LOW | `plugins/fairtide/skills/audit/SKILL.md:79`, `:106` |
| M3 | The private-name leak gate scans only `plugins/`, but the rule forbids private names anywhere in the repository. | LOW | `tools/lint_plugin.py:287`, `CLAUDE.md:33` |
| M4 | `tools/leak-hashes.txt` contains no hashes yet, so the fixture-identifier check does nothing until the test application exists. | INFORMATIONAL | `tools/leak-hashes.txt` |
| M5 | The public default branch still has no license and the earlier, overclaiming README, because none of this work is merged. Fairtide audits the checkout it is given, not what is published on `main`. | LOW for the project (outside the audited checkout) | Default branch `main` |

## How the tool behaved

| # | Observation | Effect | Possible fix |
|---|---|---|---|
| T1 | The AI agent's first output failed validation: it put `how_to_verify` on an UNVERIFIED control. The output contract asks for that field in `unverified_areas` (`tools/agent-blocks/domain-output-contract.md:98`, schema line 139), but controls allow only `missing_evidence`. | One retry, which fixed it. | Allow the field on controls, since it is useful in the report, or state the difference explicitly. |
| T2 | The accessibility agent's quote `<< /Type /Catalog ... >>` reached the coordinating session HTML-escaped (`&lt;&lt;`). The session log shows the agent itself wrote the plain characters, so the escaping happened when the background agent's result was delivered. The coordinating session un-escaped the quote before saving it, which kept the evidence check passing but goes against "write it unchanged". | Any quote containing `<`, `>`, or `&` is affected, and these are common in code. Saved as received, such quotes would fail the evidence check and lose confidence. | Normalize HTML entities in the mechanical quote check, and tell the skill to save output as received. |
| T3 | In `claude -p`, the run emitted a first result event after launching the agents ("waiting for the agents to report back"), then a second, final one after rendering. | Anything that treats the first result as the outcome gets a non-answer. `tools/blindtest/run_audit.py` reads the run files instead, so it is unaffected. | Document this, and read results from the run directory. |
| T4 | The CLI's reported cost was identical in both result events. | The cost of an audit is not visible to users (compare AI-COST-LIMITS). | Measure a typical audit and document it. |

## Limits of this self-audit

- **A single run.** Audits are not deterministic, and a second run may differ.
- **Shared blind spots.** The same model family built Fairtide and audited it, so they may miss the same things. The Phase 5 blind test, with hidden ground truth, is the real measure of detection quality.
- **Evidence gaps.** There was no vulnerability scan, no scan of git history, and no CI run. CI triggers only on `main` and pull requests, and no pull request exists yet. Separately from the audit, the unit tests were run locally on Python 3.9.23 and 3.13.14: all 61 ran, with 60 passing and 1 skipped.
- **Publishing full reports.** `docs/testing.md:49` says self-audit reports are published under `docs/audits/`. This page summarizes the report rather than committing the full 33-section version. Fairtide audits this repository, so the next self-audit would read the old report as project content and could cite it instead of the code. Deciding where full reports live (for example, as release assets) is part of Phase 4.

## Proposed Phase 4 work

None of this has started, and it needs authorization.

1. **M1:** Narrow the pre-approval for each skill:
   - audit: `init-run`, `validate`, `finalize`, `render`, `runs`.
   - legal-packet: `runs`, `findings`, `packet`.
   - track and remediate: keep the ledger commands they need.

   Also make finalize ignore acceptances and legal decisions recorded after the run started, so nothing recorded during an audit can change that audit's gate. Separately, the owner should decide whether an accepted CRITICAL finding may ever lift the CRITICAL block.
2. **T2:** Normalize HTML entities in the quote check, and say "save as received" in the skill.
3. **T1:** Allow `how_to_verify` on controls and show it in the report.
4. **FT-0001:** Declare the document language and the title display. Document the Markdown packet as the accessible format in `docs/limitations.md`; full PDF tagging is a larger job.
5. **COMP-REGULATED-DATA, AI-COST-LIMITS:** Document that audited content goes to the model provider, and the typical cost per audit.
6. **M3:** Apply the private denylist to all tracked files.
7. **SUPPLY-KNOWN-VULNS:** Import `pip-audit` and `npm audit` output as evidence, or record an explicit acceptance.
8. **FT-0002, LEGAL-TERMS, LEGAL-GOVERNING-LAW:** These are business and counsel decisions for the owner. `/fairtide:legal-packet FT-0002` can prepare an Attorney Review Packet.
9. **`docs/testing.md`:** Correct the promise about where reports are published.

Then re-audit with `/fairtide:audit` and compare against the ledger.

This record reflects only the evidence examined. It is not a certification or legal advice.
