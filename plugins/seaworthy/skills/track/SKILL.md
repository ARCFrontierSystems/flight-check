---
name: track
description: Record your decisions on Seaworthy findings (accepted risks, legal review status, closures) and show status.
argument-hint: "[status | SW-0001 <what happened> | accept SW-0001 | revoke SW-0001 | close SW-0001] [--out DIR]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seaworthy.py *)
disallowed-tools: WebFetch, WebSearch, Edit, NotebookEdit, Skill
---

# Seaworthy tracking

Records decisions **only the user can make**. Seaworthy never accepts a risk, never records a counsel decision, and never closes a finding on its own initiative.

Run Seaworthy's script only as a single plain command that starts exactly with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seaworthy.py`, as in the examples below. Never use shell variables, `cd`, `&&`, pipes, or redirection around it; only that exact form is pre-approved. Run no other shell commands. Do not modify project files.

**Ledger:** `<root>/.seaworthy/ledger.json`, where `<root>` is the current directory. For untrusted-tier audits, use `<DIR>/ledger.json` from `--out DIR`. If the ledger does not exist, tell the user to run `/seaworthy:audit` first.

Request: `$ARGUMENTS`

## Show status

For `status`, or no arguments, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seaworthy.py ledger show --ledger <ledger>`. Summarize findings by status, open legal reviews, accepted risks (with review dates), and the last few runs with their gates. For one ID, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seaworthy.py ledger show --ledger <ledger> --id SW-0001`.

## Legal review status

The lifecycle is `OPEN` → `LEGAL_REVIEW_REQUESTED` → `COUNSEL_REVIEWED` → `DECISION_RECEIVED` → `IMPLEMENTATION_REQUIRED` → `IMPLEMENTED` → `VERIFIED` → `CLOSED`.

Map what the user tells you to a status:
- "sent to counsel" or "shared the packet" → `LEGAL_REVIEW_REQUESTED`
- "counsel looked at it" → `COUNSEL_REVIEWED`
- "counsel said / decided ..." → `DECISION_RECEIVED`
- "we need to change X" → `IMPLEMENTATION_REQUIRED`
- "we changed it" → `IMPLEMENTED`

`VERIFIED` is set only after a Seaworthy re-audit confirms the change; tell the user to run `/seaworthy:audit`.

Run:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seaworthy.py ledger legal --ledger <ledger> --id SW-0001 --status <STATUS> --source user --note "<the user's own words>"`

- For `COUNSEL_REVIEWED` and `DECISION_RECEIVED`, the note must record what the user reported, attributed to them (for example `User reports counsel advised: ...`). Never paraphrase it into a stronger or different conclusion, and never invent one. If the user has not said what counsel decided, ask.
- Moving backwards needs `--reason`.
- After a decision that requires changes, offer to record `IMPLEMENTATION_REQUIRED`. Point out that `/seaworthy:remediate` can help with technical changes in the user's own project.

## Accept a risk

Acceptance requires an explicit human decision with all of these, supplied by the user:
- **risk**: what is being accepted.
- **reason**: why it is acceptable.
- **owner**: the person or role accountable.
- **date**: YYYY-MM-DD.
- **scope**: what the acceptance covers.
- **compensating controls**: what reduces the risk; "none" must be stated explicitly.
- **review date** (optional): when to revisit.

If any required item is missing, ask for it; never fill it in yourself. The reference can be a finding ID (`SW-0001`) or an UNVERIFIED control ID (for example `SUPPLY-KNOWN-VULNS`).

Before recording, show the user the exact values and ask them to confirm. Then run:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seaworthy.py ledger accept --ledger <ledger> --ref <ref> --risk "..." --reason "..." --owner "..." --date YYYY-MM-DD --scope "..." --compensating-controls "..." [--review-date YYYY-MM-DD]`

Explain how accepted risks affect the gate:
- They stay visible in every report.
- The best possible gate becomes "READY WITH ACCEPTED RISKS".
- An acceptance stops counting when its review date passes.
- A critical risk should rarely, if ever, be accepted. Say so if the user tries.

To revoke: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seaworthy.py ledger revoke --ledger <ledger> --ref <ref> --note "<user's reason>"`.

## Close a finding

Only findings that are `VERIFIED` (fixed and confirmed by a re-audit) or `NOT_REPRODUCED` can be closed, and only on the user's confirmation. Run:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/seaworthy.py ledger close --ledger <ledger> --id SW-0001 --note "<user's confirmation>"`

If the finding is still open, explain that it must be fixed and verified, or explicitly accepted as a risk.

## Always

- Report exactly what was recorded.
- If the script refuses an action, relay its reason and do not work around it.
