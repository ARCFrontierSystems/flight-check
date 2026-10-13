# Flight Check

**Evidence-first ship-readiness audits for any software project, as a Claude Code plugin.**

Flight Check asks one question:

> *Based on the evidence available in this project, what could prevent this software from being safely and responsibly released?*

It examines security, privacy, legal/business risk, compliance readiness, accessibility, reliability, and production readiness. It reports what it found with file-and-line evidence, what it verified as working, and what it could not verify. It never confuses "we did not find a problem" with "there is no problem".

> **Status: 1.0.0, not yet released.** Flight Check's blind evaluations are published: [Phase 5](docs/audits/2026-10-06-phase-5-blind-test.md), the [Phase 7 final report](docs/audits/2026-10-12-phase-7-final-report.md), the [release-candidate evaluation](docs/audits/2026-10-12-v1-release-evaluation.md), and the [sequence evaluation of audits over time](docs/audits/2026-10-13-v1.5-sequence-evaluation.md) on held-out test applications. Used the 1.0.0-rc.1 release candidate? See [docs/upgrading-from-rc.md](docs/upgrading-from-rc.md). Treat results as an aid to human judgment, not a substitute for it.

## What Flight Check does

- **Speaks plain English.** It tells you what is wrong, why it matters, and what to do next. Claude fixes what it can after asking you once, and explains exactly what only you can do. You are never stuck: fix it now, have it remind you later, accept the risk for this release, or mark it as not a real problem.
- **Works alongside you (watch mode, optional).** Turn it on with `/flight-check:watch on`. After each file Claude edits, free instant checks catch common mistakes such as a password written into the code. When changes pile up in sensitive code, it suggests a quick check of them and says what it costs. When you come back, it reminds you of open items.
- **Audits 19 domains:**
  - application security, authentication, authorization, data protection, privacy (including retention and deletion)
  - payments/subscriptions, third-party services, dependencies/supply chain, IP/assets
  - infrastructure/deployment, reliability/recovery, operational readiness, production readiness, testing
  - AI/LLM risks, accessibility
  - legal/business risk, compliance readiness, documentation consistency
- **Requires evidence.** Every finding cites files and line ranges with quotes, and Flight Check mechanically checks each quote against the file. Before reporting a problem, Flight Check searches for evidence that it is already mitigated. A separate verifier re-checks every finding and can confirm, downgrade, merge, or reject it.
- **Reports what works, too.** Positive controls are listed with evidence, so the report is not a list of complaints.
- **Fails safe.** A control Flight Check cannot establish is marked UNVERIFIED, never "passed".
- **Computes a deterministic ship gate.** The decision is computed by code from the findings and controls, not written by the model: READY FOR RELEASE, READY WITH ACCEPTED RISKS, NOT READY — REMEDIATION REQUIRED, BLOCKED — INSUFFICIENT EVIDENCE, or BLOCKED — CRITICAL RISK.
- **Includes a Legal Review Assistant.** Findings that warrant legal or business review are classified, and each comes with specific questions for qualified counsel. Flight Check can produce **FLIGHT CHECK — ATTORNEY REVIEW PACKET**, a printable PDF to take into a consultation. It does not give legal advice.
- **Tracks findings over time.** Finding IDs are stable across audits. Regressions are flagged. Accepted risks, counsel decisions, and closures are recorded only from your explicit input.
- **Offers a remediation mode** (for your own projects). It fixes the findings you authorize, adds regression tests where practical, and re-audits to verify the fixes instead of assuming they worked.
- **Produces machine-readable output** for CI: `audit.final.json` and gate exit codes.
- **Remembers the project.** Project state in `.flight-check/` keeps the project's profile and which domains apply, your requirements, decisions, and risks, a legal review queue, and a hash-chained history of every change. Your corrections and decisions are recorded only from your explicit input, and evidence still decides what is verified.
- **Audits only what changed.** `/flight-check:audit --changed` reassesses the areas a change can affect and carries the rest forward, labelled as not reassessed. When a change cannot be traced, it reassesses everything.
- **Release check for CI.** `release-check` passes only when the latest ship decision is ready and still describes the files being released, so a release or deploy workflow can depend on it. It reads only the committed records, so it needs no Claude and costs nothing to run. See "Release check" in [docs/usage.md](docs/usage.md).
- **Tells you how to get to READY.** When the decision is not READY, `/flight-check:status` lists each blocker with its next step: the vulnerability-scanner command for your project's ecosystems, the control to re-check with `/flight-check:audit --recheck`, or the finding to fix or the risk to accept.
- **Findings in GitHub code scanning.** `sarif` exports the findings that count against release as SARIF, so they appear in the repository's Security tab and on pull requests. It reads only the committed records.
- **Light on usage.** After the first audit, an audit re-checks only what changed. `--quick` gives a cheap spot check of the security-critical areas for everyday use. Each audit states its estimated cost first and asks before going over your spending limit ($5 by default). Agents use the models Flight Check's accuracy was measured with, never more expensive ones unless you choose `--models thorough`.
- **Answers questions about the project.** `/flight-check:status` answers questions such as "what does my project still need?" from the records, and `report` gives each register and view in Markdown or JSON.

