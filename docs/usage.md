# Usage

All Seaworthy commands are run explicitly. Claude never starts an audit on its own, and content inside a project cannot trigger one.

## `/seaworthy:audit`

```
/seaworthy:audit [path] [--context FILE] [--evidence FILE ...] [--domains a,b] [--untrusted --out DIR]
```

- `path`: the project to audit. Defaults to the current directory.
- `--context FILE`: project context the code cannot reveal. Without it, those questions are reported as unknown or as questions for review, never assumed. Example:

  ```markdown
  # Seaworthy context
  - Product: subscription web app for small businesses
  - Markets: United States and the European Union
  - Users: adults only; no children's accounts
  - Data: contact details, billing handled by a payment provider, no health data
  - Deployment: managed container platform, managed Postgres with daily backups
  ```

- `--evidence FILE ...`: evidence Seaworthy cannot produce itself. Place the files under `.seaworthy/evidence/` in your project. Seaworthy cites them as user-provided. Examples:
  - Dependency scanner output for the audited revision (for example from your ecosystem's audit tool or an OSV-based scanner). This can verify `SUPPLY-KNOWN-VULNS`.
  - CI test results for the audited revision. These can verify `TEST-EXECUTION`.
  - A record of a successful backup restore test. This can verify `REL-BACKUP-RESTORE`.
  - Accessibility test reports, and penetration test summaries.
- `--domains a,b`: limit the audit to some domains. The others are reported as not assessed, so the ship decision fails safe.
- `--untrusted --out DIR`: for code you do not own or trust. See [hardened-mode.md](hardened-mode.md).

A typical first audit ends in **BLOCKED — INSUFFICIENT EVIDENCE**, because some release-critical controls, such as dependency vulnerability status and tested restores, cannot be established from source code alone. That is intended. The report lists exactly which evidence would change the decision. You can then import that evidence, or explicitly accept the risk with `/seaworthy:track`.

## `/seaworthy:legal-packet`

```
/seaworthy:legal-packet [SW-0003,SW-0007 | all] [--project NAME] [--for NAME] [--a4]
```

Builds **SEAWORTHY — ATTORNEY REVIEW PACKET** (PDF, plus a Markdown copy) from the latest audit's findings that warrant legal or business review. Several findings are combined into one packet. Each finding section includes the evidence, why review is recommended, specific questions for counsel, relevant decisions, and space for counsel's notes and decisions. The packet carries a disclaimer and is not legal advice.

## `/seaworthy:track`

```
/seaworthy:track status
/seaworthy:track SW-0003 legal review requested
/seaworthy:track SW-0003 decision received: counsel advised ...
/seaworthy:track accept SW-0004
/seaworthy:track close SW-0001
```

Records decisions only you can make.
- **Accepting a risk** needs the risk, the reason, the owner, the date, the scope, compensating controls, and optionally a review date. Seaworthy asks for anything missing and never fills it in.
- **Counsel decisions** are recorded as your report of what counsel said. Seaworthy never writes its own legal conclusions.
- **Closing** is possible only for findings that a re-audit verified as fixed, or that were not reproduced.

## `/seaworthy:remediate`

```
/seaworthy:remediate SW-0001,SW-0002
```

For your own projects only. Seaworthy:
1. Proposes a fix and a regression test for each finding.
2. Waits for your approval.
3. Implements the fixes.
4. Runs your tests, if you agree.
5. Records the remediation.
6. Re-audits the affected domains.

A fix counts as verified only when the re-audit confirms it. Run a full `/seaworthy:audit` before making a release decision.

## Continuous integration

The audit itself needs Claude. Teams typically run it before a release and commit the ledger. To gate a pipeline on a finished audit:

```bash
python3 path/to/plugins/seaworthy/scripts/seaworthy.py gate .seaworthy/runs/<run>/audit.final.json --exit-code
```

Exit codes:

| Code | Decision |
|---|---|
| 0 | READY FOR RELEASE |
| 2 | READY WITH ACCEPTED RISKS (use `--allow-accepted-risks` to return 0) |
| 3 | NOT READY — REMEDIATION REQUIRED |
| 4 | BLOCKED — INSUFFICIENT EVIDENCE |
| 5 | BLOCKED — CRITICAL RISK |

To run an audit headlessly, use `claude -p "/seaworthy:audit"` with `--plugin-dir`, `--permission-mode dontAsk`, and a settings file that allows Seaworthy's script and writes to `.seaworthy/`. See [hardened-mode.md](hardened-mode.md) for a complete example.
