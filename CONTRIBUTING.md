# Contributing to Fairtide

Thank you for helping. Fairtide is used to make release decisions, so correctness, honesty about uncertainty, and the security of the tool itself come first.

## Ground rules

- **Project-agnostic.** Fairtide must work on any project. Never add logic or wording specific to one application, company, or test fixture. Improve general procedures and issue classes instead.
- **Evidence first.** New checks must tell agents what evidence to collect and what counter-evidence to look for. Checklist items without an evidence method will not be accepted.
- **No legal conclusions.** Text that states or implies legal advice, compliance, certification, enforceability, or "secure" or "safe" verdicts will not be accepted. The validator enforces this for findings and summaries; reviewers enforce it for documentation.
- **Plugin safety:**
  - No hooks, MCP servers, `bin/` directory, or shell-injection blocks in skills or agents.
  - Agents keep `tools: Read, Grep, Glob`.
  - Scripts stay Python 3.9+ standard library only, with no network, subprocess, `eval`, or `exec`.
- **No secrets, real or realistic.** Build test strings that resemble credentials at runtime. Never commit invisible or bidirectional Unicode characters.
- **Private names.** Never add names of private or unrelated projects.

## Development

```bash
python3 -m unittest discover -s tests/unit -p 'test_*.py'
python3 tools/sync_agents.py          # after editing tools/agent-blocks/ or the controls catalog
python3 tools/lint_plugin.py
claude plugin validate --strict plugins/fairtide
claude --plugin-dir ./plugins/fairtide   # try it on a project
```

Where things live:
- **Shared agent rules:** `tools/agent-blocks/`. They are inserted into every agent by `tools/sync_agents.py`; do not edit the generated blocks by hand.
- **Controls and domains:** `plugins/fairtide/references/required-controls.json` and `domains.json`. Agents' control lists are generated from them.
- **Data formats:** `plugins/fairtide/schemas/fairtide.schema.json`. Keep `constants.py` consistent; the lint checks it.

## Changes that need extra care

- **Ship gate logic** (`gate.py`): explain the change in the pull request, add tests for every affected outcome, and update `docs/methodology.md`.
- **Release-critical controls:** adding one can block releases for many users. Justify why its absence is release-critical, and say what evidence can verify it.
- **Legal Review Assistant** wording, classifications, and the packet disclaimer.

## Blind testing

Do not add vulnerable applications to this repository. Test applications live in a separate repository; see [docs/blind-test-protocol.md](docs/blind-test-protocol.md). Changes motivated by blind-test results must be described in general terms.

## Pull requests

Describe what changed and why, how you tested it, and any effect on the ship gate or output format. Add a line to `CHANGELOG.md` under "Unreleased".