## What Flight Check is not

- **Not legal advice, a compliance certification, an audit opinion, or a penetration test.** It never states that a project is compliant, certified, secure, or legal.
- **Not exhaustive.** It reads code and configuration statically. It does not run your application or tests, scan live infrastructure, or query vulnerability databases. Those areas stay UNVERIFIED unless you import evidence.
- **Not infallible.** Language models can miss issues and can be wrong. Flight Check's checks reduce, but do not eliminate, false positives and false negatives. See [docs/limitations.md](docs/limitations.md).

## Requirements

- Claude Code with plugin support. Developed and tested with Claude Code 2.1.289; it relies on agent settings introduced in 2.1.271.
- Python 3.9 or newer available as `python3`. Flight Check's bundled script uses only the Python standard library and makes no network connections.

## Install

From Claude Code's command line:

```bash
claude plugin marketplace add ARCFrontierSystems/flight-check
claude plugin install flight-check@arc-frontier-systems
```

Or inside a Claude Code session: `/plugin marketplace add ARCFrontierSystems/flight-check`, then `/plugin install flight-check@arc-frontier-systems`. To try Flight Check without installing it, clone this repository and start Claude Code with `claude --plugin-dir ./plugins/flight-check`. See [docs/installation.md](docs/installation.md) for team setup, updates, and removal.

## Use

| Command | What it does |
|---|---|
| `/flight-check:audit` | Audits the current project and writes the report, the machine-readable results, and the ship decision. |
| `/flight-check:legal-packet` | Builds the Attorney Review Packet PDF from findings that warrant legal or business review. |
| `/flight-check:track` | Shows status, and records your decisions: accepted risks, legal review status, counsel decisions you report, closures. |
| `/flight-check:remediate FC-0001,FC-0002` | Fixes findings you authorize in your own project, then re-audits to verify. |
| `/flight-check:fix` | Fixes what Claude can fix from Flight Check's findings, after asking you once. |
| `/flight-check:watch on\|off` | Turns watch mode on or off for the current project. |
| `/flight-check:status [question]` | Answers questions about readiness, open risks, changes, requirements, decisions, and legal questions from Flight Check's records, without changing anything. |

Useful audit options:
- `--context FILE`: a short description of your markets, audience, regulated data, and business model.
- `--evidence FILE ...`: imported evidence, such as scanner output, CI test results, or restore-test records.
- `--passes 2`: runs each domain agent twice and combines the results, for a steadier ship decision at about twice the usage. Recommended before a release decision.
- `--untrusted --out DIR`: for code you do not own or trust; read [Auditing untrusted code](docs/hardened-mode.md) first.

Flight Check runs its bundled script (`python3 <plugin>/scripts/flight_check.py`) to validate results and compute the gate. Approve it when asked; choosing "always allow" avoids repeated prompts. See [docs/usage.md](docs/usage.md).

## Output

Each audit writes a run directory:
- `.flight-check/runs/<run>/report.md`: the 33-section human-readable report.
- `.flight-check/runs/<run>/audit.final.json`: machine-readable findings, controls, coverage, and the gate. See [docs/output-format.md](docs/output-format.md).
- `.flight-check/runs/<run>/attorney-review-packet.pdf`: written when you run `/flight-check:legal-packet`.

The project also gets a ledger, `.flight-check/ledger.json`, which holds finding history. Commit it if you want regression tracking across your team. Run directories are gitignored automatically because they quote your project's files.

