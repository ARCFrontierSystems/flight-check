# Output format

All formats are defined in `plugins/flight-check/schemas/flight-check.schema.json` (JSON Schema 2020-12). Validate a file with:

```bash
python3 plugins/flight-check/scripts/flight_check.py schema-check FILE --definition auditFinal
```

## Run directory (`.flight-check/runs/<run_id>/`)

| File | Written by | Purpose |
|---|---|---|
| `run.json` | audit skill | Run metadata: mode, trust tier, target, context, imported evidence |
| `inventory.json` | inventory agent | Project inventory and domain applicability |
| `part-<agent>.json` | domain agents | Coverage, controls, findings, positive controls, unverified areas |
| `verification.json` | verifier agent | Per-finding verdicts and remediation checks |
| `files.json` | `flight_check.py init-run` (1.5) | Hashes of the project's files when the run started; the next comparison baseline |
| `scope.json` | `flight_check.py init-run --changed` (1.5) | Domains to reassess and domains to carry forward |
| `audit.final.json` | `flight_check.py finalize` | The machine-readable result (below) |
| `summary.json` | audit skill | Executive and legal summaries, remediation priority, candidates |
| `report.md` | `flight_check.py render` | Human-readable 33-section report |
| `attorney-review-packet.pdf` / `.md` | `flight_check.py packet` | Attorney Review Packet |

## `audit.final.json`

