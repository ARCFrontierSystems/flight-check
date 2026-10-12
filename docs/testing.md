# Testing

Flight Check is tested at four levels.

## 1. Deterministic tooling: unit tests (every commit)

```bash
python3 -m unittest discover -s tests/unit -p 'test_*.py'
```

These cover:
- **Schema validation:** cross-checked against the reference `jsonschema` implementation when it is installed.
- **Validation rules:** evidence, counter-evidence, legal-overclaim and vague-finding guards, and control state rules.
- **Text safety:** secret redaction, invisible-character stripping, and Markdown neutralization.
- **Mechanical evidence checking:** quote matching, path traversal, and symlinks escaping the root.
- **Fingerprints and the ledger lifecycle:** stable IDs, regressions, not-reproduced findings, remediation verification, acceptance validity and expiry, and legal-status rules.
- **Every ship-gate outcome.**
- **End-to-end finalize, render, and packet:** including PDF structure checks and text extraction (`pdftotext`, if installed), with verifier rejections, duplicates, and fabricated evidence.

CI runs them on Python 3.9 and 3.12.

## 2. Repository lint (every commit)

```bash
python3 tools/sync_agents.py --check
python3 tools/lint_plugin.py
claude plugin validate --strict plugins/flight-check
```

These enforce:
- Manual-only skills and read-only agents.
- No hooks, MCP servers, or shell injection in the plugin.
- No network or process modules, `eval`, or `exec` in its scripts.
- No invisible or bidirectional Unicode anywhere.
- Consistency between catalogs, the schema, and the code.
- Generated agent blocks in sync with their sources.
- The leak gate (`FLIGHT_CHECK_LEAK_DENYLIST` for private names, and `tools/leak-hashes.txt` for fixture identifiers).

## 3. Smoke tests (during development)

`tools/blindtest/run_audit.py` runs a real headless audit, with `--permission-mode dontAsk` and only the run directory writable, against a disposable copy of a small fixture (for example `tests/fixtures/mini-target`). This checks that the whole pipeline (agents, validation, verifier, finalize, report) works end to end. It is not a measure of detection quality.

## 4. Blind tests (before releases)

Detection quality is measured against synthetic applications in a separate repository, with hidden ground truth and held-out fixtures. See [blind-test-protocol.md](blind-test-protocol.md). Results are published with each release, including the weaknesses they reveal.

## Self-audit

Before each release, Flight Check audits its own repository with `/flight-check:audit`. A summary and review of each self-audit is committed under `docs/audits/`, and the ledger at `.flight-check/ledger.json` records finding IDs and decisions over time. Full 33-section reports are not committed to this repository: Flight Check audits this repository, and the next self-audit would read an earlier report as project content and could cite it instead of the code. Attach full reports to the release instead.

Headless runs (`claude -p`) can print a first result while the agents are still working, then a final one after the report is rendered. Read results from the run directory (`audit.final.json`, `report.md`), not from the first message printed.
