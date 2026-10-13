---
name: start
description: Getting started with Flight Check. Explains in plain English what it does, what it costs, and the first steps, and where this project stands. Use when the user asks how to use Flight Check, what it can do, how to get started, or what to type first.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py status *), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py settings *)
disallowed-tools: WebFetch, WebSearch, Edit, Write, NotebookEdit, Skill, Agent
---

# Getting started with Flight Check

Run Flight Check's script only as a single plain command that starts exactly with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py`. Never use shell variables, `cd`, `&&`, pipes, or redirection around it. Run no other commands, and change nothing.

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py status --root <root> --format json`, where `<root>` is the current directory. An error saying there is no project state means this project has not been checked yet.
2. Reply in plain English, short and friendly, in this order:
   - **What Flight Check is:** it checks whether a project is safe and responsible to release (security, privacy, payments, reliability, accessibility, and legal or business questions), shows the evidence for everything it reports, and tells you what to fix and who can fix it. It is not a certification or legal advice.
   - **Where this project stands:** not checked yet; or the latest result in plain words, with the number of open items Claude can fix and the number that need the user.
   - **The first steps,** as a short numbered list:
     1. Type `/flight-check:audit` to check the project (or re-check what changed). It states its cost before it starts: typically $2–5 for a first check of a medium-sized project, less afterwards, because only what changed is re-checked. It asks first if the cost is over the user's limit ($5 unless changed). A first check usually ends "not ready yet", because some proof cannot come from code alone; Flight Check then lists exactly what to do.
     2. Say "fix it" when Flight Check offers, and Claude fixes what it can after asking once.
     3. Ask questions anytime, such as "what does my project still need?"
     4. Optional: type `/flight-check:watch on` and Flight Check checks each file Claude edits for common mistakes, free, and reminds you of open items when you come back.
   - **Cheaper options,** in one line: `/flight-check:audit --quick` is a spot check of the security-critical areas for well under a dollar or two.
3. Do not start an audit yourself: only the user can, by typing the command, because it costs money.