## How it works

1. **Inventory.** A read-only agent maps the project and decides which domains apply.
2. **Domain audits.** Nine read-only domain agents (tools limited to Read, Grep, Glob) audit in parallel and return structured findings, controls, and coverage.
3. **Validation.** `flight_check.py validate` enforces the output contract: evidence on every finding, counter-evidence searches, no legal conclusions, no vague findings.
4. **Verification.** A verifier agent re-reads every cited line and searches independently for mitigations.
5. **Finalize.** `flight_check.py finalize` checks every quote against the files, assigns stable IDs, detects regressions, and computes the ship gate.
6. **Report.** The 33-section report and the machine-readable results are written.

Details: [docs/methodology.md](docs/methodology.md).

## Privacy and data handling

- **No telemetry, no network.** Flight Check adds no telemetry, and its script makes no network connections.
- **Watch mode's hooks.** The plugin registers three hooks (session start, after Claude edits a file, and when Claude finishes a reply). Each runs Flight Check's own script, which reads only local files and exits at once, doing nothing, unless you turned watch mode on for that project. Watch mode never starts a paid audit; its settings and reminders stay in `.flight-check/` and are not committed.
- **Model provider.** The content Flight Check's agents read is processed by your Claude Code session's model provider under your existing Claude Code configuration, like any other Claude Code task. That includes anything in the audited files: source code, configuration, fixtures, seed data, logs, and any personal, health, payment, or other regulated or confidential data they contain.
- **Before auditing sensitive material,** check that your organization permits sending it to that provider. To keep files out of an audit, audit a subdirectory (`/flight-check:audit path/to/subdir`) or a copy of the project without them. Flight Check masks likely secrets in what it writes, but the agents still read the original files.
- **Local files.** Flight Check masks likely secrets in everything it writes from agent results: `report.md`, `audit.final.json`, the ledger, and Attorney Review Packets. The raw agent replies saved in the run directory are kept as received for traceability; run directories are gitignored automatically. They can contain quoted project content. Flight Check never deletes them itself: they are kept until you delete them, so delete `.flight-check/runs/<run>/` directories you no longer need (the ledger keeps finding history without them). Masking is pattern-based, so it can miss unusual secret formats. Each run directory is created readable only by your user account, and the files Flight Check's script writes are owner-only. Your conversation history follows Claude Code's own retention settings. [docs/security-design.md](docs/security-design.md) maps each of these protections to its code and tests.

## Usage and cost

An audit runs an inventory agent, up to nine domain agents, and a verifier, each reading part of the project. Projects with more than 12 entry-point files add up to eight handler reviewers, and `--passes 2` or `3` runs each domain agent that many times. Usage grows with project size and with the number of applicable domains. Flight Check sets no token limit of its own; your Claude Code plan or provider settings govern spend.

For reference, Flight Check's self-audit of its own repository (about 600 KB of text, ten agent tasks plus one retry) took about six minutes. The agents used roughly 800,000 input tokens and 50,000 output tokens, and the coordinating session about 1.6 million input tokens; most input tokens in both were cache reads. In the 1.0 release evaluation, an audit of a test web application of about 90 files cost $9–11 per run with `--passes 2`, against about $3.50 for a single pass on an earlier test application. Treat these as an order of magnitude, not a quote. Prices depend on your plan and model.

To limit usage:
- Restrict the audit to the domains you need with `--domains`. The other domains are then reported as not assessed, so the ship gate stays blocked.
- Audit a subdirectory.
- For headless runs, set a spending cap with `claude -p ... --max-budget-usd <amount>`.
- Check your plan's usage page after the first audit.

## Disclaimer

Flight Check organizes evidence and questions for qualified professionals. It is not legal advice and does not establish compliance with any law, regulation, standard, or contract. Results reflect only the evidence available in the audited scope. You remain responsible for your release decisions.

Flight Check is free software provided under the [Apache License 2.0](LICENSE), including that license's disclaimer of warranty and limitation of liability. There are no separate terms of use.

## Project

- License: [Apache-2.0](LICENSE) (see [NOTICE](NOTICE))
- Security issues in Flight Check itself: [SECURITY.md](SECURITY.md)
- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Changes: [CHANGELOG.md](CHANGELOG.md)
- Testing approach: [docs/testing.md](docs/testing.md)