Top-level keys:
- **`schema_version`, `tool`, `finalized_at`, `run`, `inventory`.**
- **`coverage`:** one entry per domain: `ASSESSED`, `NOT_APPLICABLE`, or `NOT_ASSESSED`, with the rationale and the searches performed. From 1.5, an audit of changes only (`--changed`) can also report `CARRIED_FORWARD` with `carried_forward` (`run_id`, `at`: the run that last assessed the domain). Agents can never report that status.
- **`controls`:** every catalog control with `state`, `release_critical`, evidence, and `missing_evidence` or `related_findings`. A control in a carried-forward domain has `carried_forward` (`run_id`, `at`, and `evidence`: `current`, `stale`, `missing`, or `unverified`) and `reported_by: ["carried-forward"]`.
- **`carried_forward_findings`** (1.5): findings from carried-forward domains that still count in the ship decision, each with `id`, `domain`, `rule`, `title`, `severity`, `status`, `confidence`, `effective_confidence`, `release_blocking_effective`, `last_seen_run`, and `evidence_state`.
- **`incremental`** (1.5, `--changed` runs only): `baseline_run_id`, `changed_files`, `reassessed_domains`, `carried_forward_domains`.
- **`findings`:** current findings, sorted by severity. Each has:
  - `id` (stable, `FC-0001`), `fingerprint`, `rule`, `domain`, `title`.
  - `severity`, `confidence`, `effective_confidence`.
  - `status` (ledger status), `release_blocking_effective`.
  - `evidence[]`, each item with `check` (OK, QUOTE_MISMATCH, ...), and `evidence_check` (PASSED, FAILED, PARTIAL, NOT_APPLICABLE).
  - `counterevidence[]`, `affected_components[]`, `explanation`, `impact`, `remediation`.
  - `legal` (classification, questions, decisions) and `legal_status`: from version 1.5, the status of the finding's legal review item (`NEEDS_REVIEW`, `AWAITING_BUSINESS_DECISION`, `AWAITING_COUNSEL`, `REVIEWED`, `ACTION_REQUIRED`, `RESOLVED`, `NOT_APPLICABLE`), or null when the finding has no item (no legal classification, or INFORMATIONAL). Version 1.0 used the older ledger statuses (`OPEN`, `LEGAL_REVIEW_REQUESTED`, ...).
  - `human_review`, and `verification` (the verifier's verdict and notes).
  - `decision_ids` (1.5, optional): recorded decisions (`DEC-`) the finding contradicts.
  - `recorded_id` (1.5, optional): the earlier finding the verifier judged to be the same problem; the finding keeps that ID.
- **`rejected_findings`, `duplicate_findings`:** kept for transparency; they do not affect the gate.
- **`positive_controls`, `unconfirmed_positive_controls`, `unverified_areas`.**
- **`regressions`, `not_reproduced`, `remediated_pending`, `lifecycle`.**
- **`gate`:** `decision`, `reasons`, `critical_findings`, `blocking_findings`, `insufficient_evidence`, `accepted_findings`, `accepted_controls`, `scope_statement`, `exit_code`. From 1.5 also `dismissed_findings` (marked false positive or superseded but still reported), `accepted_register_risks`, `carried_forward_domains`, and `carried_forward_findings`; `critical_findings`, `blocking_findings`, and `insufficient_evidence` can then also name requirements (`REQ-`), legal items (`LR-`), and risks (`RISK-`) from the project's records.
- **`lifecycle`** (1.5): also `possible_regressions`, `false_positive_reobserved`, `superseded_reobserved`, `carried_forward_open`, `recorded_matches` (findings the verifier identified as an earlier recorded finding), and `unknown_recorded_ids`. Positive controls carry stable IDs (`PC-0001`) across runs.
- **`evidence_index`:** every evidence item with a reference such as `E-0001`.
- **`validation`:** warnings recorded during finalization.

## Status and report views (1.5)

`status --format json` and `report --view <view> --format json` print (or, with `--out NAME`, write to `.flight-check/reports/NAME`) one object:
- `report_version` (`1.0`), `view`, `generated_at`, `project` (`name`, `project_id`, `revision`), `scope_note`.
- `data`, by view:
  - `status`: `project`, `profile` (`known`, `known_total`, `unknown_aspects`), `applicability` (domains by classification), `readiness` (`latest` stamp, `historical`, `note`), `changes` (`available`, and when available `changed_files`, `reassess_domains`, `carried_forward_domains`, `widened`, `stale_evidence` count; otherwise `reason`), `findings` (`open_by_severity`, `open_total`, `top`, `accepted`, `regressions`), `risks`, `requirements` (not verified), `decisions` (`needs_attention`, `triggered_by_changes`), `legal` (`open`, `message`), `next_steps`.
  - `profile`: `facts`, `applicability`.
  - `requirements`, `decisions`, `risks`, `legal`: `records` (summary rows with links), or `record` with `--id`; `legal` adds `message` when no item is open.
  - `findings`: `findings` (with first and last run), or `finding`, `history`, and `evidence_files` with `--id`.
  - `evidence`: `baseline`, `changed_files`, `stale_or_missing`. `changes`: the `changes` command's output. `readiness`: the `status` readiness fields plus `history` (last 20 stamps).

Markdown is the default format; it is for reading, and its layout may change between versions.

## Ledger (`.flight-check/ledger.json`)

- `entries`: keyed by fingerprint. Each entry has a stable `id`, `status`, `history[]`, optional `legal`, `accepted_risk`, and `remediations[]`. In ledger schema 1.0, `legal` holds the classification, status, and history; in schema 2.0 (Flight Check 1.5) it holds the `classification` and the `item` (`LR-` ID) whose status and history are in `registers.json`. From 1.5 (ledger schema 2.0) also `evidence_files`, and `confidence` and `release_blocking` as of the last observation, so an audit of changes only can count the finding without re-observing it.
- `control_acceptances`: risks accepted for UNVERIFIED controls.
- `runs[]`: run IDs with their gate decisions.

History events record `source`: `flight-check` or `user`. Accepted risks and counsel decisions are always `user`.

## Gate exit codes

`flight_check.py gate <audit.final.json> --exit-code` returns 0 (READY FOR RELEASE), 2 (READY WITH ACCEPTED RISKS; 0 with `--allow-accepted-risks`), 3 (NOT READY — REMEDIATION REQUIRED), 4 (BLOCKED — INSUFFICIENT EVIDENCE), or 5 (BLOCKED — CRITICAL RISK).
