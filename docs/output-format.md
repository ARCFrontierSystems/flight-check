# Output format

All formats are defined in `plugins/fairtide/schemas/fairtide.schema.json` (JSON Schema 2020-12). Validate a file with:

```bash
python3 plugins/fairtide/scripts/fairtide.py schema-check FILE --definition auditFinal
```

## Run directory (`.fairtide/runs/<run_id>/`)

| File | Written by | Purpose |
|---|---|---|
| `run.json` | audit skill | Run metadata: mode, trust tier, target, context, imported evidence |
| `inventory.json` | inventory agent | Project inventory and domain applicability |
| `part-<agent>.json` | domain agents | Coverage, controls, findings, positive controls, unverified areas |
| `verification.json` | verifier agent | Per-finding verdicts and remediation checks |
| `audit.final.json` | `fairtide.py finalize` | The machine-readable result (below) |
| `summary.json` | audit skill | Executive and legal summaries, remediation priority, candidates |
| `report.md` | `fairtide.py render` | Human-readable 33-section report |
| `attorney-review-packet.pdf` / `.md` | `fairtide.py packet` | Attorney Review Packet |

## `audit.final.json`

Top-level keys:
- **`schema_version`, `tool`, `finalized_at`, `run`, `inventory`.**
- **`coverage`:** one entry per domain: `ASSESSED`, `NOT_APPLICABLE`, or `NOT_ASSESSED`, with the rationale and the searches performed.
- **`controls`:** every catalog control with `state`, `release_critical`, evidence, and `missing_evidence` or `related_findings`.
- **`findings`:** current findings, sorted by severity. Each has:
  - `id` (stable, `FT-0001`), `fingerprint`, `rule`, `domain`, `title`.
  - `severity`, `confidence`, `effective_confidence`.
  - `status` (ledger status), `release_blocking_effective`.
  - `evidence[]`, each item with `check` (OK, QUOTE_MISMATCH, ...), and `evidence_check` (PASSED, FAILED, PARTIAL, NOT_APPLICABLE).
  - `counterevidence[]`, `affected_components[]`, `explanation`, `impact`, `remediation`.
  - `legal` (classification, questions, decisions) and `legal_status`.
  - `human_review`, and `verification` (the verifier's verdict and notes).
- **`rejected_findings`, `duplicate_findings`:** kept for transparency; they do not affect the gate.
- **`positive_controls`, `unconfirmed_positive_controls`, `unverified_areas`.**
- **`regressions`, `not_reproduced`, `remediated_pending`, `lifecycle`.**
- **`gate`:** `decision`, `reasons`, `critical_findings`, `blocking_findings`, `insufficient_evidence`, `accepted_findings`, `accepted_controls`, `scope_statement`, `exit_code`.
- **`evidence_index`:** every evidence item with a reference such as `E-0001`.
- **`validation`:** warnings recorded during finalization.

## Ledger (`.fairtide/ledger.json`)

- `entries`: keyed by fingerprint. Each entry has a stable `id`, `status`, `history[]`, optional `legal` (classification, status, history), `accepted_risk`, and `remediations[]`.
- `control_acceptances`: risks accepted for UNVERIFIED controls.
- `runs[]`: run IDs with their gate decisions.

History events record `source`: `fairtide` or `user`. Accepted risks and counsel decisions are always `user`.

## Gate exit codes

`fairtide.py gate <audit.final.json> --exit-code` returns 0 (READY FOR RELEASE), 2 (READY WITH ACCEPTED RISKS; 0 with `--allow-accepted-risks`), 3 (NOT READY — REMEDIATION REQUIRED), 4 (BLOCKED — INSUFFICIENT EVIDENCE), or 5 (BLOCKED — CRITICAL RISK).
