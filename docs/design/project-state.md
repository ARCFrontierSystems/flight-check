# Design: Project State (Flight Check 1.5)

Status: **design, approved phase by phase.** Nothing here is implemented until the phase that builds it says so. This document is the contract for Phases 3 to 8 of the 1.5 plan; when implementation shows that a detail must change, this document changes in the same pull request.

## 1. Purpose and principles

Flight Check 1.0 audits a project at one point in time and keeps a finding ledger. 1.5 adds persistent project understanding: what the project is, which audit domains apply, what has been required and decided, which risks and legal questions are open, and what changed since the last assessment.

Principles, in order of priority:

1. **Remembering is not verifying.** Every stored fact says where it came from and when it was last checked against the files. A stored conclusion whose evidence changed is shown as stale, never as current.
2. **Flight Check proposes; people decide.** Flight Check may create observations and proposals. Only an explicit user action accepts a requirement or decision, accepts a risk, resolves a legal question, or overrides an observation.
3. **Agents reason; the script decides.** Agents read the project and report. The bundled script (standard-library Python, no network, no subprocess) validates, stores, compares, and computes every status and gate.
4. **Local-first.** State lives in the project's `.flight-check/` directory. No hosted service, daemon, telemetry, or paid dependency.
5. **Compatible with 1.0.** Existing ledgers keep working, finding IDs and history are preserved, and 1.0 commands keep their behavior.
6. **Agent-neutral.** Nothing in the data model depends on Claude Code; a future adapter for another agent or a CI system reads and writes the same files.

## 2. Storage layout

All paths are relative to the audit root. One audit root is one project (section 4).

| File | Contents | Written by | In git |
|---|---|---|---|
| `.flight-check/project.json` | Identity, profile, applicability, corrections, state revision | script | yes |
| `.flight-check/ledger.json` | Finding lifecycle (1.0 file, schema 2.0 after migration) | script | yes |
| `.flight-check/registers.json` | Requirements, decisions, risks, legal review items | script | yes |
| `.flight-check/history.jsonl` | Append-only, hash-chained event log of every state change | script | yes |
| `.flight-check/manifest.json` | Content hashes of the project's files at the last assessment | script | no |
| `.flight-check/runs/` | Run directories (1.0, unchanged) | script and audit skill | no |
| `.flight-check/packets/` | Attorney Review Packets (1.0, unchanged) | script | no |
| `.flight-check/backups/` | Copies of the state files made before migrations, restores, and imports | script | no |
| `.flight-check/state.lock` | Held while a command changes state | script | no |

- The four committed files hold references, hashes, titles, and decisions, never source code or quotes. Quotes stay in run directories, as in 1.0.
- `manifest.json` changes on every assessment, so it is local only. Without it (for example in a fresh clone), change detection falls back to the hashes stored with each record and treats unlisted files as changed, which widens reassessment instead of narrowing it.
- Files are written with sorted keys and two-space indentation so that git diffs and merges stay readable.
- Only the script writes these files. Skills and agents never edit them directly.
- Flight Check writes `.flight-check/.gitignore` listing the local-only entries, so projects do not need to configure git themselves.
- The schema definitions for these files are in `plugins/flight-check/schemas/flight-check.schema.json` (`projectState`, `registers`, `logEvent`, and the definitions they use), and `tests/unit/test_state_schema.py` pins them with valid and invalid examples.

## 3. Common building blocks

### 3.1 Origin and source

Every stored fact and record carries both:

- **`origin`**, what kind of knowledge it is: `observed` (read directly from project evidence by Flight Check), `declared` (stated by the user), `inferred` (suggested by evidence but not confirmed), `unknown` (insufficient information), or `historical` (observed before, not currently).
- **`source`**, who wrote it: `user` or `flight-check`, as in the 1.0 ledger.

`declared` requires `source: user`. `observed` and `inferred` require `source: flight-check`. The script rejects any other combination, so a generated conclusion can never be stored as a user declaration.

### 3.2 Evidence references

```json
{"path": "src/routes/orders.py", "start_line": 40, "end_line": 58, "sha256": "<file hash when observed>",
 "anchor_hash": "<16 hex>", "observed_at": "2026-10-12T22:20:35Z", "run_id": "20261012T222035Z"}
```

