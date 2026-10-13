---
name: watch
description: Turn Flight Check's watch mode on or off for this project. When it is on, Flight Check checks each file Claude edits for common mistakes (free), suggests a quick check of your changes with its cost when they pile up, and reminds you of open items when you come back.
argument-hint: "[on | off | status]"
disable-model-invocation: true
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py watch *)
disallowed-tools: WebFetch, WebSearch, Edit, Write, NotebookEdit, Skill, Agent
---

# Flight Check watch mode

Request: `$ARGUMENTS`

Run Flight Check's script only as a single plain command that starts exactly with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py`. Never use shell variables, `cd`, `&&`, pipes, or redirection around it. Run no other commands.

1. Take the action from the request: `on`, `off`, or `status` (no argument means `status`). Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/flight_check.py watch <action> --root <root>`, where `<root>` is the current directory.
2. Tell the user, in plain English, what the output says, and explain what watch mode does:
   - **After each file Claude edits,** free instant checks of that file: a password or API key written into the code, HTTPS certificate checks turned off, debug mode left on, and a `.env` file that git does not ignore. If one turns up, Claude tells you right away and offers to fix it, asking once first.
   - **When changes pile up in sensitive code** (logins, permissions, payments, personal data), it suggests a quick check of just those changes and says what it would cost. It never starts a paid check by itself: you type `/flight-check:audit --changed` when you are ready.
   - **When you start a session,** it reminds you of open items and of anything you asked to be reminded about.
   - It reads only files on this machine and sends nothing anywhere. Its settings and reminders stay in `.flight-check/` and are not committed.
3. When turning it on for a project that Flight Check has not checked yet, suggest `/flight-check:audit` as a starting point.

Watch mode's free checks catch a few common mistakes as they happen. They are not an audit; only `/flight-check:audit` gives a release decision.
