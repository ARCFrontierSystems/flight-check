# Usage

All Flight Check commands are run explicitly. Claude never starts an audit on its own, and content inside a project cannot trigger one.

## `/flight-check:audit`

```
/flight-check:audit [path] [--context FILE] [--evidence FILE ...] [--domains a,b] [--passes 2] [--changed] [--untrusted --out DIR]
```

- `path`: the project to audit. Defaults to the current directory.
- `--context FILE`: project context the code cannot reveal. Without it, those questions are reported as unknown or as questions for review, never assumed. Example:

  ```markdown
  # Flight Check context
  - Product: subscription web app for small businesses
  - Markets: United States and the European Union
  - Users: adults only; no children's accounts
  - Data: contact details, billing handled by a payment provider, no health data
  - Deployment: managed container platform, managed Postgres with daily backups
  ```

- `--evidence FILE ...`: evidence Flight Check cannot produce itself. Place the files under `.flight-check/evidence/` in your project. Flight Check cites them as user-provided. Examples:
  - Dependency scanner output for the audited revision (for example from your ecosystem's audit tool or an OSV-based scanner). This can verify `SUPPLY-KNOWN-VULNS`.
  - CI test results for the audited revision. These can verify `TEST-EXECUTION`.
  - A record of a successful backup restore test. This can verify `REL-BACKUP-RESTORE`.
  - Accessibility test reports, and penetration test summaries.
- `--domains a,b`: limit the audit to some domains. The others are reported as not assessed, so the ship decision fails safe.
- `--passes 2` (or 3): run each domain agent two or three times independently and combine the results. Language-model audits vary from run to run; combining passes finds more issues and gives a steadier ship decision. A control counts as verified when any pass verified it with evidence, a gap reported by any pass stands, and the verifier merges repeated findings. Domain-audit usage grows about in proportion. Recommended before a release decision.
- `--changed`: reassess only what changed since the last finalized audit. See "Audits of changes only" below.
- `--untrusted --out DIR`: for code you do not own or trust. See [hardened-mode.md](hardened-mode.md).

A typical first audit ends in **BLOCKED — INSUFFICIENT EVIDENCE**, because some release-critical controls, such as dependency vulnerability status and tested restores, cannot be established from source code alone. That is intended. The report lists exactly which evidence would change the decision. You can then import that evidence, or explicitly accept the risk with `/flight-check:track`.

## `/flight-check:legal-packet`

```
/flight-check:legal-packet [FC-0003,FC-0007 | all] [--project NAME] [--for NAME] [--a4]
```

Builds **FLIGHT CHECK — ATTORNEY REVIEW PACKET** (PDF, plus a Markdown copy) from the latest audit's findings that warrant legal or business review. Several findings are combined into one packet. Each finding section includes the evidence, why review is recommended, specific questions for counsel, relevant decisions, and space for counsel's notes and decisions. The packet carries a disclaimer and is not legal advice.

## `/flight-check:track`

```
/flight-check:track status
/flight-check:track FC-0003 legal review requested
/flight-check:track FC-0003 decision received: counsel advised ...
/flight-check:track accept FC-0004
/flight-check:track close FC-0001
```

Records decisions only you can make.
- **Accepting a risk** needs the risk, the reason, the owner, the date, the scope, compensating controls, and optionally a review date. Flight Check asks for anything missing and never fills it in.
- **Counsel decisions** are recorded as your report of what counsel said. Flight Check never writes its own legal conclusions.
- **Closing** is possible only for findings that a re-audit verified as fixed, or that were not reproduced.

## `/flight-check:remediate`

```
/flight-check:remediate FC-0001,FC-0002
```

For your own projects only. Flight Check:
1. Proposes a fix and a regression test for each finding.
2. Waits for your approval.
3. Implements the fixes.
4. Runs your tests, if you agree.
5. Records the remediation.
6. Re-audits the affected domains.

A fix counts as verified only when the re-audit confirms it. Run a full `/flight-check:audit` before making a release decision.

## Project state

From version 1.5, Flight Check keeps project state next to the ledger in `.flight-check/`: `project.json` (identity, and later the profile and applicability), `registers.json` (requirements, decisions, risks, and legal review items, added in later 1.5 phases), and `history.jsonl`, an append-only log of every change. The design is in [design/project-state.md](design/project-state.md). The state is created by `state init`, or automatically by the next finalized audit or `ledger` command; an existing 1.0 ledger is kept unchanged and backed up first.

Commands (`python3 <plugin>/scripts/flight_check.py state <command> --root <project>`):

| Command | What it does |
|---|---|
| `show` | Summarize the state |
| `check` | Verify the files, the history log's hash chain, revisions, and the project's location; exits 1 on any problem |
| `recover` | Finish or roll back a command that was interrupted |
| `backup --reason TEXT`, `backups` | Make or list backups in `.flight-check/backups/` |
| `restore --from DIR` | Restore from a backup (the current state is backed up first; the history log is kept) |
| `export --out FILE`, `import --from FILE` | Move the state as one JSON bundle; importing a bundle from another project needs `--adopt` |
| `adopt --reason TEXT` | Confirm that the state belongs to the project at its current place in the repository, for example after moving an application inside a monorepo |

### Profile and applicability

Each finalized audit turns the inventory into **profile facts** (what the project uses, with the files that show it, labelled observed, inferred, or unknown) and an **applicability** entry per audit domain (applicable, not applicable, or uncertain). A domain counts as not applicable only with evidence of what was searched; otherwise it is uncertain and still assessed.

| Command | What it does |
|---|---|
| `profile show [--all]` | Current facts (`--all` adds superseded and historical ones) |
| `profile correct --id PF-0004 --assertion not_used\|excluded\|replace\|scope --reason TEXT [--value V] [--by NAME]` | Correct an observation. The observation and its evidence stay; if later evidence contradicts your correction, the fact is flagged |
| `profile correct --id PF-0004 --withdraw --reason TEXT` | Withdraw your corrections of a fact |
| `profile declare --aspect deployment.model --value "offline desktop app" --reason TEXT` | State a fact yourself; it is shown as declared, beside any observations |
| `applicability show` | Each domain's classification from evidence, your override, and the effective result |
| `applicability override --domain ai-llm --classification not_applicable --reason TEXT` (or `--clear`) | Declare a domain applicable or not applicable |

An override decides only what evidence leaves open. Declaring an uncertain domain not applicable lets the audit record it as not applicable ("declared by the project owner") when no agent assessed it. Declaring a domain not applicable when the evidence shows it applies is recorded as a contradiction, and the domain is still assessed. Declaring a domain applicable always keeps it in scope.

### Requirements, decisions, and risks

Three registers in `registers.json`. Records you add start as accepted (requirements, decisions) or open (risks); suggestions that Flight Check adds start as proposed and say so. Only you accept, decide, accept a risk, or close a record.

| Command | What it does |
|---|---|
| `req add --title T --description D --category C --criticality release-critical\|high\|normal [--link control:PRIV-DELETION] [--link FC-0003]` | Record a requirement |
| `req update --id REQ-0001 --status ...`, `req list`, `req show --id` | Change or inspect requirements |
| `decision add --title T --context C --decision D [--rationale R] [--assertion "statement the code should satisfy"]` | Record a decision |
| `decision update --id DEC-0001 --status SUPERSEDED --superseded-by DEC-0002`, `decision list`, `decision assertions` | Change decisions; list the assertions of accepted decisions |
| `risk add --title T --description D --domain payments --severity HIGH` or `risk add --from-finding FC-0007` | Record a risk |
| `risk accept --id RISK-0001 --reason R --owner O --date YYYY-MM-DD --scope S --compensating-controls C` | Accept a risk; severity does not change |
| `risk update --id RISK-0001 --status MITIGATION_PLANNED`, `risk list`, `risk show --id` | Change or inspect risks |

Links (`--link`) must point at records that exist: `FC-`, `REQ-`, `DEC-`, `RISK-`, `LR-`, `PF-` IDs, or `control:<control id>`.

What only evidence can do:
- A requirement becomes **VERIFIED** only when a finalized audit verifies all of its linked controls with checked evidence and none of its linked findings is still open. "Implemented" never means verified. If a later audit can no longer verify it, it goes back to IMPLEMENTED.
- A risk becomes **MITIGATED** automatically when all of its linked findings are verified fixed or closed, and **REOPENED** when one of them regresses. You can also mark a risk mitigated or resolved yourself, with a note on what changed.

### Legal review queue

Every legal or business question is a legal review item (`LR-0001`) in `registers.json`. A finding with a legal or business classification (other than INFORMATIONAL) gets one automatically, with the audit's questions and evidence references; you can add questions that no finding raised. Statuses: `NEEDS_REVIEW` → `AWAITING_COUNSEL` (or `AWAITING_BUSINESS_DECISION`) → `REVIEWED` → `ACTION_REQUIRED` → `RESOLVED`, or `NOT_APPLICABLE`. Flight Check never marks a question reviewed, resolved, or not applicable; you do, with a note on what you were told or decided. If a finding comes back after its item was closed, the item is reopened for review.

| Command | What it does |
|---|---|
| `legal list [--status S]`, `legal show --id LR-0001` | The queue; when nothing is open it says "Based on the project evidence currently available, no specific legal-review issue has been identified." |
| `legal add --title T --category C --description D --trigger WHAT_RAISED_IT [--question Q] [--jurisdiction J]` | Add a question no finding raised; the jurisdiction is recorded as unknown unless you give one |
| `legal update --id FC-0003 or LR-0001 --status AWAITING_COUNSEL --note "..."` | Record progress |

`/flight-check:legal-packet` builds a packet from findings and, on request, from open items without a finding (`item_ids` in the packet request). It refuses to build an empty packet. The 1.0 command `ledger legal` still works with the old status names and records the matching new status.

### What changed, and what is still current

Each finalized audit records the hashes of the files every conclusion cites, and keeps a list of the project's file hashes on this machine (`.flight-check/manifest.json`, not committed).

`changes --root <project>` compares the files now with the last finalized audit and reports:
- the changed files (only content changes count; timestamps never do);
- the findings, controls, positive controls, profile facts, legal items, and requirements those files support, and which of them now rest on **stale** (changed) or **missing** evidence;
- accepted decisions whose review triggers match the changes;
- the domains to reassess and the domains whose results can be carried forward. A changed file that no rule or stored conclusion explains, an incomplete file list, or a missing manifest (for example in a fresh clone) widens reassessment to every applicable domain;
- whether the latest readiness decision is now historical.

### Audits of changes only

`/flight-check:audit --changed` runs `changes` first, then launches only the domain agents for the domains a change can affect. The inventory always runs again. Every other applicable domain is **carried forward**:
- its coverage is reported as `CARRIED_FORWARD`, labelled "carried forward, not reassessed", with the run that last assessed it;
- its controls keep their last recorded state only while the files they cited are unchanged; a control whose cited file changed or disappeared becomes UNVERIFIED, so a release-critical one blocks as insufficient evidence;
- its open, accepted, or dismissed findings from that assessment still count in the ship decision exactly as before, listed as carried forward;
- a carried-forward control never verifies a requirement for the first time; an earlier verification stands only while its evidence is unchanged.

A domain is carried forward only when the run that wrote the last file list assessed it (or carried it forward itself). A domain that run skipped, for example with `--domains`, is reassessed. If files change while the audit runs, finalize re-checks and stops carrying forward the affected domains; they then count as not assessed. The file list recorded for the next comparison is the one taken when the run started, so such changes are reported again next time. When nothing changed and every applicable domain was assessed, no run is started.

Your triage of a finding: `ledger mark --id FC-0003 --status UNDER_REVIEW|REMEDIATION_PLANNED|FALSE_POSITIVE|SUPERSEDED|OPEN --reason TEXT [--superseded-by FC-0007]`. A finding you mark false positive or superseded that the next audit still reports is shown as such, and the ship decision is at best READY WITH ACCEPTED RISKS; a CRITICAL one still blocks.

A finding that was verified fixed or closed and comes back is a **regression** when it matches by fingerprint, by rule and file, or by the same quoted line; when only the file and the wording match, it is a **possible regression**.

The ship decision also counts the project's records: a release-critical requirement that is not verified by evidence makes the evidence insufficient, a legal item you marked ACTION_REQUIRED blocks release, and an open CRITICAL or HIGH risk in the register (one not represented by findings) blocks it; an accepted CRITICAL risk still blocks.

Commit `project.json`, `registers.json`, `history.jsonl`, and `ledger.json` if your team shares the history. Flight Check writes `.flight-check/.gitignore` so run output, backups, and lock files stay local.

## Continuous integration

The audit itself needs Claude. Teams typically run it before a release and commit the ledger. To gate a pipeline on a finished audit:

```bash
python3 path/to/plugins/flight-check/scripts/flight_check.py gate .flight-check/runs/<run>/audit.final.json --exit-code
```

Exit codes:

| Code | Decision |
|---|---|
| 0 | READY FOR RELEASE |
| 2 | READY WITH ACCEPTED RISKS (use `--allow-accepted-risks` to return 0) |
| 3 | NOT READY — REMEDIATION REQUIRED |
| 4 | BLOCKED — INSUFFICIENT EVIDENCE |
| 5 | BLOCKED — CRITICAL RISK |

To run an audit headlessly, use `claude -p "/flight-check:audit"` with `--plugin-dir`, `--permission-mode dontAsk`, and a settings file that allows Flight Check's script and writes to `.flight-check/`. See [hardened-mode.md](hardened-mode.md) for a complete example.
