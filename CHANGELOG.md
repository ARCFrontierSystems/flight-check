# Changelog

All notable changes are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased] — 0.1.0

First development version. Not yet blind-tested or released.

- Changes from the first self-audit (see `docs/audits/2026-10-04-self-audit.md`):
  - **Decisions only you make now always prompt.** Each skill pre-approves only the script subcommands it needs. `ledger accept`, `revoke`, `legal`, and `close` are never pre-approved, and the lint enforces this. The headless runner and the hardened-mode settings allow only the audit subcommands and deny the ledger commands.
  - **Acceptances count only from the next run.** An acceptance recorded after a run started does not count for that run's ship decision.
  - **Accepted CRITICAL findings still block release.** The decision stays BLOCKED — CRITICAL RISK; the acceptance is recorded and shown.
  - **HTML-escaped quotes.** The evidence check accepts quotes whose `<`, `>`, or `&` were HTML-escaped in transit, restores the file's text, and notes the decoding. The audit skill saves agent output as received.
  - **Output contract.** UNVERIFIED controls may include `how_to_verify`, which the report shows. Agents report one issue per finding.
  - **Attorney Review Packet PDF.** It declares its document language (optional `language` in the packet request) and displays its title. It is still untagged; the Markdown copy is the accessible version.
  - **Leak gate.** The private-name denylist and the fixture canaries now cover every committable file, not only the plugin. The lint notes when no canaries are configured.
  - **Documentation.** Where audited content goes (your model provider), measured usage of an audit and how to limit it, PDF accessibility, and where self-audit reports are published.
- Renamed the project from Seaworthy (originally Project Guardian) to **Fairtide**: plugin id `fairtide`, commands `/fairtide:*`, script `fairtide.py`, working directory `.fairtide/`, finding IDs `FT-0001`, and the CI secret `FAIRTIDE_LEAK_DENYLIST`. Nothing had been released under the old names.

- `/fairtide:audit`: evidence-first audit across 19 domains, with an inventory agent, nine read-only domain agents, a verifier, mechanical evidence checking, stable finding IDs, regression tracking, and a deterministic ship gate. Writes a 33-section `report.md` and machine-readable `audit.final.json`.
- `/fairtide:legal-packet`: Legal Review Assistant and "FAIRTIDE — ATTORNEY REVIEW PACKET" (PDF and Markdown).
- `/fairtide:track`: accepted risks, legal review lifecycle, counsel decisions reported by the user, and closures.
- `/fairtide:remediate`: authorized fixes with regression tests and re-audit verification (own projects only).
- `fairtide.py`: standard-library-only tooling for validation, finalization, rendering, the gate (with CI exit codes), the ledger, and packets.