- Paths are relative to the audit root and must resolve inside it (the 1.0 `resolve_within` rules, including symlinks).
- `sha256` is the hash of the whole file at the time the conclusion was drawn. It is what makes freshness checkable (section 9).
- An absence or user-provided item uses the 1.0 evidence kinds (`absence`, `user-provided`) and has no file hash.
- No quoted text is stored in state.

### 3.3 Identifiers

| Prefix | Record | Counter stored in |
|---|---|---|
| `FC-` | Finding (1.0) | `ledger.json` `next_id` |
| `PF-` | Profile fact | `project.json` |
| `REQ-` | Requirement | `registers.json` |
| `DEC-` | Decision | `registers.json` |
| `RISK-` | Risk | `registers.json` |
| `LR-` | Legal review item | `registers.json` |

IDs are never reused, even after a record is superseded or rejected.

### 3.4 Record history

Every record keeps a `history` list of events, as 1.0 ledger entries do:
`{"at", "status", "source", "note", "run_id", "by"}`, where `by` is an optional person or role given by the user. The global log (section 8) holds the same changes for the whole project, hash-chained.

## 4. Project identity and isolation

`project.json.identity`:

```json
{"project_id": "<32 hex, random, created once>", "created_at": "...", "name": "<display name>",
 "root": ".", "vcs": "git", "repository": "github.com/owner/repo"}
```

- `project_id` is created when state is first initialized and never changes. The display name is only a label.
- `root` is the audit root's path relative to the repository root (`.` for the whole repository, `apps/api` for one application in a monorepo). Each audit root has its own `.flight-check/` and its own identity, so two applications in one monorepo never share findings or decisions.
- `repository` is read from `.git/config` as a plain file (no subprocess), with any user name, password, or token removed. It is informational.
- **Clones and working copies** share the committed `project.json`, so they are the same project. A moved directory keeps its identity because nothing stores an absolute path.
- **Isolation checks:** every run records `project_id` in `run.json`. `finalize` refuses to merge a run whose `project_id` differs from the state it is merging into, and refuses state whose recorded `root` does not match the audit root's position in the repository. In both cases it reports the mismatch and asks for confirmation (`state adopt`) instead of merging histories.
- **Projects without git** work the same way; `vcs` is `none` and `repository` is omitted.
- **Branches** are not separate projects. The branch name (read from `.git/HEAD`) is recorded with each run, so the history shows which branch an assessment saw.

## 5. Project profile

`project.json.profile.facts` is a list of profile facts:

```json
{"id": "PF-0004", "aspect": "payments.provider", "value": "card payments through a hosted checkout",
 "origin": "observed", "source": "flight-check", "confidence": "CONFIRMED", "status": "current",
 "evidence": [<evidence references>], "first_observed": "...", "last_observed": "...", "run_id": "...",
 "corrections": [<corrections>], "history": [...]}
```

- Facts come from the inventory agent (the 1.0 `inventory.json` items). `IDENTIFIED` becomes `observed`; `UNVERIFIED` becomes `inferred` or `unknown`; `NOT_FOUND` with an absence search becomes an observed absence.
- Aspects use a fixed vocabulary in `references/profile-aspects.json` (application type, languages, frameworks, build tools, package managers, data stores, authentication, deployment, hosting, components, AI integrations, payments, sensitive-data indicators, third-party services, tests, CI/CD, policy documents). Unknown aspects from an agent are kept under `other.*`.
- `status`: `current`, `historical` (not reported by the latest inventory), or `superseded` (replaced by a newer fact for the same aspect). `presence` is `present`, `absent`, or `unclear`.
- A correction attaches to an aspect: when a new observation supersedes a fact, its active corrections carry over. A correction that says something is not used or excluded is contradicted when the latest observation finds it present.
- A fact needs evidence. A mention in a comment alone is not evidence for a capability; the inventory agent's instructions already require code or configuration.

### 5.1 Corrections

```json
{"at": "...", "by": "<person or role>", "source": "user", "assertion": "not_used",
 "value": "<optional replacement>", "reason": "...", "active": true}
```

- Assertions: `not_used`, `excluded` (intentionally out of scope), `replace` (the right value is `value`), `scope` (applies only to a named environment or component).
- A correction never deletes the observed fact or its evidence. Both stay visible.
- If a later assessment observes evidence that contradicts an active correction, the fact gets `"contradiction": true` and appears under "needs attention" in the status view. Neither side wins silently.
- Corrections are reversible: deactivating one adds an event; nothing is removed.

