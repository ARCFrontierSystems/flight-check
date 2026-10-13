---
name: track
description: Record your decisions in Flight Check — accepted risks, legal review status, closures, finding triage, requirements, decisions, risks, and corrections to what Flight Check observed.
argument-hint: "[status | FC-0001 <what happened> | accept FC-0001 | revoke FC-0001 | close FC-0001 | <a requirement, decision, risk, or correction>] [--out DIR]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py ledger show *)
disallowed-tools: WebFetch, WebSearch, Edit, NotebookEdit, Skill
---

# Flight Check tracking

Records decisions **only the user can make**. Flight Check never accepts a risk, never records a counsel decision, and never closes a finding on its own initiative.

Only `ledger show` is pre-approved. Every command that records a decision (`ledger accept`, `revoke`, `legal`, `close`, `mark`, and the `req`, `decision`, `risk`, `legal`, `profile`, and `applicability` commands below) raises a Claude Code permission prompt, so the user approves each one explicitly. Never ask the user to pre-allow those commands in their settings, and never try another way to record a decision when a prompt is declined.

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

## Triage a finding

When the user says a finding is being looked at, has a fix planned, is a false positive, or was replaced by another finding:
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py ledger mark --root <root> --id <FC-xxxx> --status UNDER_REVIEW|REMEDIATION_PLANNED|FALSE_POSITIVE|SUPERSEDED|OPEN --reason "<the user's own words>" [--superseded-by FC-yyyy]`

A finding marked false positive or superseded stays visible; if an audit still reports it, the best decision is READY WITH ACCEPTED RISKS, and a CRITICAL one still blocks. Say so before recording it.

## Requirements, decisions, and risks

Record only what the user states; ask for any required field they have not given, and never fill one in yourself.
- **Requirement:** `req add --root <root> --title "..." --description "..." --category <category> --criticality release-critical|high|normal [--link control:<CONTROL-ID>]`. A requirement becomes VERIFIED only when an audit verifies its linked controls; never set it yourself. Suggest linking a control so an audit can verify it.
- **Decision:** `decision add --root <root> --title "..." --context "..." --decision "..." [--rationale "..."] [--assertion "a statement the code should satisfy" ...] [--review-trigger <path, pattern, or domain> ...]`. Assertions are checked against the code in every audit; review triggers flag the decision for review when matching files change. To replace one: `decision update --root <root> --id DEC-xxxx --status SUPERSEDED --superseded-by DEC-yyyy --note "..."`.
- **Risk:** `risk add --root <root> --title "..." --description "..." --domain <domain> --severity <SEVERITY>`, or `risk add --root <root> --from-finding FC-xxxx`. Accepting a register risk (`risk accept`) needs the same complete, user-supplied record as accepting a finding, and never lowers its severity; an accepted CRITICAL risk still blocks.
- Changes: `<req|decision|risk> update --root <root> --id <ID> --status <STATUS> --note "<the user's own words>"`. The script refuses transitions only evidence may make; relay its reason.

Prefix each with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py`.

## Corrections to what Flight Check observed

- A wrong observation: `profile correct --root <root> --id PF-xxxx --assertion not_used|excluded|replace|scope --reason "..." [--value "..."]`. The observation and its evidence stay; if later evidence contradicts the correction, Flight Check flags it.
- A fact the code cannot show: `profile declare --root <root> --aspect <aspect> --value "..." --reason "..."`.
- A domain that does or does not apply: `applicability override --root <root> --domain <domain> --classification applicable|not_applicable --reason "..."` (or `--clear`). When the evidence contradicts an override, the evidence is used and the conflict is reported.

Prefix each with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py`. For questions about the project rather than changes, suggest `/flight-check:status`.

## Always

- **Plain English first.** Say what was recorded and what it means for the user in everyday words; put IDs and status names in parentheses after the plain sentence.
- **Never leave the user stuck.** End with the next step, and say what still counts against a release.
- Report exactly what was recorded.
- If the script refuses an action, relay its reason and do not work around it.
