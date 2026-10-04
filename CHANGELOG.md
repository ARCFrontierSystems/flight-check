# Changelog

All notable changes are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased] — 0.1.0

First development version. Not yet blind-tested or released.

- Renamed the project from Seaworthy (originally Project Guardian) to **Fairtide**: plugin id `fairtide`, commands `/fairtide:*`, script `fairtide.py`, working directory `.fairtide/`, finding IDs `FT-0001`, and the CI secret `FAIRTIDE_LEAK_DENYLIST`. Nothing had been released under the old names.

- `/fairtide:audit`: evidence-first audit across 19 domains, with an inventory agent, nine read-only domain agents, a verifier, mechanical evidence checking, stable finding IDs, regression tracking, and a deterministic ship gate. Writes a 33-section `report.md` and machine-readable `audit.final.json`.
- `/fairtide:legal-packet`: Legal Review Assistant and "FAIRTIDE — ATTORNEY REVIEW PACKET" (PDF and Markdown).
- `/fairtide:track`: accepted risks, legal review lifecycle, counsel decisions reported by the user, and closures.
- `/fairtide:remediate`: authorized fixes with regression tests and re-audit verification (own projects only).
- `fairtide.py`: standard-library-only tooling for validation, finalization, rendering, the gate (with CI exit codes), the ledger, and packets.
