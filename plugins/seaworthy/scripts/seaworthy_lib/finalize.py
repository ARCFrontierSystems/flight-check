"""finalize: validate -> assemble -> check evidence -> ledger merge -> gate -> audit.final.json."""

import datetime
import os

from . import VERSION, assemble, catalog, constants, evidence, gate, ledger, minischema, validate
from .jsonio import dump_json, load_json


def _now(now_iso=None):
    if now_iso:
        dt = datetime.datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    else:
        dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dt.date()


def _index_evidence(final):
    index = []
    n = 0

    def add(owner, items):
        nonlocal n
        for ev in items or []:
            n += 1
            ev["ref"] = "E-%04d" % n
            if ev.get("kind") == "absence":
                loc = "absence: " + (ev.get("searched") or "")[:120]
            elif ev.get("kind") == "user-provided":
                loc = "user-provided: " + ev.get("path", "")
            else:
                loc = "%s:%s-%s" % (ev.get("path"), ev.get("start_line"), ev.get("end_line"))
            index.append({"ref": ev["ref"], "owner": owner, "location": loc, "kind": ev.get("kind"), "check": ev.get("check", "NOT_CHECKED")})

    for f in final["findings"]:
        add(f["id"], f["evidence"])
    for c in final["controls"]:
        add("control " + c["id"], c.get("evidence"))
    for p in final["positive_controls"]:
        add("positive " + p["id"], p["evidence"])
    return index


def finalize(run_dir, root, ledger_path=None, now_iso=None):
    """Returns a result dict. On validation failure nothing is written and ok is False."""
    errors, warnings = validate.validate_run_dir(run_dir)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}
    final_path = os.path.join(run_dir, "audit.final.json")
    if os.path.exists(final_path):
        return {"ok": False, "errors": ["%s already exists; this run was finalized. Use 'render' to regenerate the report." % final_path], "warnings": warnings}

    audit, more = assemble.assemble(run_dir)
    warnings.extend(more)
    now, today = _now(now_iso)
    cache = evidence.FileCache()

    for f in audit["findings"] + audit["rejected_findings"]:
        f["evidence_check"] = evidence.annotate(f["evidence"], root, cache)
    kept_positive, unconfirmed_positive = [], []
    for p in audit["positive_controls"]:
        p["evidence_check"] = evidence.annotate(p["evidence"], root, cache)
        (unconfirmed_positive if p["evidence_check"] == "FAILED" else kept_positive).append(p)
    audit["positive_controls"] = kept_positive
    for c in audit["controls"]:
        if c.get("evidence"):
            c["evidence_check"] = evidence.annotate(c["evidence"], root, cache)
            if c["state"] == "VERIFIED" and c["evidence_check"] == "FAILED":
                c["state"] = "UNVERIFIED"
                c["missing_evidence"] = "The evidence cited to verify this control could not be confirmed in the files; it needs to be re-established."
                warnings.append("control %s downgraded to UNVERIFIED: cited evidence did not match the files" % c["id"])
    for item in audit["inventory"].get("items", []):
        if item.get("evidence"):
            evidence.annotate(item["evidence"], root, cache)

    led = ledger.load(ledger_path, create=True, project_name=audit["run"]["target"]["name"]) if ledger_path else ledger.new_ledger(audit["run"]["target"]["name"])
    lifecycle = ledger.merge(led, audit, now, today)

    local_to_id = {f["local_id"]: f["id"] for f in audit["findings"]}
    for f in audit["findings"]:
        f["effective_confidence"] = gate.effective_confidence(f)
        f["release_blocking_effective"] = gate.release_blocking_effective(f)
    for c in audit["controls"]:
        if c.get("related_findings"):
            c["related_findings"] = [local_to_id.get(r, r + " (rejected)") for r in c["related_findings"]]
    for i, p in enumerate(audit["positive_controls"], 1):
        p["id"] = "PC-%03d" % i
    audit["findings"].sort(key=lambda f: (constants.SEVERITY_RANK[f["severity"]], f["id"]))

    decision = gate.decide(audit, led, today, lifecycle)
    ledger.record_run(led, audit["run"]["run_id"], now, decision["decision"], audit["run"]["mode"])
    if ledger_path:
        ledger.save(ledger_path, led)

    final = {
        "schema_version": constants.SCHEMA_VERSION,
        "tool": {"name": "seaworthy", "version": VERSION},
        "finalized_at": now,
        "run": audit["run"],
        "inventory": audit["inventory"],
        "coverage": audit["coverage"],
        "controls": audit["controls"],
        "findings": audit["findings"],
        "rejected_findings": audit["rejected_findings"],
        "positive_controls": audit["positive_controls"],
        "unconfirmed_positive_controls": unconfirmed_positive,
        "unverified_areas": audit["unverified_areas"],
        "remediation_checks": audit["remediation_checks"],
        "regressions": lifecycle.get("regressions", []),
        "not_reproduced": lifecycle.get("not_reproduced", []),
        "remediated_pending": lifecycle.get("remediated_pending", []),
        "lifecycle": {k: v for k, v in lifecycle.items() if k != "ledger_entries"},
        "gate": decision,
        "notes": audit["notes"],
        "truncation": audit["truncation"],
        "validation": {"errors": [], "warnings": warnings},
        "ledger": os.path.abspath(ledger_path) if ledger_path else None,
    }
    final["evidence_index"] = _index_evidence(final)
    schema_errors = minischema.validate_def(final, catalog.schema(), "auditFinal")
    if schema_errors:
        raise RuntimeError("internal error: final document failed its schema: %s" % "; ".join(schema_errors[:5]))
    dump_json(final_path, final)
    return {
        "ok": True,
        "final": final_path,
        "gate": decision["decision"],
        "reasons": decision["reasons"],
        "counts": {s: sum(1 for f in final["findings"] if f["severity"] == s) for s in constants.SEVERITIES},
        "findings": [{"id": f["id"], "severity": f["severity"], "confidence": f["effective_confidence"], "status": f["status"],
                      "domain": f["domain"], "title": f["title"], "legal": (f.get("legal") or {}).get("classification")}
                     for f in final["findings"]],
        "controls_unverified": [c["id"] for c in final["controls"] if c["state"] == "UNVERIFIED"],
        "rejected": len(final["rejected_findings"]),
        "evidence_failures": [f["id"] for f in final["findings"] if f.get("evidence_check") == "FAILED"],
        "lifecycle": final["lifecycle"],
        "warnings": warnings,
    }


def load_final(run_dir):
    return load_json(os.path.join(run_dir, "audit.final.json"))
