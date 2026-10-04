"""The finding ledger: stable IDs, lifecycle, regression detection, accepted risks, legal review tracking.

Rules enforced here:
  * A finding that was VERIFIED or CLOSED and is observed again becomes REGRESSION.
  * A finding that is no longer observed is never silently dropped: it becomes
    NOT_REPRODUCED (needs confirmation) or, if a remediation was verified, VERIFIED.
  * Accepted risks and counsel decisions are recorded only with source "user".
  * An expired or incomplete acceptance does not count as accepted.
"""

import datetime
import os

from . import catalog, constants, fingerprint, minischema
from .jsonio import dump_json, load_json


class LedgerError(Exception):
    pass


ACCEPTANCE_FIELDS = ("risk", "reason", "owner", "date", "scope", "compensating_controls")


def new_ledger(project_name=None):
    led = {
        "schema_version": constants.SCHEMA_VERSION,
        "next_id": 1,
        "entries": {},
        "control_acceptances": {},
        "runs": [],
    }
    if project_name:
        led["project"] = {"name": project_name}
    return led


def load(path, create=True, project_name=None):
    if not os.path.exists(path):
        if create:
            return new_ledger(project_name)
        raise LedgerError("ledger %s does not exist" % path)
    led = load_json(path)
    errors = minischema.validate_def(led, catalog.schema(), "ledger")
    if errors:
        raise LedgerError("ledger %s is invalid: %s" % (path, "; ".join(errors[:10])))
    return led


def save(path, led):
    errors = minischema.validate_def(led, catalog.schema(), "ledger")
    if errors:
        raise LedgerError("refusing to write an invalid ledger: %s" % "; ".join(errors[:10]))
    dump_json(path, led)


def _event(at, status, source, note=None, run_id=None):
    ev = {"at": at, "status": status, "source": source}
    if note:
        ev["note"] = note
    if run_id:
        ev["run_id"] = run_id
    return ev


def _parse_date(text):
    return datetime.date(*[int(x) for x in text.split("-")])


def acceptance_problems(acc, today):
    """Return reasons an acceptance does not count (empty list means it is valid)."""
    if not acc:
        return ["no acceptance recorded"]
    problems = []
    for field in ACCEPTANCE_FIELDS:
        if not str(acc.get(field) or "").strip():
            problems.append("missing %s" % field)
    if acc.get("source") != "user":
        problems.append("acceptance was not recorded from a user decision")
    try:
        if acc.get("date") and _parse_date(acc["date"]) > today:
            problems.append("acceptance date is in the future")
        if acc.get("review_date") and _parse_date(acc["review_date"]) < today:
            problems.append("review date %s has passed" % acc["review_date"])
    except (ValueError, TypeError):
        problems.append("invalid date")
    return problems


def find_entry(led, finding_id):
    for fp, entry in led["entries"].items():
        if entry["id"] == finding_id:
            return fp, entry
    raise LedgerError("no finding %s in the ledger" % finding_id)


