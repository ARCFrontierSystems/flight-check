# Seaworthy

**Evidence-first ship-readiness audits for any software project, as a Claude Code plugin.**

Seaworthy asks one question:

> *Based on the evidence available in this project, what could prevent this software from being safely and responsibly released?*

It examines security, privacy, legal/business risk, compliance readiness, accessibility, reliability, and production readiness. It reports what it found with file-and-line evidence, what it verified as working, and what it could not verify. It never confuses "we did not find a problem" with "there is no problem".

> **Status: pre-release (0.1.0).** Seaworthy is under active development. Its blind evaluation against a synthetic test application has not been published yet. Treat results as an aid to human judgment, not a substitute for it.

## What Seaworthy does

- **Audits 19 domains:**
  - application security, authentication, authorization, data protection, privacy (including retention and deletion)
  - payments/subscriptions, third-party services, dependencies/supply chain, IP/assets
  - infrastructure/deployment, reliability/recovery, operational readiness, production readiness, testing
  - AI/LLM risks, accessibility
  - legal/business risk, compliance readiness, documentation consistency
- **Requires evidence.** Every finding cites files and line ranges with quotes, and Seaworthy mechanically checks each quote against the file. Before reporting a problem, Seaworthy searches for evidence that it is already mitigated. A separate verifier re-checks every finding and can confirm, downgrade, merge, or reject it.
- **Reports what works, too.** Positive controls are listed with evidence, so the report is not a list of complaints.
- **Fails safe.** A control Seaworthy cannot establish is marked UNVERIFIED, never "passed".
- **Computes a deterministic ship gate.** The decision is computed by code from the findings and controls, not written by the model: READY FOR RELEASE, READY WITH ACCEPTED RISKS, NOT READY — REMEDIATION REQUIRED, BLOCKED — INSUFFICIENT EVIDENCE, or BLOCKED — CRITICAL RISK.
- **Includes a Legal Review Assistant.** Findings that warrant legal or business review are classified, and each comes with specific questions for qualified counsel. Seaworthy can produce **SEAWORTHY — ATTORNEY REVIEW PACKET**, a printable PDF to take into a consultation. It does not give legal advice.
- **Tracks findings over time.** Finding IDs are stable across audits. Regressions are flagged. Accepted risks, counsel decisions, and closures are recorded only from your explicit input.
- **Offers a remediation mode** (for your own projects). It fixes the findings you authorize, adds regression tests where practical, and re-audits to verify the fixes instead of assuming they worked.
- **Produces machine-readable output** for CI: `audit.final.json` and gate exit codes.

## What Seaworthy is not

- **Not legal advice, a compliance certification, an audit opinion, or a penetration test.** It never states that a project is compliant, certified, secure, or legal.
- **Not exhaustive.** It reads code and configuration statically. It does not run your application or tests, scan live infrastructure, or query vulnerability databases. Those areas stay UNVERIFIED unless you import evidence.
- **Not infallible.** Language models can miss issues and can be wrong. Seaworthy's checks reduce, but do not eliminate, false positives and false negatives. See [docs/limitations.md](docs/limitations.md).

## Requirements

- Claude Code with plugin support. Developed and tested with Claude Code 2.1.289; it relies on agent settings introduced in 2.1.271.
- Python 3.9 or newer available as `python3`. Seaworthy's bundled script uses only the Python standard library and makes no network connections.

## Install

From Claude Code's command line:

```bash
claude plugin marketplace add ARCFrontierSystems/project-guardian
claude plugin install seaworthy@arc-frontier-systems
```

Or inside a Claude Code session: `/plugin marketplace add ARCFrontierSystems/project-guardian`, then `/plugin install seaworthy@arc-frontier-systems`. To try Seaworthy without installing it, clone this repository and start Claude Code with `claude --plugin-dir ./plugins/seaworthy`. See [docs/installation.md](docs/installation.md) for team setup, updates, and removal.

## Use

| Command | What it does |
|---|---|
| `/seaworthy:audit` | Audits the current project and writes the report, the machine-readable results, and the ship decision. |
| `/seaworthy:legal-packet` | Builds the Attorney Review Packet PDF from findings that warrant legal or business review. |
| `/seaworthy:track` | Shows status, and records your decisions: accepted risks, legal review status, counsel decisions you report, closures. |
| `/seaworthy:remediate SW-0001,SW-0002` | Fixes findings you authorize in your own project, then re-audits to verify. |

Useful audit options:
- `--context FILE`: a short description of your markets, audience, regulated data, and business model.
- `--evidence FILE ...`: imported evidence, such as scanner output, CI test results, or restore-test records.
- `--untrusted --out DIR`: for code you do not own or trust; read [Auditing untrusted code](docs/hardened-mode.md) first.

Seaworthy runs its bundled script (`python3 <plugin>/scripts/seaworthy.py`) to validate results and compute the gate. Approve it when asked; choosing "always allow" avoids repeated prompts. See [docs/usage.md](docs/usage.md).

## Output

Each audit writes a run directory:
- `.seaworthy/runs/<run>/report.md`: the 33-section human-readable report.
- `.seaworthy/runs/<run>/audit.final.json`: machine-readable findings, controls, coverage, and the gate. See [docs/output-format.md](docs/output-format.md).
- `.seaworthy/runs/<run>/attorney-review-packet.pdf`: written when you run `/seaworthy:legal-packet`.

The project also gets a ledger, `.seaworthy/ledger.json`, which holds finding history. Commit it if you want regression tracking across your team. Run directories are gitignored automatically because they quote your project's files.

## How it works

1. **Inventory.** A read-only agent maps the project and decides which domains apply.
2. **Domain audits.** Nine read-only domain agents (tools limited to Read, Grep, Glob) audit in parallel and return structured findings, controls, and coverage.
3. **Validation.** `seaworthy.py validate` enforces the output contract: evidence on every finding, counter-evidence searches, no legal conclusions, no vague findings.
4. **Verification.** A verifier agent re-reads every cited line and searches independently for mitigations.
5. **Finalize.** `seaworthy.py finalize` checks every quote against the files, assigns stable IDs, detects regressions, and computes the ship gate.
6. **Report.** The 33-section report and the machine-readable results are written.

Details: [docs/methodology.md](docs/methodology.md).

## Privacy and data handling

- **No telemetry, no network.** Seaworthy adds no telemetry, and its script makes no network connections.
- **Model provider.** The content Seaworthy's agents read is processed by your Claude Code session's model provider under your existing Claude Code configuration, like any other Claude Code task.
- **Local files.** Seaworthy masks likely secrets in its output files, but your conversation history follows Claude Code's own retention settings.

## Disclaimer

Seaworthy organizes evidence and questions for qualified professionals. It is not legal advice and does not establish compliance with any law, regulation, standard, or contract. Results reflect only the evidence available in the audited scope. You remain responsible for your release decisions.

## Project

- License: [Apache-2.0](LICENSE) (see [NOTICE](NOTICE))
- Security issues in Seaworthy itself: [SECURITY.md](SECURITY.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Changes: [CHANGELOG.md](CHANGELOG.md)
- Testing approach: [docs/testing.md](docs/testing.md)
