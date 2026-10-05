# Flight Check (Claude Code plugin)

Evidence-first ship-readiness audits for any software project. Full documentation: https://github.com/ARCFrontierSystems/project-guardian

## Commands

- `/flight-check:audit [path] [--context FILE] [--evidence FILE ...] [--untrusted --out DIR]`: audit the project and produce the report, the machine-readable results, and a deterministic ship decision.
- `/flight-check:legal-packet [FC-0001,... | all]`: build "FLIGHT CHECK — ATTORNEY REVIEW PACKET" (PDF) for findings that warrant legal or business review.
- `/flight-check:track [status | FC-0001 ...]`: record your decisions (accepted risks, legal review status, counsel decisions you report, closures) and show status.
- `/flight-check:remediate FC-0001[,...]`: fix authorized findings in your own project, then re-audit to verify.

## Requirements

Python 3.9+ as `python3` (standard library only). Flight Check asks to run its bundled script `scripts/flight_check.py`; approving it with "always allow" avoids repeated prompts.

## Disclaimer

Flight Check is not legal advice and does not certify compliance or security. It reports evidence found in the audited scope. "No finding" does not mean "no problem". Licensed under Apache-2.0.
