# Changelog

All notable changes are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased] — 0.1.0

First development version. Not yet blind-tested or released.

- `/seaworthy:audit`: evidence-first audit across 19 domains, with an inventory agent, nine read-only domain agents, a verifier, mechanical evidence checking, stable finding IDs, regression tracking, and a deterministic ship gate. Writes a 33-section `report.md` and machine-readable `audit.final.json`.
- `/seaworthy:legal-packet`: Legal Review Assistant and "SEAWORTHY — ATTORNEY REVIEW PACKET" (PDF and Markdown).
- `/seaworthy:track`: accepted risks, legal review lifecycle, counsel decisions reported by the user, and closures.
- `/seaworthy:remediate`: authorized fixes with regression tests and re-audit verification (own projects only).
- `seaworthy.py`: standard-library-only tooling for validation, finalization, rendering, the gate (with CI exit codes), the ledger, and packets.
