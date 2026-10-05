# Changelog

All notable changes are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased] — 0.1.0

First development version. Not yet blind-tested or released.

- Renamed the project from Fairtide to **Flight Check**: plugin id `flight-check`, commands `/flight-check:*`, script `flight_check.py`, working directory `.flight-check/`, finding IDs `FC-0001` (existing ledger IDs `FT-` became `FC-` with the same numbers), packet title "FLIGHT CHECK — ATTORNEY REVIEW PACKET", and the CI secret `FLIGHT_CHECK_LEAK_DENYLIST`. Nothing had been released under the old names.
- Changes from the Phase 5 blind tests:
  - **Audit root paths.** Agents build every path from the audit root exactly as given, and the audit skill reuses `run_dir` and the root verbatim. Agents had dropped the last directory of the root, Claude Code refused those reads, and one run stalled before finalize.
  - **Legal review flag.** A finding with a legal or business classification always gets the matching human-review flag when the run is assembled. A missing flag is now a warning instead of a validation retry; the flag can only add review.
  - **Blind-test tooling.** The runner streams every event to a log, reports denied tool calls, and audits a copy placed at the top of its temporary directory. `owasp_subset.py` builds a balanced OWASP Benchmark sample for the security domains outside this repository, and `stability.py` summarizes repeated runs.
- Changes from the first self-audit (see `docs/audits/2026-10-04-self-audit.md`):
  - **Decisions only you make now always prompt.** Each skill pre-approves only the script subcommands it needs. `ledger accept`, `revoke`, `legal`, and `close` are never pre-approved, and the lint enforces this. The headless runner and the hardened-mode settings allow only the audit subcommands and deny the ledger commands.
  - **Acceptances count only from the next run.** An acceptance recorded after a run started does not count for that run's ship decision.
  - **Accepted CRITICAL findings still block release.** The decision stays BLOCKED — CRITICAL RISK; the acceptance is recorded and shown.
  - **HTML-escaped quotes.** The evidence check accepts quotes whose `<`, `>`, or `&` were HTML-escaped in transit, restores the file's text, and notes the decoding. The audit skill saves agent output as received.
  - **Output contract.** UNVERIFIED controls may include `how_to_verify`, which the report shows. Agents report one issue per finding.
  - **Attorney Review Packet PDF.** It declares its document language (optional `language` in the packet request) and displays its title. It is still untagged; the Markdown copy is the accessible version.
  - **Leak gate.** The private-name denylist and the fixture canaries now cover every committable file, not only the plugin. The lint notes when no canaries are configured.
  - **Documentation.** Where audited content goes (your model provider), measured usage of an audit and how to limit it, PDF accessibility, and where self-audit reports are published.
- Changes from the Phase 4 re-audit:
  - **Secret masking now covers JSON outputs.** Likely secrets are masked in all agent text when a run is assembled, so `audit.final.json` and the ledger never hold them, not only the rendered report and packet (previously the README claimed more than the code did).
  - **Full audits verify pending fixes.** Every full audit asks the verifier to check findings recorded as REMEDIATED; before, only a targeted re-audit could move them to VERIFIED.
  - **Authorization control wording.** `AUTHZ-SERVER-SIDE` now applies to projects that serve multiple users or principals. Tool permission settings are not authorization in this sense, which kept the control's applicability, and with it the ship decision, from changing between runs.
  - **Agent retries.** A retry after a validation error launches a new agent rather than trying to message the earlier one.
  - **Remediation checks always apply.** A verifier's explicit check of a recorded fix now updates the ledger even when the finding's domain was not assessed in that run; before, it was silently ignored.
  - **Agent contracts.** The inventory agent must quote a line for every file it cites, and the verifier's contract lists the allowed evidence kinds; both had caused validation retries.
- Renamed the project from Seaworthy (originally Project Guardian) to **Fairtide**: plugin id `fairtide`, commands `/fairtide:*`, script `fairtide.py`, working directory `.fairtide/`, finding IDs `FT-0001`, and the CI secret `FAIRTIDE_LEAK_DENYLIST`. Nothing had been released under the old names.

- `/flight-check:audit`: evidence-first audit across 19 domains, with an inventory agent, nine read-only domain agents, a verifier, mechanical evidence checking, stable finding IDs, regression tracking, and a deterministic ship gate. Writes a 33-section `report.md` and machine-readable `audit.final.json`.
- `/flight-check:legal-packet`: Legal Review Assistant and "FLIGHT CHECK — ATTORNEY REVIEW PACKET" (PDF and Markdown).
- `/flight-check:track`: accepted risks, legal review lifecycle, counsel decisions reported by the user, and closures.
- `/flight-check:remediate`: authorized fixes with regression tests and re-audit verification (own projects only).
- `flight_check.py`: standard-library-only tooling for validation, finalization, rendering, the gate (with CI exit codes), the ledger, and packets.