def merge(led, audit, now_iso, today):
    """Assign stable IDs and statuses to audit findings and update the ledger in place.

    Returns a dict describing lifecycle changes for the report.
    """
    run_id = audit["run"]["run_id"]
    if any(r["run_id"] == run_id for r in led["runs"]):
        raise LedgerError("run %s was already merged into this ledger" % run_id)
    entries = led["entries"]
    alt_index = {}
    key_index = {}
    for fp, e in entries.items():
        for alt in e.get("alt_fingerprints") or []:
            alt_index[alt] = fp
        key_index.setdefault(e.get("match_key", ""), []).append(fp)

    findings = sorted(audit["findings"], key=lambda f: (f["domain"], f["rule"], fingerprint.primary_path(f), f["local_id"]))
    fps = fingerprint.assign(findings)
    matched = set()
    changes = {"new": [], "regressions": [], "reopened": [], "expired_acceptances": []}

    for f, fp in zip(findings, fps):
        key = fingerprint.match_key(f)
        entry_fp = fp if fp in entries else alt_index.get(fp)
        if entry_fp in matched:
            entry_fp = None
        if entry_fp is None:
            candidates = [c for c in key_index.get(key, []) if c not in matched]
            if len(candidates) == 1:
                entry_fp = candidates[0]
                if fp != entry_fp:
                    entries[entry_fp].setdefault("alt_fingerprints", []).append(fp)
        if entry_fp is None:
            entry_fp = fp
            entries[fp] = {
                "id": "FT-%04d" % led["next_id"],
                "fingerprint": fp,
                "match_key": key,
                "rule": f["rule"],
                "domain": f["domain"],
                "title": f["title"],
                "severity": f["severity"],
                "status": "OPEN",
                "first_seen_run": run_id,
                "last_seen_run": run_id,
                "history": [_event(now_iso, "OPEN", "fairtide", "first observed", run_id)],
            }
            led["next_id"] += 1
            changes["new"].append(entries[fp]["id"])
        else:
            e = entries[entry_fp]
            prev = e["status"]
            new = prev
            note = None
            if prev in constants.RESOLVED_STATUSES:
                new, note = "REGRESSION", "previously %s; observed again" % prev
                changes["regressions"].append(e["id"])
            elif prev == "REMEDIATED":
                new, note = "OPEN", "still observed after the recorded remediation"
                changes["reopened"].append(e["id"])
            elif prev == "NOT_REPRODUCED":
                new, note = "OPEN", "observed again after not being reproduced"
                changes["reopened"].append(e["id"])
            elif prev == "ACCEPTED_RISK":
                problems = acceptance_problems(e.get("accepted_risk"), today)
                if problems:
                    new, note = "OPEN", "acceptance no longer valid: %s" % "; ".join(problems)
                    changes["expired_acceptances"].append(e["id"])
            if new != prev:
                e["status"] = new
                e["history"].append(_event(now_iso, new, "fairtide", note, run_id))
            e["last_seen_run"] = run_id
            e["title"] = f["title"]
            e["severity"] = f["severity"]
            e["match_key"] = key
        matched.add(entry_fp)
        e = entries[entry_fp]
        legal = f.get("legal")
        if legal:
            if "legal" not in e:
                e["legal"] = {"classification": legal["classification"], "status": "OPEN",
                              "history": [_event(now_iso, "OPEN", "fairtide", "classified %s" % legal["classification"], run_id)]}
            elif e["legal"]["classification"] != legal["classification"]:
                e["legal"]["history"].append(_event(now_iso, e["legal"]["status"], "fairtide",
                                                    "classification changed from %s to %s" % (e["legal"]["classification"], legal["classification"]), run_id))
                e["legal"]["classification"] = legal["classification"]
        f["id"] = e["id"]
        f["fingerprint"] = entry_fp
        f["status"] = e["status"]
        f["first_seen_run"] = e["first_seen_run"]
        f["legal_status"] = e["legal"]["status"] if "legal" in e else None
        if e["status"] == "ACCEPTED_RISK":
            f["accepted_risk"] = e["accepted_risk"]

    checks = {c["finding_id"]: c for c in audit.get("remediation_checks", [])}
    coverage = {c["domain"]: c["status"] for c in audit["coverage"]}
    lifecycle = {"not_reproduced": [], "remediated_pending": [], "verified_this_run": [],
                 "accepted_not_observed": [], "not_assessed_open": [], "inconsistent_remediation": []}
    for fp, e in entries.items():
        if fp in matched:
            continue
        check = checks.get(e["id"])
        status = e["status"]
        if status in ("OPEN", "REGRESSION", "REMEDIATED", "NOT_REPRODUCED"):
            # An explicit remediation check is applied even when the finding's domain was not assessed
            # in this run: the verifier read the current code for exactly this finding.
            if status == "REMEDIATED" and check and check["result"] == "FIXED_VERIFIED":
                e["status"] = "VERIFIED"
                e["history"].append(_event(now_iso, "VERIFIED", "fairtide", "re-audit verified the fix: %s" % check["notes"], run_id))
                lifecycle["verified_this_run"].append(e["id"])
                continue
            if check and check["result"] == "NOT_FIXED":
                e["status"] = "OPEN"
                e["history"].append(_event(now_iso, "OPEN", "fairtide", "verifier reports the fix is not effective: %s" % check["notes"], run_id))
                lifecycle["inconsistent_remediation"].append(e["id"])
                continue
            if coverage.get(e["domain"]) != "ASSESSED":
                lifecycle["not_assessed_open"].append(e["id"])
                continue
            if status == "REMEDIATED":
                lifecycle["remediated_pending"].append(e["id"])
            elif status in ("OPEN", "REGRESSION"):
                e["status"] = "NOT_REPRODUCED"
                e["history"].append(_event(now_iso, "NOT_REPRODUCED", "fairtide", "not observed in an assessed domain; confirm resolution", run_id))
                lifecycle["not_reproduced"].append(e["id"])
            else:
                lifecycle["not_reproduced"].append(e["id"])
        elif status == "ACCEPTED_RISK":
            lifecycle["accepted_not_observed"].append(e["id"])
    for fid in checks:
        try:
            find_entry(led, fid)
        except LedgerError:
            lifecycle.setdefault("unknown_remediation_checks", []).append(fid)
    changes.update(lifecycle)
    changes["ledger_entries"] = {e["id"]: {"status": e["status"], "title": e["title"], "domain": e["domain"],
                                           "severity": e["severity"]} for e in entries.values()}
    return changes