## 6. Applicability

`project.json.applicability` has one entry per domain:

```json
{"domain": "payments", "classification": "applicable", "confidence": "LIKELY",
 "reasoning": "...", "evidence": [<evidence references>], "components": ["src/billing/"],
 "evaluated_at": "...", "run_id": "...", "override": null, "contradiction": false}
```

- Classifications: `applicable`, `not_applicable`, `uncertain` (the 1.0 `yes`, `no`, `unknown`).
- `not_applicable` needs evidence of absence. 1.0 already requires this when an agent reports a domain or control as not applicable; 1.5 extends it to the inventory's applicability, which 1.0 does not check. Missing evidence gives `uncertain`, never `not_applicable`.
- **Overrides** (`{"classification", "by", "at", "reason"}`, user only) may resolve `uncertain` either way and may mark an `applicable` domain `not_applicable`. When the latest evidence says `applicable` and an override says `not_applicable`, the domain is still assessed, the contradiction is reported, and the gate treats the domain as applicable. An override can narrow attention; it can never hide evidence.
- In coverage, an uncertain domain that no agent assessed and that the owner declared not applicable is recorded as NOT_APPLICABLE with "declared by the project owner" and `reported_by: ["owner"]`; the ship gate then treats it as not applicable, with that attribution visible in the report.
- The history log records applicability only when a classification changes from an earlier evaluation, and every override or withdrawal.
- Legal review items and Attorney Review Packets are created only from specific triggers (section 7.4), never from applicability alone.

## 7. Registers

`registers.json` holds four lists. Every record has `id`, `title`, `status`, `origin`, `source`, `created_at`, `updated_at`, `links` (`findings`, `requirements`, `decisions`, `risks`, `legal`), `evidence`, and `history`.

Status changes marked **user** below are accepted only with `--source user`, which the skills pass only when the user explicitly asks for that change. Changes marked **evidence** are made by the script from a finalized assessment.

### 7.1 Requirements (`REQ-`)

Fields: `description`, `category` (security, privacy, compliance-readiness, accessibility, reliability, data-lifecycle, authentication, authorization, infrastructure, deployment, legal-business, product-behavior, release), `criticality` (`release-critical`, `high`, `normal`), `applicability` (domain or component), `components`, `controls` (Flight Check control IDs that verify it), `verification` (`{"state": "current|stale|unverified", "at", "run_id"}`), `last_review`.

A finalized audit sets `VERIFIED` when every linked control is VERIFIED with a passing evidence check and no linked finding is open, and moves a `VERIFIED` requirement back to `IMPLEMENTED` when that stops being true. A requirement without linked controls can only be verified once a later phase adds another evidence route; until then it stays at the status you set.

| Status | Set by |
|---|---|
| `PROPOSED` | Flight Check (a suggestion, labelled as such) or user |
| `ACCEPTED` | user |
| `IMPLEMENTED` | user, or Flight Check when an assessment observes the implementation |
| `VERIFIED` | evidence only: a finalized assessment verified it with current evidence |
| `BLOCKED`, `SUPERSEDED`, `REJECTED` | user |

`IMPLEMENTED` never implies `VERIFIED`. When a `VERIFIED` requirement's evidence becomes stale, its status stays and `verification.state` becomes `stale`, which the status view and gate both show.

### 7.2 Decisions (`DEC-`)

Fields: `context`, `decision`, `rationale`, `alternatives`, `owner`, `date`, `review_triggers` (paths, domains, or dependency names whose change should prompt review), `assertions` (short statements the implementation should satisfy, used for consistency checks), `superseded_by`.

| Status | Set by |
|---|---|
| `PROPOSED` | Flight Check (a recommendation) or user |
| `ACCEPTED`, `SUPERSEDED`, `REVERSED` | user |
| `NEEDS_REVIEW` | Flight Check, when a review trigger changes or an assessment reports a contradiction; or user |

**Consistency checks:** the `assertions` of `ACCEPTED` decisions are passed to the domain agents as statements to check. A contradiction is reported as an ordinary evidence-backed finding (rule `<domain>.decision-contradiction`) linked to the decision, which moves to `NEEDS_REVIEW`. Flight Check never concludes a violation without evidence, and never changes a decision itself.

### 7.3 Risks (`RISK-`)

