---
name: legal-packet
description: Legal Review Assistant: builds the Attorney Review Packet PDF of findings and questions for counsel. Not legal advice.
argument-hint: "[FT-0001,FT-0002 | all] [--run RUN_DIR] [--project NAME] [--for NAME] [--a4] [--out DIR]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fairtide.py runs *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fairtide.py findings *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fairtide.py packet *)
disallowed-tools: WebFetch, WebSearch, Edit, NotebookEdit, Skill
---

# Fairtide Legal Review Assistant

This helps the user prepare for a conversation with qualified legal counsel. It does not replace that conversation and never answers the legal questions itself.

## Non-negotiable rules

1. **No legal advice and no legal conclusions.** Never state or imply that anything is compliant, legal, lawful, enforceable, adequate, or protected from liability. Never cite statutes, regulations, or cases. If jurisdiction-specific research would be needed, say it is UNVERIFIED.
2. **Separate evidence from questions.** Technical evidence comes from the finalized audit, and the packet copies it verbatim. You write only the summaries and optional per-finding context.
3. **Never invent counsel's conclusions or decisions.** Decisions are recorded only from what the user reports, through `/fairtide:track`.
4. **Do not modify the project.** Write only inside the Fairtide run directory.

Run Fairtide's script only as a single plain command that starts exactly with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fairtide.py`, as in the examples below. Never use shell variables, `cd`, `&&`, pipes, or redirection around it; only that exact form is pre-approved. Run no other shell commands.

## Steps

1. **Find the audit run.**
   - If `--run` was given, use it.
   - Otherwise run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fairtide.py runs --base <root>/.fairtide/runs`, where `<root>` is the current directory, and use `latest_finalized`.
   - For untrusted-tier audits, the user must give `--out DIR`; use `<DIR>/runs`.
   - If there is no finalized run, tell the user to run `/fairtide:audit` first, and stop.

   The ledger is `<root>/.fairtide/ledger.json`, or `<DIR>/ledger.json` for the untrusted tier.

2. **Select findings.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fairtide.py findings <run_dir> --legal --detail`.
   - With explicit IDs, use those. Each must carry a legal or business classification; if one does not, explain that the packet is only for findings that warrant legal or business review.
   - Otherwise (or with `all`), use every finding whose classification is not `INFORMATIONAL`.
   - If none qualify, tell the user that no findings currently warrant an Attorney Review Packet, and stop.
   - When several findings qualify, combine them into one packet.

3. **Write `<run_dir>/packet-request.json`:**

```json
{
  "project_identifier": "<--project value, else the audited project's directory name>",
  "prepared_for": "<--for value; omit if not given>",
  "language": "<BCP 47 tag of the summaries' language, for example de; omit for English>",
  "executive_summary": "...",
  "overall_summary": "...",
  "finding_ids": ["FT-0003", "FT-0007"],
  "per_finding": {
    "FT-0003": {
      "technical_context": "<optional: how the affected feature works, from the finding's explanation>",
      "relevant_decisions": ["<optional: decisions already made or pending, only as stated in the findings or by the user>"],
      "follow_up": ["<optional: concrete follow-up items>"]
    }
  }
}
```

   - **executive_summary** (at most ~200 words): what the packet covers, how many findings, their classifications and severities, and what the user hopes to learn from counsel. Factual and neutral.
   - **overall_summary** (at most ~250 words): the legal and business themes across the findings (for example deletion commitments, liability terms, subscription terms). State the open questions without answering them.
   - **No conclusions.** Neither summary may contain legal conclusions. When you refer to wording from the project's own documents, put it in double quotes.

4. **Build the packet.** Run:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fairtide.py packet <run_dir> --request <run_dir>/packet-request.json --out <run_dir>/attorney-review-packet.pdf --ledger <ledger>`
   - Add `--page-size a4` if the user asked for A4.
   - If `--out DIR` was given for the packet, write the PDF there instead.
   - If the command rejects the request (for example a prohibited conclusion or an unknown finding), correct the request and run it again.

5. **Tell the user:**
   - **Paths:** the PDF path, and the Markdown copy next to it (`.md`, which keeps any characters the PDF fonts cannot show).
   - **Contents:** the number of pages and the findings included.
   - **The disclaimer:** this is a document to organize questions for qualified counsel; it is not legal advice and establishes nothing about compliance.
   - **Next steps:**
     - When the user shares the packet with counsel: `/fairtide:track FT-0003 legal review requested`.
     - When counsel responds, the user reports the decision in their own words: `/fairtide:track FT-0003 decision received: <what counsel said>`. Fairtide then tracks the implementation work and verifies it in the next audit.
