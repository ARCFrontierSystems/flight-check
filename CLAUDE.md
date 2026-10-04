# Fairtide

## Project name

This project is named **Fairtide**. Plugin id: `fairtide`. Commands are `/fairtide:audit`, `/fairtide:legal-packet`, `/fairtide:track`, and `/fairtide:remediate`; finding IDs look like `FT-0001`.

The user has asked for this to be remembered permanently: **every earlier name means Fairtide.**
- "Project Guardian" is the original name. Whenever the user says "Project Guardian", they mean Fairtide.
- "DevGuard" was an interim name, dropped because of the OWASP DevGuard project. It also means Fairtide.
- "Seaworthy" was the next name, dropped because similar products already use it. It also means Fairtide.
- The GitHub repository is still named `project-guardian` until the user renames it; that is the same project.

## Saved for later

The user asked to keep these until they ask for them. When they do, point them to the file, or provide its contents. Do not act on either one before then.

- Blind-test protocol: `docs/blind-test-protocol.md`, for Phase 5, which runs Fairtide blind against the separate private testbed repository.
- GitHub repository settings to switch on: `docs/maintainers/github-settings.md`.

## Repository layout

- `plugins/fairtide/` is the shipped Claude Code plugin (skills, agents, scripts, schemas, references). Everything in it reaches users.
- `.claude-plugin/marketplace.json` publishes the plugin from `./plugins/fairtide`.
- `tests/`, `tools/`, `docs/` are development-only and never ship.

## Development rules

- Run before every commit: `python3 -m unittest discover -s tests/unit -p 'test_*.py'`, `python3 tools/sync_agents.py --check`, `python3 tools/lint_plugin.py`, and `claude plugin validate --strict plugins/fairtide`.
- Shared agent instructions live in `tools/agent-blocks/`; edit them there and run `python3 tools/sync_agents.py`. Control lists in agents are generated from `plugins/fairtide/references/required-controls.json`.
- Plugin scripts are Python 3.9+ standard library only: no network, no subprocess, no eval/exec. The lint enforces this.
- Never put blind-test fixtures, ground truth, expected findings, or fixture-specific identifiers into `plugins/`. Fairtide must discover issues from evidence. Improve general procedures, never special-case a test app.
- Never commit strings that look like real credentials (build test samples at runtime) or invisible/bidirectional Unicode characters (in code, write them as escape sequences, never as the characters themselves; `tools/lint_plugin.py` rejects them).
- Never name private or unrelated projects anywhere in this repository. The leak gate reads a private denylist from `FAIRTIDE_LEAK_DENYLIST` (a local file that is never committed).
- Fairtide never gives legal advice or claims compliance/certification; keep that true in every file, including docs.