Fields: `description`, `domain`, `severity`, `confidence`, `impact`, `likelihood` (only when supportable), `owner`, `mitigation_plan`, `acceptance` (the 1.0 acceptance fields plus `conditions` and `review_trigger`), `first_observed`, `last_reviewed`, `next_review`, `resolution_evidence`.

| Status | Set by |
|---|---|
| `OPEN`, `REOPENED` | Flight Check (from findings) or user |
| `UNDER_REVIEW`, `MITIGATION_PLANNED` | user |
| `ACCEPTED` | user, with a complete acceptance record |
| `MITIGATED`, `RESOLVED` | evidence: linked findings verified fixed; or user with resolution evidence |

- A finalized audit sets `MITIGATED` when every linked finding is VERIFIED or CLOSED (an accepted risk is left as accepted), and `REOPENED` when a linked finding of a mitigated or resolved risk regresses.
- A finding is evidence of a problem; a risk is the ongoing concern with an owner and a treatment. A risk may link several findings, and a finding may support several risks.
- Acceptance never lowers severity. An accepted CRITICAL risk still blocks the gate, as in 1.0.
- Acknowledged (`UNDER_REVIEW`), accepted, mitigated, and resolved are separate statuses.

### 7.4 Legal review items (`LR-`)

Fields: `category`, `description`, `trigger` (the finding, profile fact, or document that raised it), `jurisdiction` (or `jurisdiction_unknown: true`), `questions` (for counsel), `classification` (the 1.0 six classifications), `owner`, `review_date`, `resolution_notes`, `references`.

| Status | Set by |
|---|---|
| `NEEDS_REVIEW` | Flight Check or user |
| `AWAITING_BUSINESS_DECISION`, `AWAITING_COUNSEL`, `ACTION_REQUIRED` | user (Flight Check may propose) |
| `REVIEWED`, `RESOLVED`, `NOT_APPLICABLE` | user only, with a note |

- Items are created from specific triggers: a finding with a legal classification other than INFORMATIONAL, or a user request. A domain being applicable is not a trigger by itself. An item opened from a finding records the finding's questions and evidence references (file hashes, no quotes); later audits update its classification, add new questions (up to 20), and reopen it for review when the finding regresses after the item was reviewed, resolved, or marked not applicable.
- The finding's ledger entry keeps only `legal.classification` and `legal.item`; the item is the single source of the review status, and `legal_status` in audit results is read from it.
- Attorney Review Packets can be built from selected or unresolved items. When there are none, the packet command says "Based on the project evidence currently available, no specific legal-review issue has been identified." and adds that this is not a guarantee that no legal issues exist. It never produces an empty packet.

## 8. History log

`history.jsonl` has one JSON object per line:

```json
{"seq": 42, "at": "...", "actor": "user|flight-check", "by": "<optional person or role>", "command": "decision accept",
 "record": "DEC-0003", "change": {"status": ["PROPOSED", "ACCEPTED"]}, "reason": "...", "run_id": null,
 "revision": 42, "prev": "<hash of event 41>", "hash": "<sha256 of this event without hash>"}
```

- Every change to any state file appends one event. Nothing is ever removed or rewritten.
- The hash chain makes silent edits detectable (`state check`); it does not prevent them. This is tamper evidence, not tamper proofing.
- `project.json.revision` equals the `revision` of the latest event. Readiness decisions are stamped with this revision.

## 9. Freshness and change detection

- **Manifest:** at each finalized assessment, the script hashes the files under the audit root (skipping `.git/`, `.flight-check/`, and dependency and build directories, and never following symlinks out of the root) into `manifest.json`.
- **Evidence state** for any stored conclusion, from its evidence references:

| State | Meaning |
|---|---|
| `current` | every cited file still has the hash recorded with the conclusion |
| `stale` | a cited file changed since the conclusion was drawn |
| `missing` | a cited file no longer exists |
| `superseded` | a newer assessment replaced the conclusion |
| `unverified` | the conclusion never had checkable evidence |

