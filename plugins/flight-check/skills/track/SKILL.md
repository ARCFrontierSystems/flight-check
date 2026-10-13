---
name: track
description: Record your decisions on Flight Check findings (accepted risks, legal review status, closures) and show status.
argument-hint: "[status | FC-0001 <what happened> | accept FC-0001 | revoke FC-0001 | close FC-0001] [--out DIR]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py ledger show *)
disallowed-tools: WebFetch, WebSearch, Edit, NotebookEdit, Skill
---

# Flight Check tracking

Records decisions **only the user can make**. Flight Check never accepts a risk, never records a counsel decision, and never closes a finding on its own initiative.

Only `ledger show` is pre-approved. Every command that records a decision (`ledger accept`, `revoke`, `legal`, `close`) raises a Claude Code permission prompt, so the user approves each one explicitly. Never ask the user to pre-allow those commands in their settings, and never try another way to record a decision when a prompt is declined.

Run Flight Check's script only as a single plain command that starts exactly with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py`, as in the examples below. Never use shell variables, `cd`, `&&`, pipes, or redirection around it; only that exact form is pre-approved. Run no other shell commands. Do not modify project files.

**Ledger:** `<root>/.flight-check/ledger.json`, where `<root>` is the current directory. For untrusted-tier audits, use `<DIR>/ledger.json` from `--out DIR`. If the ledger does not exist, tell the user to run `/flight-check:audit` first.

Request: `$ARGUMENTS`

## Show status

For `status`, or no arguments, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py ledger show --ledger <ledger>`. Summarize findings by status, open legal reviews, accepted risks (with review dates), and the last few runs with their gates. For one ID, run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py ledger show --ledger <ledger> --id <ID>`.

## Legal review status

Each legal or business question is a legal review item (`LR-0001`). Findings with a legal or business classification get one automatically; the user can refer to it by the finding ID or the item ID. Statuses: `NEEDS_REVIEW` → `AWAITING_COUNSEL` (or `AWAITING_BUSINESS_DECISION`) → `REVIEWED` → `ACTION_REQUIRED` → `RESOLVED`, or `NOT_APPLICABLE`.

Map what the user tells you to a status:
- "sent to counsel" or "shared the packet" → `AWAITING_COUNSEL`
- "this is ours to decide" → `AWAITING_BUSINESS_DECISION`
- "counsel looked at it", "counsel said / decided ..." → `REVIEWED`
- "we need to change X" → `ACTION_REQUIRED`
- "it's settled" or "we changed it" → `RESOLVED`; when it involved a code or policy change, suggest `/flight-check:audit` to check it
- "this doesn't apply to us" → `NOT_APPLICABLE`

Run:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py legal update --root <root> --id <FC-xxxx or LR-xxxx> --status <STATUS> --note "<the user's own words>"`

- For `REVIEWED`, `RESOLVED`, and `NOT_APPLICABLE`, the note must record what the user reported, attributed to them (for example `User reports counsel advised: ...`). Never paraphrase it into a stronger or different conclusion, and never invent one. If the user has not said what counsel decided, ask.
- Flight Check never sets these statuses itself; only the user's report does.
- To record a legal question that no finding raised, use `legal add --root <root> --title ... --category ... --description ... --trigger "<what raised it>" [--question "..."]`.
- After a decision that requires changes, offer to record `ACTION_REQUIRED`. Point out that `/flight-check:remediate` can help with technical changes in the user's own project.
- The 1.0 command `ledger legal` with the old status names still works; it records the matching new status.

## Accept a risk

Acceptance requires an explicit human decision with all of these, supplied by the user:
- **risk**: what is being accepted.
- **reason**: why it is acceptable.
- **owner**: the person or role accountable.
- **date**: YYYY-MM-DD.
- **scope**: what the acceptance covers.
- **compensating controls**: what reduces the risk; "none" must be stated explicitly.
- **review date** (optional): when to revisit.

If any required item is missing, ask for it; never fill it in yourself. The reference can be a finding ID (`FC-0001`) or an UNVERIFIED control ID (for example `SUPPLY-KNOWN-VULNS`).

Before recording, show the user the exact values and ask them to confirm. Then run:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py ledger accept --ledger <ledger> --ref <ref> --risk "..." --reason "..." --owner "..." --date YYYY-MM-DD --scope "..." --compensating-controls "..." [--review-date YYYY-MM-DD]`

Explain how accepted risks affect the gate:
- They stay visible in every report.
- The best possible gate becomes "READY WITH ACCEPTED RISKS".
- An acceptance stops counting when its review date passes.
- **An accepted CRITICAL finding still blocks release.** The acceptance is recorded and shown, but the decision stays BLOCKED — CRITICAL RISK. Say so before recording one.
- An acceptance counts from the next audit run onwards; it never changes a run that had already started when it was recorded.

To revoke: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py ledger revoke --ledger <ledger> --ref <ref> --note "<user's reason>"`.

## Close a finding

Only findings that are `VERIFIED` (fixed and confirmed by a re-audit) or `NOT_REPRODUCED` can be closed, and only on the user's confirmation. Run:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py ledger close --ledger <ledger> --id <ID> --note "<user's confirmation>"`

If the finding is still open, explain that it must be fixed and verified, or explicitly accepted as a risk.

## Always

- Report exactly what was recorded.
- If the script refuses an action, relay its reason and do not work around it.