def record_run(led, run_id, now_iso, gate, mode):
    led["runs"].append({"run_id": run_id, "at": now_iso, "gate": gate, "mode": mode})


def accept(led, ref, fields, now_iso, today):
    acc = {k: str(fields.get(k) or "").strip() for k in ACCEPTANCE_FIELDS}
    if fields.get("review_date"):
        acc["review_date"] = fields["review_date"]
    acc["recorded_at"] = now_iso
    acc["source"] = "user"
    problems = [p for p in acceptance_problems(acc, today) if not p.startswith("review date")]
    if problems:
        raise LedgerError("cannot record acceptance: %s" % "; ".join(problems))
    if ref.startswith("FT-"):
        _, entry = find_entry(led, ref)
        if entry["status"] in constants.RESOLVED_STATUSES:
            raise LedgerError("%s is %s; there is no open risk to accept" % (ref, entry["status"]))
        entry["accepted_risk"] = acc
        entry["status"] = "ACCEPTED_RISK"
        entry["history"].append(_event(now_iso, "ACCEPTED_RISK", "user", "accepted by %s: %s" % (acc["owner"], acc["reason"])))
    else:
        if ref not in catalog.control_by_id():
            raise LedgerError("unknown control %s" % ref)
        led["control_acceptances"][ref] = acc


def revoke_acceptance(led, ref, note, now_iso):
    if ref.startswith("FT-"):
        _, entry = find_entry(led, ref)
        if entry["status"] != "ACCEPTED_RISK":
            raise LedgerError("%s is not an accepted risk" % ref)
        entry.pop("accepted_risk", None)
        entry["status"] = "OPEN"
        entry["history"].append(_event(now_iso, "OPEN", "user", "acceptance revoked: %s" % note))
    else:
        if ref not in led["control_acceptances"]:
            raise LedgerError("control %s has no acceptance" % ref)
        del led["control_acceptances"][ref]


def set_legal_status(led, finding_id, status, note, source, now_iso, reason=None):
    if status not in constants.LEGAL_STATUSES:
        raise LedgerError("unknown legal status %s (expected one of %s)" % (status, ", ".join(constants.LEGAL_STATUSES)))
    if source not in ("user", "fairtide"):
        raise LedgerError("source must be user or fairtide")
    _, entry = find_entry(led, finding_id)
    if "legal" not in entry:
        raise LedgerError("%s has no legal classification; only legal/business findings are tracked for counsel review" % finding_id)
    if status in constants.LEGAL_STATUSES_USER_ONLY:
        if source != "user":
            raise LedgerError("%s may only be recorded from information the user provides (source user)" % status)
        if not (note or "").strip():
            raise LedgerError("%s needs a note recording what the user reported (Fairtide never invents counsel's conclusions)" % status)
    current = entry["legal"]["status"]
    order = constants.LEGAL_STATUSES
    if order.index(status) < order.index(current) and not (reason or "").strip():
        raise LedgerError("moving legal status backwards from %s to %s needs a reason" % (current, status))
    if status in ("VERIFIED", "CLOSED") and current not in ("IMPLEMENTED", "VERIFIED", "DECISION_RECEIVED") and not (reason or "").strip():
        raise LedgerError("closing legal review from %s needs a reason (for example: counsel advised no change)" % current)
    text = note or ""
    if reason:
        text = (text + " | reason: " + reason).strip(" |")
    entry["legal"]["status"] = status
    entry["legal"]["history"].append(_event(now_iso, status, source, text or None))


def record_remediation(led, finding_id, note, files, now_iso, source="fairtide"):
    _, entry = find_entry(led, finding_id)
    if entry["status"] not in ("OPEN", "REGRESSION", "NOT_REPRODUCED"):
        raise LedgerError("%s is %s; only OPEN, REGRESSION, or NOT_REPRODUCED findings can be marked remediated" % (finding_id, entry["status"]))
    entry.setdefault("remediations", []).append({"at": now_iso, "note": note, "files": list(files or []), "source": source})
    entry["status"] = "REMEDIATED"
    entry["history"].append(_event(now_iso, "REMEDIATED", source, "remediation recorded; pending verification by re-audit: %s" % note))


def close(led, finding_id, note, now_iso):
    _, entry = find_entry(led, finding_id)
    if entry["status"] not in ("VERIFIED", "NOT_REPRODUCED"):
        raise LedgerError("%s is %s; only VERIFIED or NOT_REPRODUCED findings can be closed (use accept-risk for open risks)" % (finding_id, entry["status"]))
    if not (note or "").strip():
        raise LedgerError("closing needs a note recording the user's confirmation")
    entry["status"] = "CLOSED"
    entry["history"].append(_event(now_iso, "CLOSED", "user", note))
