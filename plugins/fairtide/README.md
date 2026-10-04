# Fairtide (Claude Code plugin)

Evidence-first ship-readiness audits for any software project. Full documentation: https://github.com/ARCFrontierSystems/project-guardian

## Commands

- `/fairtide:audit [path] [--context FILE] [--evidence FILE ...] [--untrusted --out DIR]`: audit the project and produce the report, the machine-readable results, and a deterministic ship decision.
- `/fairtide:legal-packet [FT-0001,... | all]`: build "FAIRTIDE — ATTORNEY REVIEW PACKET" (PDF) for findings that warrant legal or business review.
- `/fairtide:track [status | FT-0001 ...]`: record your decisions (accepted risks, legal review status, counsel decisions you report, closures) and show status.
- `/fairtide:remediate FT-0001[,...]`: fix authorized findings in your own project, then re-audit to verify.

## Requirements

Python 3.9+ as `python3` (standard library only). Fairtide asks to run its bundled script `scripts/fairtide.py`; approving it with "always allow" avoids repeated prompts.

## Disclaimer

Fairtide is not legal advice and does not certify compliance or security. It reports evidence found in the audited scope. "No finding" does not mean "no problem". Licensed under Apache-2.0.
