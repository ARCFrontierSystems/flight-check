# Moving from Flight Check 1.0 to 1.5

Flight Check 1.5 keeps everything 1.0 does: the same audit, the same five ship decisions, the same report, and the same commands. It adds project state, so Flight Check remembers the project between audits. This page says what changes in your project when you first use 1.5, and how to go back.

## What happens on the first 1.5 command that changes state

The first audit you finalize with 1.5 (or `state init`, or any command that records something, such as `ledger accept` or `legal update`) does this once, in this order:

1. **Backs up** your `.flight-check/ledger.json` to `.flight-check/backups/<time>-migrate-from-1-0/`, with the hash of each file.
2. **Creates** `project.json` (a new project identity, the profile, applicability, controls, and readiness), an empty `registers.json`, and `history.jsonl`, whose first event records the migration.
3. **Moves legal statuses** out of ledger entries into legal review items (`LR-0001`, ...), keeping their history, and sets the ledger to schema 2.0. The backup from step 1 covers this step too; a separate `<time>-migrate-ledger-2-0` backup is made only when 1.5 state already existed. An INFORMATIONAL classification that was never worked on stays a classification and opens no item. The 1.0 statuses map like this:

| 1.0 legal status | 1.5 legal item status |
|---|---|
| `OPEN` | `NEEDS_REVIEW` |
| `LEGAL_REVIEW_REQUESTED` | `AWAITING_COUNSEL` |
| `COUNSEL_REVIEWED`, `DECISION_RECEIVED`, `IMPLEMENTED` | `REVIEWED` |
| `IMPLEMENTATION_REQUIRED` | `ACTION_REQUIRED` |
| `VERIFIED`, `CLOSED` | `RESOLVED` |

4. **Writes** `.flight-check/.gitignore`, so run output, backups, reports, the lock, and the local file list stay on your machine.

Finding IDs, statuses, accepted risks, and history are kept. If any step fails, the original files are left as they were and the error names the backup.

Read-only commands (`ledger show`, `findings`, `runs`, `render`) work on a 1.0 ledger without migrating it.

## What to commit

Commit `project.json`, `registers.json`, `history.jsonl`, and `ledger.json` if your team shares Flight Check's history. Also commit `audited-files.json` if you use the release check in CI (see "Release check" in [usage.md](usage.md)). Everything else in `.flight-check/` is local.

## What behaves differently

- **Legal status** lives in the legal review queue. `legal update --id FC-0003 ...` (or the item ID) records it; the 1.0 command `ledger legal` with the old status names still works and records the matching new status. `legal_status` in `audit.final.json` holds the item status.
- **The ship decision has more inputs**: release-critical requirements not verified by evidence, legal items marked `ACTION_REQUIRED`, and open or accepted CRITICAL risks in the risk register. A project without such records gets the same decision as in 1.0.
- **Positive controls** have stable IDs (`PC-0001`) across runs; 1.0 numbered them per run (`PC-001`).
- **New finding statuses** that only you set: `UNDER_REVIEW`, `REMEDIATION_PLANNED`, `FALSE_POSITIVE`, `SUPERSEDED`. A finding you dismissed that an audit still reports keeps the decision at best READY WITH ACCEPTED RISKS.
- **Regressions** say whether they matched exactly or only possibly.
- New, optional: `/flight-check:audit --changed`, `/flight-check:status`, `release-check` for CI, the `report` views, and the requirement, decision, risk, profile, and applicability commands in `/flight-check:track`.

## Going back to 1.0

1.0 does not accept a schema 2.0 ledger, and state written by a newer Flight Check is never rewritten by an older one. To return to 1.0:

1. Install 1.0 (see "Use a specific version" in [installation.md](installation.md)).
2. Copy `ledger.json` from the `...-migrate-from-1-0` backup in `.flight-check/backups/` back to `.flight-check/ledger.json`.
3. Optionally remove `project.json`, `registers.json`, and `history.jsonl`; 1.0 ignores them.

Anything recorded with 1.5 after the migration (registers, legal items, triage) is not available in 1.0. Keep the backup and the 1.5 files if you may come back.