- No time-based expiry. Staleness comes from change, and from runs that were interrupted.
- A changed timestamp alone is not a change; only content hashes count.
- **Impact:** `changes` compares the current files with the manifest, then maps each changed file to (a) every finding, control, positive control, profile fact, and record that cites it, and (b) domains, through rules in `references/impact-rules.json` (for example dependency manifests to supply-chain, templates and stylesheets to accessibility, migrations to data protection, privacy, authorization, and reliability, policy documents to legal-business and documentation). A change that no rule covers widens reassessment to every applicable domain. Skipped domains are labelled "carried forward, not reassessed", never "verified".
- **Carrying forward (Phase 7b):** `project.json` keeps `domains`: each domain's coverage in the latest run (`status`, `run_id`, `at`) and its last real assessment (`assessed_run_id`, `assessed_at`). A domain can be carried forward only when the run that wrote the manifest assessed it or carried it forward, so the chain of unchanged files is never broken by a run that skipped it. `init-run --changed` writes the scope; finalize re-checks it against the files and drops any domain that a change made during the run affects. Carried-forward controls keep their recorded state only while their cited files are `current` (or they cite none and no change affects the domain); otherwise they are UNVERIFIED. Findings that counted at the domain's last assessment still count. A carried-forward control never verifies a requirement for the first time. `init-run` stores the file hashes as the run starts (`files.json`), and finalize records those as the next manifest.

## 10. Finding lifecycle (ledger schema 2.0)

1.0 status names are kept, because CI scripts and reports already use them. Four statuses are added.

| Lifecycle step in the 1.5 plan | Ledger status |
|---|---|
| New | `OPEN`, with `first_seen_run` equal to the latest run (derived, not stored) |
| Open | `OPEN` |
| Under review | `UNDER_REVIEW` (new; user) |
| Remediation planned | `REMEDIATION_PLANNED` (new; user) |
| Potentially fixed | `REMEDIATED` (user claim; awaits verification) |
| Verified fixed | `VERIFIED` (evidence only) |
| Accepted | `ACCEPTED_RISK` (user) |
| Reopened | `REGRESSION` |
| False positive | `FALSE_POSITIVE` (new; user, with a reason) |
| Superseded | `SUPERSEDED` (new; replaced by another finding) |
| Not seen this run | `NOT_REPRODUCED` (1.0; not seen is not fixed) |
| Closed | `CLOSED` (user) |

- **Regressions** keep a `regression` record: original resolution, reappearance run and time, and `match`: `exact` when the finding matched by fingerprint, by the same rule and file, or by the same quoted anchor line; `possible` when only the file and a similar title matched. A `possible` regression is reported as such (`possible_regressions`), never as confirmed.
- Each ledger entry keeps `evidence_files` (path to hash of every file the finding cited when last observed); `project.json` keeps the latest state of each control with the same hashes. These are what `changes` traces.
- A finding the user marked `FALSE_POSITIVE` or `SUPERSEDED` that an audit still reports counts like an accepted risk in the ship decision (at best READY WITH ACCEPTED RISKS), and a CRITICAL one still blocks.
- A finding marked `FALSE_POSITIVE` that is observed again keeps its status but is flagged for review in the status view.
- Partial scans never mark findings in unassessed domains `NOT_REPRODUCED` (1.0 behavior, kept).
- Positive controls gain persistent records in the same ledger file (`PC-` keys under `positive_controls`), with evidence references, so a positive control whose code changed becomes stale instead of staying verified. They get stable four-digit IDs (`PC-0001`), matched across runs like findings; 1.0 numbers them per run (`PC-001`), so report numbering changes.

## 11. Readiness

The 1.0 gate keeps its five decisions and rules. 1.5 adds inputs:

- release-critical controls carried forward from an earlier run (Phase 7b) count as UNVERIFIED when their evidence is `stale` or `missing`;
- open legal review items in `ACTION_REQUIRED` are release-blocking; accepted requirements with criticality `release-critical` that are not `VERIFIED` with current evidence make the evidence insufficient;
- risks in the register that no finding represents: an open CRITICAL risk blocks as a critical risk, an open HIGH risk is release-blocking, an accepted CRITICAL risk still blocks, and other accepted risks lead at best to READY WITH ACCEPTED RISKS;
- a run that was interrupted never produces a readiness decision.

Every decision is stamped `{"decision", "at", "run_id", "revision", "commit"}`. The status view shows the latest decision with its stamp and, when files changed since, says that the decision is historical and which areas need reassessment. A person still makes the release decision.

## 12. Writing, safety, and recovery

