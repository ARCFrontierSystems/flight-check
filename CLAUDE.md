# Flight Check

## Project name

This project is named **Flight Check** (two words). Plugin id: `flight-check`. Commands are `/flight-check:audit`, `/flight-check:legal-packet`, `/flight-check:track`, and `/flight-check:remediate`; finding IDs look like `FC-0001`.

The user has asked for this to be remembered permanently: **every earlier name means Flight Check.**
- "Project Guardian" is the original name. Whenever the user says "Project Guardian", they mean Flight Check.
- "DevGuard" was an interim name, dropped because of the OWASP DevGuard project. It also means Flight Check.
- "Seaworthy" was the next name, dropped because similar products already use it. It also means Flight Check.
- "Fairtide" was the name before Flight Check; finding IDs were `FT-` then. It also means Flight Check.
- The user chose Flight Check knowing that other software already uses the one-word name "FlightCheck": a print-preflight product with a registered trademark, and an AI-agent readiness check from a large software vendor. Counsel review of the name is advised before public launch.
- The GitHub repository was renamed from `project-guardian` to `flight-check` (`ARCFrontierSystems/flight-check`); old URLs redirect. It is the same project.

## Saved for later

The user asked to keep these until they ask for them. When they do, point them to the file, or provide its contents. Do not act on either one before then.

- Blind-test protocol: `docs/blind-test-protocol.md`, for Phase 5, which runs Flight Check blind against the separate private testbed repository.
- GitHub repository settings to switch on: `docs/maintainers/github-settings.md`.

## Repository layout

- `plugins/flight-check/` is the shipped Claude Code plugin (skills, agents, scripts, schemas, references). Everything in it reaches users.
- `.claude-plugin/marketplace.json` publishes the plugin from `./plugins/flight-check`.
- `tests/`, `tools/`, `docs/` are development-only and never ship.

## Development rules

- Run before every commit: `python3 -m unittest discover -s tests/unit -p 'test_*.py'`, `python3 tools/sync_agents.py --check`, `python3 tools/lint_plugin.py`, and `claude plugin validate --strict plugins/flight-check`.
- Shared agent instructions live in `tools/agent-blocks/`; edit them there and run `python3 tools/sync_agents.py`. Control lists in agents are generated from `plugins/flight-check/references/required-controls.json`.
- Plugin scripts are Python 3.9+ standard library only: no network, no subprocess, no eval/exec. The lint enforces this.
- Never put blind-test fixtures, ground truth, expected findings, or fixture-specific identifiers into `plugins/`. Flight Check must discover issues from evidence. Improve general procedures, never special-case a test app.
- Never commit strings that look like real credentials (build test samples at runtime) or invisible/bidirectional Unicode characters (in code, write them as escape sequences, never as the characters themselves; `tools/lint_plugin.py` rejects them).
- Never name private or unrelated projects anywhere in this repository. The leak gate reads a private denylist from `FLIGHT_CHECK_LEAK_DENYLIST` (a local file that is never committed).
- Flight Check never gives legal advice or claims compliance/certification; keep that true in every file, including docs.