- **One lock** (`state.lock`, replacing the 1.0 `ledger.json.lock`) is held by any command that changes state, from first read to last write.
- **Journal:** before replacing anything, a command copies the files it will change into `pending/` and writes `pending/journal.json` last, recording the log position it will reach. It then writes each changed file atomically (temporary file and rename, mode 0600, as in 1.0), appends its history events, and removes `pending/`. If `pending/` exists, an earlier command was interrupted: write commands refuse to run, and `state recover` either finishes the cleanup (when every event was written) or puts every journaled file back as it was before that command. Nothing is reset silently.
- **Validation on load:** every file is checked against its schema; size limits apply; unknown fields are rejected; paths must stay inside the root. An invalid file stops write commands with a clear error and leaves the file untouched. Read-only commands report the problem.
- **Symbolic links:** a state directory or state file that is a symbolic link is refused, so a planted link cannot make Flight Check read or replace a file elsewhere.
- **Backups:** written to `.flight-check/backups/<time>-<reason>/` (mode 0700) before migrations, restores, and imports, or on request (`state backup`), each with the hashes of its files. Flight Check never deletes backups. `state restore` restores the project, registers, and ledger from a backup, keeps the history log, and records the restore in it.
- **Export and import:** `state export` writes one JSON bundle of the four committed files with their hashes. `state import` validates the bundle, refuses a different `project_id` unless the user confirms with `state adopt`, and backs up first.
- **No secrets:** text fields pass through the 1.0 secret masking before they are stored; repository URLs lose any credentials; state stores no quotes.

## 13. Migration from 1.0

- A 1.0 project has `ledger.json` (schema 1.0) and no `project.json`. Read-only commands work on it as it is.
- Each state file carries its own `schema_version` and is migrated on its own, in steps, so a step can ship with the phase that needs it:
  1. **State files** (Phase 3): the first command that changes state (`state init`, a finalized audit, or a `ledger` command) backs up `ledger.json`, creates `project.json` with a new identity, an empty `registers.json`, and `history.jsonl` whose first event records the migration. The ledger itself is not changed by this step.
  2. **Legal items** (Phase 6): legal fields on findings become `LR-` items linked from the finding, keeping their history; 1.0 legal statuses map as in the table below.
  3. **Ledger 2.0** (Phase 7): the added finding statuses, regression records, and persistent positive controls.
- A failure at any step leaves the original files as they were and reports the backup location.
- State written by a newer Flight Check is never rewritten by an older one: an unknown `schema_version` makes write commands refuse.

| 1.0 legal status | 1.5 legal item status |
|---|---|
| `OPEN` | `NEEDS_REVIEW` |
| `LEGAL_REVIEW_REQUESTED` | `AWAITING_COUNSEL` |
| `COUNSEL_REVIEWED`, `DECISION_RECEIVED` | `REVIEWED` |
| `IMPLEMENTATION_REQUIRED`, `IMPLEMENTED` | `ACTION_REQUIRED` |
| `VERIFIED`, `CLOSED` | `RESOLVED` |

The 1.0 command `ledger legal` keeps working and updates the linked item.

## 14. Ownership summary

| Class | Examples | Who may change it |
|---|---|---|
| Developer-maintained | declarations, corrections, overrides, accepted requirements and decisions, acceptances, legal outcomes | user only |
| Flight Check observations | profile facts, applicability, findings, positive controls | script, from finalized assessments |
| Verified evidence | evidence references with hashes, verification records | script, from finalized assessments |
| Historical records | superseded facts, closed records, the history log | nobody; append only |
| Derived conclusions | gate decisions, freshness states, status summaries | computed on demand; only readiness stamps are stored |

## 15. Interfaces planned

Script subcommands (each with Markdown and JSON output): `state init|show|check|export|import|adopt|restore`, `profile show|correct`, `applicability override`, `req add|update|list`, `decision add|update|list`, `risk add|update|accept|list`, `legal add|update|list` (the 1.0 `ledger legal` stays), `changes`, `status`. Skills: a read-only `status` skill for natural-language questions, `track` extended for recording, and `audit --changed`. Pre-approval follows the 1.0 rule: read-only subcommands may be pre-approved; every command that records a decision always asks.

## 16. Out of scope for 1.5

Background services or automatic triggers, hosted storage, team permissions, organization policies, and agent-specific adapters (planned for 2.0 and later). The data model leaves room for them: identities are global, records are agent-neutral, and the history log is a plain event stream.
